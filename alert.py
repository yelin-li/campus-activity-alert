#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
campus-activity-alert · 校园第二课堂活动筛选与推送

从一个学校的第二课堂平台拉取活动列表，按「校区 + 学院/参与对象 + 报名未截止」
筛选出你能报的活动，生成报告并通过邮件推送。

只依赖 Python 标准库，不需要 pip install 任何东西。

用法：
    python alert.py --demo                     # 用内置样例跑一遍，看效果
    python alert.py --config schools/example-zhixun.json # 用某校配置跑
    python configure.py                        # 交互式向导，适配你自己的学校
    python alert.py --scan-token               # 只扫 token，看能不能取到
    python alert.py --discover                 # 探测活动接口路径
    python alert.py --offline data.json        # 离线筛选（调试字段用）
"""

import argparse
import datetime as dt
import glob
import json
import os
import pathlib
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent
SSL_CTX = ssl.create_default_context()

# ---------------------------------------------------------------- 平台预设
# verified=True 表示在本项目里真实跑通过；False 只是模板，字段需自行校准。
PLATFORMS = {
    "zhixun": {
        "verified": True,
        "note": "广州职迅 jobsys.cn，Vue SPA，域名常为 erke.<学校>.edu.cn",
        "api": {
            "activity_path": "/api/student/activity",
            "college_path": "/api/student/college",
            "page_param": "page",
            "items_path": "result.data",
            "last_page_path": "result.last_page",
            "auth_scheme": "Bearer",
        },
        "token": {
            "key_names": ["student-token"],
            "regex": r"[0-9]{3,12}\|[A-Za-z0-9]{10,80}",
        },
        "links": {
            "detail_url": "{base_url}/mobile/activity-detail?id={id}",
        },
        "fields": {
            "title": ["name", "title"],
            "place": ["address", "place", "location", "site"],
            "host": ["host", "organizer", "sponsor"],
            "time": ["started_at", "start_time", "start"],
            "deadline": ["apply_ended_at", "apply_end", "deadline"],
            "status": ["apply_status", "status"],
            "contact": ["contact", "contacts", "contact_name"],
        },
    },
    "qutuo": {
        "verified": False,
        "note": "趣拓校园（合肥申祥）。仅提供模板，接口路径需抓包确认",
        "api": {
            "activity_path": "/api/activity/list",
            "college_path": "",
            "page_param": "page",
            "items_path": "data.list",
            "last_page_path": "data.last_page",
            "auth_scheme": "Bearer",
        },
        "token": {
            "key_names": ["token", "access_token", "student-token"],
            "regex": r"[A-Za-z0-9_\-]{20,}",
        },
        "fields": {
            "title": ["title", "name", "activity_name"],
            "place": ["address", "place"],
            "host": ["host", "organizer"],
            "time": ["start_time", "started_at"],
            "deadline": ["sign_end_time", "apply_ended_at"],
            "status": ["status", "apply_status"],
            "contact": ["contact"],
        },
    },
    "generic": {
        "verified": False,
        "note": "完全由 config.json 指定，抓包后自己填",
        "api": {
            "activity_path": "/api/student/activity",
            "college_path": "",
            "page_param": "page",
            "items_path": "result.data",
            "last_page_path": "result.last_page",
            "auth_scheme": "Bearer",
        },
        "token": {"key_names": ["student-token", "token"], "regex": r"[A-Za-z0-9|_\-]{16,}"},
        "fields": {},
    },
}

CANDIDATE_PATHS = [
    "/api/student/activity",
    "/api/student/activities",
    "/api/activity",
    "/api/activity/list",
    "/api/activity/index",
    "/api/student/activity/list",
    "/api/open/activity",
    "/api/v1/activity",
    "/api/v1/activities",
]

DEFAULT_FIELDS = {
    "title": ["name", "title"],
    "place": ["address", "place", "location", "site"],
    "host": ["host", "organizer", "sponsor"],
    "time": ["started_at", "start_time", "start"],
    "deadline": ["apply_ended_at", "apply_end", "deadline"],
    "apply_start": ["apply_started_at", "apply_start", "sign_start_time", "apply_begin"],
    "status": ["apply_status", "status"],
    "contact": ["contact", "contacts"],
}

CONDITION_RE = re.compile(
    r"(?:报名条件|参与对象|报名对象|活动对象|参与人员|面向对象)\s*[:：]?\s*([^\n，。；]{2,60})")


# ---------------------------------------------------------------- 配置
def deep_merge(base, over):
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path):
    p = pathlib.Path(path)
    if not p.exists():
        sys.exit("[!] 配置文件不存在：%s\n    先跑 python configure.py 生成" % p)
    cfg = json.loads(p.read_text(encoding="utf-8"))
    preset = PLATFORMS.get(cfg.get("platform", "generic"), PLATFORMS["generic"])
    cfg = deep_merge(preset, cfg)
    cfg["fields"] = deep_merge(DEFAULT_FIELDS, cfg.get("fields") or {})
    links = cfg.get("links") or {}
    if links.get("detail_url"):
        links["detail_url"] = links["detail_url"].replace(
            "{base_url}", cfg.get("base_url", "").rstrip("/"))
    cfg["links"] = links
    cfg.setdefault("filter", {})
    cfg.setdefault("token", {})
    cfg.setdefault("email", {})
    cfg.setdefault("max_page", 50)
    cfg["_preset_verified"] = preset.get("verified", False)
    return cfg


# ---------------------------------------------------------------- HTTP
def http_json(url, token=None, params=None, timeout=25, scheme="Bearer"):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X)",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = ("%s %s" % (scheme, token)).strip()
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
            raw = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "ignore")
        except Exception:
            pass
        if e.code in (401, 403):
            return {"_unauth": True, "_code": e.code, "message": "Unauthenticated."}
        return {"_error": "HTTP %s" % e.code, "_raw": raw[:400]}
    except Exception as e:
        return {"_error": str(e)}
    try:
        return json.loads(raw)
    except Exception:
        return {"_raw": raw[:400]}


def dig(obj, path):
    cur = obj
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def find_list_path(obj, prefix=""):
    """在返回 JSON 里找出「活动数组」所在的点号路径，省得用户自己数嵌套。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = (prefix + "." + str(k)) if prefix else str(k)
            if isinstance(v, list) and v and isinstance(v[0], dict):
                return p
            r = find_list_path(v, p)
            if r:
                return r
    return ""


def pull_items(j):
    """接口结构不确定时，尽量把活动数组掏出来。"""
    res = j.get("result", j) if isinstance(j, dict) else j
    if isinstance(res, list):
        return res
    if isinstance(res, dict):
        for key in ("items", "data", "list", "rows", "records", "activities"):
            if isinstance(res.get(key), list):
                return res[key]
        if "id" in res or "title" in res or "name" in res:
            return [res]
    return []


def fetch_all(cfg, token, verbose=False):
    """返回 (items, err)。items 为 None 表示「连不上/取不到」，err 是可读原因。

    注意：连不上和「确实没有活动」必须区分开 —— 否则用户会以为学校没活动。
    """
    api = cfg["api"]
    base = cfg["base_url"].rstrip("/")
    url = base + api["activity_path"]
    items, raw_pages = [], []
    last_page = None
    for page in range(1, int(cfg.get("max_page", 50)) + 1):
        j = http_json(url, token, {api["page_param"]: page}, scheme=api.get("auth_scheme", "Bearer"))
        if isinstance(j, dict) and j.get("_unauth"):
            return None, "unauth"
        if isinstance(j, dict) and j.get("_error"):
            # 第一页就失败 = 根本连不上，不能当成 0 条活动
            if page == 1:
                return None, j["_error"]
            print("[warn] page %d: %s（已取 %d 条，按已有数据继续）" % (page, j["_error"], len(items)))
            break
        got = dig(j, api.get("items_path", "")) or pull_items(j)
        got = [g for g in got if isinstance(g, dict)]
        raw_pages.append(j)
        if verbose:
            print("   page %-2d -> %d 条" % (page, len(got)))
        if not got:
            break
        items.extend(got)
        last_page = dig(j, api.get("last_page_path", ""))
        # 有的接口把页码返回成字符串
        if isinstance(last_page, str) and last_page.isdigit():
            last_page = int(last_page)
        if isinstance(last_page, int) and page >= last_page:
            break
    if not raw_pages:
        return None, "接口没有返回任何数据（可能 items_path 配错了）"
    max_page = int(cfg.get("max_page", 50))
    if len(raw_pages) >= max_page and not (isinstance(last_page, int) and last_page <= max_page):
        # 翻满了还没到底 = 后面的活动被截断了，不能闷声不响
        print("[warn] 翻页到上限 %d 页（%d 条）还没结束，后面的活动没拉到。"
              "把 config.json 的 max_page 调大（例如 100）。" % (max_page, len(items)))
    return items, None


# ---------------------------------------------------------------- token
def cache_roots():
    home = pathlib.Path.home()
    roots = []
    docs = os.environ.get("USERPROFILE") or str(home)
    roots.append(pathlib.Path(docs) / "Documents" / "WXWork")
    for env in ("LOCALAPPDATA", "APPDATA"):
        b = os.environ.get(env)
        if b:
            roots.append(pathlib.Path(b) / "Tencent" / "WXWork")
    roots.append(home / "Library" / "Application Support" / "WXWork")
    roots.append(home / "Documents" / "WXWork")
    return roots


def scan_token(cfg, verbose=False):
    """从企业微信桌面端的 CEF localStorage 里挖 token。

    企微内置浏览器打开过第二课堂后，token 会明文落在 leveldb 的 .log 里。
    这样就不必在外部浏览器登录 —— 企微 OAuth 在外部浏览器根本走不通。
    """
    tk = cfg.get("token", {})
    key_names = tk.get("key_names") or ["student-token"]
    host_hint = tk.get("host_hint") or ""
    regex = tk.get("regex") or r"[A-Za-z0-9|_\-]{16,}"
    rx = re.compile(regex.encode())

    extra = tk.get("extra_cache_dir")
    roots = cache_roots()
    if extra:
        roots.insert(0, pathlib.Path(os.path.expanduser(extra)))

    pats = []
    for r in roots:
        pats.append(str(r / "*" / "WXWorkCefCache" / "Local Storage" / "leveldb" / "*.log"))
        pats.append(str(r / "*" / "*" / "WXWorkCefCache" / "Local Storage" / "leveldb" / "*.log"))
        pats.append(str(r / "*" / "Local Storage" / "leveldb" / "*.log"))

    def collect(require_host):
        """扫一遍缓存文件。require_host=True 时只收含 host_hint 的文件。"""
        out = []
        for pat in pats:
            for f in glob.glob(pat):
                try:
                    raw = open(f, "rb").read()
                except Exception:
                    continue
                if require_host and host_hint and host_hint.encode() not in raw:
                    continue
                cands = []
                for key in key_names:
                    kb = key.encode()
                    start = 0
                    while True:
                        i = raw.find(kb, start)
                        if i < 0:
                            break
                        seg = raw[i + len(kb): i + len(kb) + 240]
                        for m in rx.finditer(seg):
                            cands.append(m.group(0).decode("ascii", "ignore"))
                        start = i + 1
                if not cands:
                    for m in rx.finditer(raw):
                        cands.append(m.group(0).decode("ascii", "ignore"))
                for c in cands:
                    out.append((len(c), c, f))
                if verbose and cands:
                    print("   [hit] %s -> %d 个候选" % (f, len(cands)))
        return out

    # 先按域名精确匹配；匹配不到就放宽再扫一次。
    # 原因：leveldb 里存的 origin 写法不一定等于你粘贴的域名，
    # 严格过滤会假阴性——token 明明在缓存里，却报「没找到」。
    found = collect(True)
    if not found and host_hint:
        if verbose:
            print("   [i] 缓存里没出现域名「%s」，放宽条件再扫一次" % host_hint)
        found = collect(False)

    if not found:
        return None, None
    found.sort(key=lambda x: -x[0])
    return found[0][1], found[0][2]


def resolve_token(cfg, args):
    tk = cfg.get("token", {})
    if args.token:
        return args.token, "--token 参数"
    env_name = tk.get("env") or "CAMPUS_TOKEN"
    if os.environ.get(env_name):
        return os.environ[env_name], "环境变量 " + env_name
    f = HERE / (tk.get("file") or "token.txt")
    if f.exists() and f.read_text(encoding="utf-8").strip():
        return f.read_text(encoding="utf-8").strip(), str(f)
    tok, src = scan_token(cfg, verbose=args.verbose)
    if tok and tk.get("file"):
        (HERE / tk["file"]).write_text(tok, encoding="utf-8")
    return tok, src


# ---------------------------------------------------------------- 筛选
def to_set(v):
    """把学院/校区限制字段（可能是 id 列表、dict 列表或字符串）摊平成字符串集合。"""
    out = set()
    if isinstance(v, list):
        for x in v:
            if isinstance(x, dict):
                for k in ("id", "name", "title"):
                    if x.get(k) not in (None, ""):
                        out.add(str(x[k]))
            elif x not in (None, ""):
                out.add(str(x))
    elif v not in (None, ""):
        out.add(str(v))
    return out


ISO_FORMATS = ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
               "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def fmt_time(s):
    """接口常返回 UTC 的 ISO 时间（如 2026-10-11T06:00:00.000000Z），转成你本地时间。"""
    s = str(s).strip()
    if not s:
        return ""
    if not s.endswith("Z"):
        return s
    for f in ISO_FORMATS:
        try:
            d = dt.datetime.strptime(s, f)
            break
        except ValueError:
            continue
    else:
        return s
    try:
        return d.replace(tzinfo=dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return s


def pick(item, keys, default=""):
    for k in keys:
        v = item.get(k)
        if isinstance(v, (list, tuple)):
            parts = []
            for x in v:
                if isinstance(x, dict):
                    parts.append(str(x.get("name") or x.get("title") or x.get("id") or ""))
                else:
                    parts.append(str(x))
            v = "、".join([p for p in parts if p])
        elif isinstance(v, dict):
            nm = v.get("name") or v.get("title") or ""
            ph = v.get("phone") or v.get("mobile") or v.get("tel") or ""
            parts2 = [str(x) for x in (nm, ph) if x not in (None, "")]
            v = " ".join(parts2) or str(v.get("id") or "")
        if v not in (None, ""):
            return str(v)
    return default


def audience_text(cfg, item, blob):
    """把「谁能报」说成人话：优先学院名单，其次抓正文里的报名条件。"""
    f = cfg["filter"]
    colmap = cfg.get("_college_names") or {}
    for af in (f.get("audience_fields") or ["apply_college_limit"]):
        raw = item.get(af)
        if raw:
            names, seen = [], set()
            for x in (raw if isinstance(raw, list) else [raw]):
                if x is None:
                    continue
                if isinstance(x, dict):
                    sid = "" if x.get("id") is None else str(x.get("id"))
                    nm = str(x.get("name") or x.get("title") or "")
                else:
                    sid, nm = str(x), ""
                if not sid and not nm:
                    continue
                label = nm or colmap.get(sid, "")
                if not label:
                    # 学院表没拉到时别甩一个裸编号给用户，至少说清它是什么
                    label = ("学院#" + sid) if sid.isdigit() else sid
                if label not in seen:
                    seen.add(label)
                    names.append(label)
            if names:
                return "仅限 " + "、".join(names)
    m = CONDITION_RE.search(blob or "")
    if m:
        return m.group(1).strip()
    for k in (f.get("audience_keywords") or []):
        if k and k in (blob or ""):
            return k
    return "不限（全校可报）"


def load_college_names(cfg, token):
    """拉一次学院列表，把 college_id 映射成学院名，参与对象才读得懂。"""
    path = (cfg.get("api") or {}).get("college_path")
    if not path or not token:
        return {}
    j = http_json(cfg["base_url"].rstrip("/") + path, token,
                  scheme=cfg["api"].get("auth_scheme", "Bearer"))
    if isinstance(j, dict) and (j.get("_unauth") or j.get("_error")):
        return {}
    out = {}
    for c in pull_items(j):
        if isinstance(c, dict) and c.get("id") is not None:
            out[str(c.get("id"))] = str(c.get("name") or c.get("title") or "")
    return out


def build_detail_url(cfg, item):
    """拼出「在第二课堂里打开这个活动」的直达链接。"""
    tpl = (cfg.get("links") or {}).get("detail_url") or ""
    if not tpl:
        return ""
    aid = item.get("id")
    if aid in (None, ""):
        return ""
    return tpl.replace("{id}", str(aid))


def is_muted(item, cfg, fd):
    """不提醒名单：命中后彻底不出现在报告/邮件里（连「校区内其他」也不列）。"""
    f = cfg["filter"]
    excl_ids = set(str(x) for x in (f.get("exclude_ids") or []))
    excl_kw = [k for k in (f.get("exclude_keywords") or []) if k]
    if not excl_ids and not excl_kw:
        return False
    aid = str(item.get("id") or "")
    if aid and aid in excl_ids:
        return True
    if excl_kw:
        title = str(pick(item, fd["title"]) or "")
        for k in excl_kw:
            if k in title:
                return True
    return False


def evaluate(item, cfg):
    """返回 (是否命中, 元信息)。"""
    f = cfg["filter"]
    fd = cfg["fields"]
    blob = json.dumps(item, ensure_ascii=False)

    if is_muted(item, cfg, fd):
        return False, {"_muted": True, "id": str(item.get("id") or ""),
                       "title": str(pick(item, fd["title"]) or "")}

    campus_field = f.get("campus_field") or "campus_id"
    campus_ids = set(map(str, f.get("campus_ids") or []))
    campus_kw = f.get("campus_keywords") or []

    hit_campus = []
    if campus_ids and str(item.get(campus_field)) in campus_ids:
        hit_campus.append("%s=%s" % (campus_field, item.get(campus_field)))
    for k in campus_kw:
        if k and k in blob:
            hit_campus.append("正文含「%s」" % k)
    ok_campus = bool(hit_campus) or (not campus_ids and not campus_kw)

    audience_fields = f.get("audience_fields") or ["apply_college_limit"]
    college_ids = set(map(str, f.get("college_ids") or []))
    college_kw = f.get("college_keywords") or []
    audience_kw = f.get("audience_keywords") or []

    hit_audience = []
    for af in audience_fields:
        vals = to_set(item.get(af))
        inter = vals & college_ids
        if inter:
            hit_audience.append("%s=%s" % (af, "/".join(sorted(inter))))
        for k in college_kw:
            if k and k in vals:
                hit_audience.append("名单含「%s」" % k)
            elif k and k in blob:
                hit_audience.append("正文含「%s」" % k)
    for k in audience_kw:
        if k and k in blob:
            hit_audience.append("正文含「%s」" % k)
    ok_audience = bool(hit_audience) or (not college_ids and not college_kw and not audience_kw)

    status_field = f.get("status_field") or fd.get("status", ["apply_status"])[0]
    open_status = set(map(str, f.get("open_status") or []))
    closed_status = set(map(str, f.get("closed_status") or []))
    status = str(item.get(status_field) or "")
    if open_status:
        ok_status = status in open_status
    elif closed_status:
        ok_status = status not in closed_status
    else:
        ok_status = True

    major_kw = f.get("major_keywords") or []
    major_ex = f.get("major_exclude_keywords") or []
    ok_major, hit_major = True, []
    for k in major_ex:
        if k and k in blob:
            ok_major = False
            hit_major.append("排除：正文含「%s」" % k)
            break
    if ok_major:
        for k in major_kw:
            if k and k in blob:
                hit_major.append("专业命中「%s」" % k)

    meta = {
        "title": pick(item, fd["title"]) or "(无标题)",
        "place": pick(item, fd["place"]),
        "host": pick(item, fd["host"]),
        "time": fmt_time(pick(item, fd["time"])),
        "deadline": fmt_time(pick(item, fd["deadline"])),
        "apply_start": fmt_time(pick(item, fd.get("apply_start") or [])),
        # 正则要在「换行已还原」的文本上跑，否则 json 转义出的 \n 会被当成普通字符
        "audience": audience_text(cfg, item, blob.replace("\\n", "\n").replace("\\r", "")),
        "contact": pick(item, fd["contact"]),
        "url": build_detail_url(cfg, item),
        "status": status,
        "campus_hit": hit_campus,
        "audience_hit": hit_audience + hit_major,
        "major_hit": hit_major,
        "id": str(item.get("id") or ""),
        "_campus": ok_campus,
        "_audience": ok_audience,
        "_status": ok_status,
    }
    ok = ok_campus and ok_audience and ok_status and ok_major
    if not ok:
        why = []
        if not ok_campus:
            why.append("不在目标校区")
        if not ok_audience:
            why.append("参与对象不含你的学院/全体新生")
        if not ok_major:
            why.append("限其他专业")
        if not ok_status:
            why.append("报名已结束" if status else "状态字段缺失")
        meta["reason"] = "；".join(why)
    return ok, meta


def sanity_check(cfg, items, hits):
    """字段名配错时，所有活动都会被静默筛掉，用户只会看到「命中 0 场」。

    这里按样本数据检查关键字段到底存不存在，不存在就明说，别让人白等。
    """
    if not items:
        return
    f = cfg["filter"]
    fd = cfg["fields"]

    def present_rate(field):
        if not field:
            return None
        n = sum(1 for i in items if isinstance(i, dict) and field in i)
        return n / float(len(items))

    checks = [
        ("状态", f.get("status_field") or fd.get("status", ["apply_status"])[0]),
        ("校区", f.get("campus_field") or "campus_id"),
        ("标题", (fd.get("title") or ["name"])[0]),
    ]
    for af in (f.get("audience_fields") or []):
        checks.append(("参与对象", af))

    missing = []
    for label, field in checks:
        rate = present_rate(field)
        if rate is not None and rate == 0:
            missing.append("%s 字段「%s」" % (label, field))

    warn = []
    if missing:
        warn.append("数据在样本里找不到这些字段：%s" % "、".join(missing))
        warn.append("字段名配错的话，活动会被全部筛掉。用 --offline <原始json> 看真实字段名，"
                    "或翻 ADAPT.md 的「抓包」一节。")
    if not hits and not missing:
        status_field = f.get("status_field") or fd.get("status", ["apply_status"])[0]
        open_status = set(map(str, f.get("open_status") or []))
        if open_status:
            vals = sorted(set(str(i.get(status_field)) for i in items
                              if isinstance(i, dict) and i.get(status_field) is not None))
            if vals and not (set(vals) & open_status):
                warn.append("没命中。接口里 %s 的实际取值是 %s，"
                            "但 open_status 配的是 %s —— 状态值对不上也会全军覆没。"
                            % (status_field, "、".join(vals[:12]), "、".join(sorted(open_status))))
    for w in warn:
        print("[!] " + w)


def classify(items, cfg):
    hits, near = [], []
    seen = set()
    muted = 0
    for it in items:
        ok, meta = evaluate(it, cfg)
        if meta.get("_muted"):
            muted += 1
            continue
        key = meta["title"] + "|" + meta["time"] + "|" + meta["id"]
        if key in seen:
            continue
        seen.add(key)
        if ok:
            hits.append({"meta": meta, "item": it})
        elif meta["_campus"] and meta["_status"]:
            # 校区对、还能报，但你报不了 —— 这才值得列出来
            near.append({"meta": meta, "item": it})
    hits.sort(key=lambda h: (h["meta"]["time"], h["meta"]["title"]))
    return hits, near, muted


# ---------------------------------------------------------------- 输出
def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


OPEN_TIP = ("第二课堂的链接<strong>必须在企业微信里打开</strong>。外部浏览器点开只会看到"
            "「请在企业微信客户端打开」——这是平台的登录方式决定的，绕不过去。<br>"
            "最快的一键办法：点下面的「复制链接」，粘贴到企业微信的<b>文件传输助手</b>"
            "（或任意群/好友），再点开即可直达活动页。")

JUMP_JS = """
<script>
(function () {
  var ua = (navigator.userAgent || '').toLowerCase();
  var inWxWork = ua.indexOf('wxwork') >= 0;
  var tip = document.getElementById('opentip');
  if (inWxWork && tip) { tip.style.display = 'none'; }
  var links = document.querySelectorAll('a.openlnk');
  for (var i = 0; i < links.length; i++) { (function (a) {
    var wrap = document.createElement('div');
    wrap.style.marginTop = '8px';
    var btn = document.createElement('button');
    btn.textContent = '复制链接（去企业微信粘贴打开）';
    btn.style.cssText = 'font-size:13px;padding:6px 12px;border:1px solid #185FA5;color:#185FA5;' +
      'background:#fff;border-radius:6px;cursor:pointer;margin-right:8px';
    btn.onclick = function () {
      var u = a.getAttribute('data-copy') || a.href;
      var done = function () {
        btn.textContent = '已复制，去企业微信粘贴打开';
        setTimeout(function () { btn.textContent = '复制链接（去企业微信粘贴打开）'; }, 2500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(u).then(done, function () { legacy(u, done); });
      } else { legacy(u, done); }
    };
    var wake = document.createElement('a');
    wake.textContent = '尝试唤起企业微信';
    wake.href = 'wxwork://';
    wake.style.cssText = 'font-size:13px;color:#5a636e';
    wrap.appendChild(btn); wrap.appendChild(wake);
    a.parentNode.appendChild(wrap);
  })(links[i]); }
  function legacy(u, done) {
    var t = document.createElement('textarea');
    t.value = u; document.body.appendChild(t); t.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(t); done();
  }
})();
</script>
"""


def build_html(cfg, hits, near, stats, note="", for_email=False):
    f = cfg["filter"]
    school = cfg.get("school", "")
    campus_name = cfg.get("campus_name") or "/".join(f.get("campus_keywords") or []) or "目标校区"
    college_name = cfg.get("college_name") or "/".join(f.get("college_keywords") or []) or "你的学院"
    today = dt.date.today().strftime("%Y-%m-%d")

    hit_html = ""
    for h in hits:
        m = h["meta"]
        span = m.get("apply_start", "")
        span = ("%s 至 %s" % (span, m["deadline"])) if span and m["deadline"] else (m["deadline"] or span)
        rows = ""
        for label, val in (
            ("活动时间", m["time"]), ("地点", m["place"]), ("主办", m["host"]),
            ("参与对象", m.get("audience", "")),
            ("报名时间", span),
            ("报名截止", m["deadline"]), ("联系人", m["contact"]),
            ("专业匹配", "；".join(m.get("major_hit") or [])),
        ):
            if val:
                cls = ' style="color:#b91c1c;font-weight:600"' if label == "报名截止" else ""
                rows += '<tr><td style="padding:3px 0;color:#5a636e;width:76px;vertical-align:top">%s</td><td style="padding:3px 0"%s>%s</td></tr>' % (label, cls, esc(val))

        title_html = esc(m["title"])
        if m.get("url"):
            u = esc(m["url"])
            title_html = '<a href="%s" style="color:#0f766e;text-decoration:none">%s</a>' % (u, esc(m["title"]))
            rows += ('<tr><td style="padding:3px 0;color:#5a636e;width:76px;vertical-align:top">打开</td>'
                     '<td style="padding:3px 0"><a class="openlnk" href="%s" data-copy="%s" '
                     'style="color:#185FA5;text-decoration:underline">'
                     '在第二课堂中打开这个活动</a></td></tr>') % (u, u)

        hit_html += (
            '<div style="background:#ecfdf5;border:1px solid #a7f3d0;border-left:4px solid #0f766e;'
            'border-radius:8px;padding:16px 18px;margin-bottom:14px">'
            '<div style="font-size:16px;font-weight:600;color:#0f766e;margin-bottom:8px">%s</div>'
            '<table style="font-size:14px;width:100%%;border-collapse:collapse">%s</table></div>'
        ) % (title_html, rows)
    if not hit_html:
        hit_html = ('<div style="background:#fff;border:1px solid #e3e7ec;border-radius:8px;'
                    'padding:16px 18px;color:#5a636e">本次没有同时满足全部条件的活动。</div>')

    near_html = ""
    if near:
        trs = ""
        for h in near:
            m = h["meta"]
            sub = " · ".join([x for x in (m["time"], m["place"]) if x])
            trs += ('<tr><td style="padding:9px 10px;border-bottom:1px solid #e3e7ec">%s'
                    '<div style="color:#5a636e;font-size:12px">%s</div></td>'
                    '<td style="padding:9px 10px;border-bottom:1px solid #e3e7ec;color:#b91c1c">%s</td></tr>'
                    ) % (esc(m["title"]), esc(sub), esc(m.get("reason", "")))
        near_html = (
            '<div style="margin-top:22px"><h3 style="font-size:15px;margin:0 0 10px;'
            'padding-bottom:8px;border-bottom:1px solid #e3e7ec">%s另外 %d 场 · 你报不了</h3>'
            '<table style="width:100%%;border-collapse:collapse;font-size:14px">%s</table></div>'
        ) % (esc(campus_name), len(near), trs)

    stat_html = ""
    for num, label in stats:
        stat_html += ('<div style="flex:1;min-width:96px;background:#fafbfc;border:1px solid #e3e7ec;'
                      'border-radius:8px;padding:10px 12px;text-align:center">'
                      '<b style="display:block;font-size:20px">%s</b>'
                      '<span style="color:#5a636e;font-size:12px">%s</span></div>') % (num, esc(label))
    note_html = '<p style="font-size:13px;color:#5a636e;margin:10px 0 0">%s</p>' % esc(note) if note else ""

    # 邮件客户端会剥掉 JS，所以邮件里给静态提示；本地报告给可交互的一键复制
    if for_email:
        top_html = ('<div style="background:#fff8e6;border:1px solid #f0d9a0;border-radius:8px;'
                    'padding:12px 16px;margin-bottom:18px;font-size:13px;color:#854F0B">'
                    '<b>怎么打开：</b>%s</div>') % OPEN_TIP
        script_html = ""
    else:
        top_html = ('<div id="opentip" style="background:#fff8e6;border:1px solid #f0d9a0;'
                    'border-radius:8px;padding:12px 16px;margin-bottom:18px;font-size:13px;'
                    'color:#854F0B"><b>当前不在企业微信中 · </b>%s</div>') % OPEN_TIP
        script_html = JUMP_JS

    return """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(school)s 第二课堂筛选 · %(today)s</title></head>
<body style="margin:0;background:#f6f7f9;color:#1a1d21;line-height:1.7;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif">
<div style="max-width:820px;margin:0 auto;padding:36px 22px 56px">
<h1 style="font-size:24px;margin:0 0 6px">%(school)s 第二课堂 · 筛选结果</h1>
<div style="color:#5a636e;font-size:13px;margin-bottom:20px">条件：%(campus)s 开展 · %(college)s 可参与 ｜ %(today)s ｜ 数据源：%(base)s</div>
%(top)s
%(hits)s
%(near)s
<div style="margin-top:22px;display:flex;gap:10px;flex-wrap:wrap">%(stats)s</div>
%(note)s
<div style="color:#5a636e;font-size:12px;margin-top:28px;border-top:1px solid #e3e7ec;padding-top:12px">
由 campus-activity-alert 自动生成 · %(today)s</div>
</div>%(script)s</body></html>""" % {
        "school": esc(school), "today": today, "campus": esc(campus_name),
        "college": esc(college_name), "base": esc(cfg.get("base_url", "")),
        "hits": hit_html, "near": near_html, "stats": stat_html, "note": note_html,
        "top": top_html, "script": script_html,
    }


def build_text(cfg, hits, near):
    lines = ["%s 第二课堂 · 筛选结果" % cfg.get("school", ""),
             "生成时间 " + dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "命中 %d 场" % len(hits), "",
             "【怎么打开】链接必须在企业微信里点开，外部浏览器会提示"
             "「请在企业微信客户端打开」。把链接复制到企业微信「文件传输助手」再点开即可。",
             ""]
    for h in hits:
        m = h["meta"]
        lines.append("-" * 44)
        lines.append("【%s】" % m["title"])
        span = m.get("apply_start", "")
        span = ("%s 至 %s" % (span, m["deadline"])) if span and m["deadline"] else (m["deadline"] or span)
        for label, val in (("活动时间", m["time"]), ("地点", m["place"]), ("主办", m["host"]),
                           ("参与对象", m.get("audience", "")),
                           ("报名时间", span), ("报名截止", m["deadline"]),
                           ("联系人", m["contact"]),
                           ("专业匹配", "；".join(m.get("major_hit") or [])),
                           ("链接", m.get("url", ""))):
            if val:
                lines.append("  %s：%s" % (label, val))
    if near:
        lines.append("")
        lines.append("校区内其他 %d 场（不符合）：" % len(near))
        for h in near:
            lines.append("  · %s —— %s" % (h["meta"]["title"], h["meta"].get("reason", "")))
    return "\n".join(lines)


# ---------------------------------------------------------------- 邮件
def send_mail(cfg, subject, html, text):
    m = cfg.get("email") or {}
    if not m.get("enabled"):
        print("[skip] 邮件未启用（在 config.json 里把 email.enabled 设为 true）")
        return []
    tos = m.get("to") or []
    if isinstance(tos, str):
        tos = [tos]
    if not tos:
        print("[skip] 没有收件人（config.json 的 email.to）")
        return []
    if not (m.get("user") and m.get("password")):
        print("[skip] 缺发件邮箱或 SMTP 授权码（email.user / email.password）")
        return []
    ok = []
    try:
        with smtplib.SMTP_SSL(m.get("smtp_host", "smtp.qq.com"), int(m.get("smtp_port", 465)),
                              context=SSL_CTX) as s:
            s.login(m["user"], m["password"])
            for addr in tos:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = Header(subject, "utf-8")
                msg["From"] = m["user"]
                msg["To"] = addr
                msg.attach(MIMEText(text, "plain", "utf-8"))
                msg.attach(MIMEText(html, "html", "utf-8"))
                s.sendmail(m["user"], [addr], msg.as_string())
                ok.append(addr)
                print("[ok] 已发送至 " + addr)
    except smtplib.SMTPAuthenticationError:
        print("[!] SMTP 登录失败：邮箱或授权码不对。\n"
              "    QQ 邮箱要用「SMTP 授权码」，不是登录密码 —— 邮箱设置 → 账户 → POP3/SMTP 里单独生成。")
    except Exception as e:
        print("[!] 发信失败：%s\n"
              "    端口 465 走 SSL；如果你们邮箱是 587，需要在 config.json 里改 smtp_port。" % e)
    return ok


# ---------------------------------------------------------------- 主流程
def run(cfg, args, items=None, note=""):
    out = HERE / (args.out or "outputs")
    out.mkdir(exist_ok=True)

    if getattr(args, "colleges", ""):
        try:
            cfg["_college_names"] = {
                str(c.get("id")): str(c.get("name") or "")
                for c in pull_items(json.loads(pathlib.Path(args.colleges).read_text(encoding="utf-8")))
                if isinstance(c, dict) and c.get("id") is not None
            }
        except Exception as e:
            print("[warn] 学院表载入失败：%s" % e)

    if items is None:
        token, src = resolve_token(cfg, args)
        if not token:
            sys.exit("[!] 取不到 token。请在企业微信客户端里打开一次第二课堂，"
                     "然后重跑；或用 --token 手动传入。\n    排查：python alert.py --scan-token")
        print("[ok] token 来源：" + str(src))
        items, err = fetch_all(cfg, token, verbose=args.verbose)
        if err == "unauth" and src and str(src).endswith("token.txt"):
            # 缓存里的旧 token 过期了，别让用户手动删文件，重扫一次再试
            try:
                (HERE / (cfg.get("token", {}).get("file") or "token.txt")).unlink()
            except Exception:
                pass
            print("[i] token.txt 里的旧 token 已失效，清掉重扫企微缓存 ...")
            tok2, src2 = scan_token(cfg, verbose=args.verbose)
            if tok2:
                items, err = fetch_all(cfg, tok2, verbose=args.verbose)
                if items is not None:
                    (HERE / (cfg.get("token", {}).get("file") or "token.txt")).write_text(tok2, encoding="utf-8")
                    print("[ok] 换到新 token，来自 " + str(src2))
        if items is None:
            if err == "unauth":
                sys.exit("[!] token 已失效（接口返回 Unauthenticated）。"
                         "重新在企业微信里打开一次第二课堂即可。\n"
                         "    排查：python alert.py --scan-token")
            sys.exit("[!] 取不到活动数据：%s\n"
                     "    常见原因：\n"
                     "      1) base_url 填错了（用 --discover 探测接口路径）\n"
                     "      2) 学校内网/VPN 才能访问\n"
                     "      3) api.items_path 与返回结构对不上（抓包确认，见 ADAPT.md）"
                     % err)
        (out / "activities_raw.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print("[ok] 拉取活动 %d 条" % len(items))
        names = load_college_names(cfg, token)
        if names:
            cfg["_college_names"] = names
            print("[ok] 学院表 %d 个（参与对象可直接显示学院名）" % len(names))
        elif not cfg.get("_college_names"):
            print("[i] 没拉到学院表，参与对象可能显示成编号（不影响筛选，只是不好读）")

    total = len(items)
    hits, near, muted = classify(items, cfg)
    sanity_check(cfg, items, hits)
    if not hits and getattr(args, "demo", False):
        print("[i] 样例数据写的是「中心校区 / 计算机学院」，和你填的校区、学院对不上，"
              "所以命中 0 是正常的。接上真实数据后才会按你的条件命中。")
    if muted:
        print("[i] 不提醒名单屏蔽 %d 场（config.json → filter.exclude_ids / exclude_keywords）" % muted)

    f = cfg["filter"]
    status_field = f.get("status_field") or cfg["fields"]["status"][0]
    open_status = set(map(str, f.get("open_status") or []))
    closed_status = set(map(str, f.get("closed_status") or []))
    stats = [(str(total), "活动总数")]
    if open_status:
        n_open = sum(1 for i in items if str(i.get(status_field) or "") in open_status)
        stats.append((str(n_open), "当前可报名"))
    elif closed_status:
        n_open = sum(1 for i in items if str(i.get(status_field) or "") not in closed_status)
        stats.append((str(n_open), "当前可报名"))
    stats.append((str(len(near) + len(hits)), "校区内可报"))
    stats.append((str(len(hits)), "最终命中"))

    html = build_html(cfg, hits, near, stats, note=note)
    text = build_text(cfg, hits, near)
    (out / "report.html").write_text(html, encoding="utf-8")
    (out / "report.txt").write_text(text, encoding="utf-8")

    print("\n" + text)
    print("\n[ok] 报告：" + str(out / "report.html"))

    subject = "【%s】第二课堂命中 %d 场 · %s" % (
        cfg.get("school", "第二课堂"), len(hits), dt.date.today().strftime("%m-%d"))
    if hits:
        dl = [h["meta"]["deadline"] for h in hits if h["meta"]["deadline"]]
        if dl:
            subject += " · 最近截止 " + dl[0]
    if args.email or (cfg.get("email", {}).get("enabled") and not args.no_email):
        send_mail(cfg, subject, build_html(cfg, hits, near, stats, note=note, for_email=True), text)
    return hits


def do_discover(cfg, args):
    token, src = resolve_token(cfg, args)
    if not token:
        print("[!] 先拿到 token 才能探测接口（python alert.py --scan-token）")
        return
    print("[ok] token 来源：" + str(src))
    base = cfg["base_url"].rstrip("/")
    print("探测 %s ..." % base)
    for p in CANDIDATE_PATHS:
        j = http_json(base + p, token, {cfg["api"]["page_param"]: 1},
                      scheme=cfg["api"].get("auth_scheme", "Bearer"))
        if isinstance(j, dict) and j.get("_unauth"):
            print("  %-28s 401 需要鉴权（说明路径可能存在）" % p)
        elif isinstance(j, dict) and j.get("_error"):
            print("  %-28s %s" % (p, j["_error"]))
        else:
            n = len(pull_items(j))
            print("  %-28s 200 OK，取出 %d 条  <== 可用" % (p, n))
            if n:
                auto = find_list_path(j)
                print("     建议 activity_path = %s，items_path = %s"
                      % (p, auto or cfg["api"].get("items_path") or "result.data"))


def main():
    ap = argparse.ArgumentParser(description="校园第二课堂活动筛选与推送")
    ap.add_argument("--config", default="config.json")
    ap.add_argument("--school", default="", help="使用 schools/<name>.json")
    ap.add_argument("--token", default="")
    ap.add_argument("--offline", default="", help="离线 JSON 文件（调试字段用）")
    ap.add_argument("--colleges", default="", help="学院表 JSON，离线时用来还原学院名")
    ap.add_argument("--demo", action="store_true", help="用内置样例数据跑一遍")
    ap.add_argument("--scan-token", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--email", action="store_true", help="强制发送邮件")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--out", default="outputs")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cfg_path = args.config
    if args.school:
        cfg_path = str(HERE / "schools" / (args.school + ".json"))
    elif args.demo:
        cfg_path = str(HERE / "schools" / "demo.json")

    cfg = load_config(cfg_path)

    if args.scan_token:
        tok, src = scan_token(cfg, verbose=True)
        if tok:
            # 别把完整 token 打到终端上 —— 截图、录屏、分享日志都会泄出去
            masked = (tok[:6] + "*" * max(0, len(tok) - 10) + tok[-4:]) if len(tok) > 12 else "*" * len(tok)
            print("[ok] 取到 token：%s（已脱敏，完整值存在 %s）\n     来自 %s"
                  % (masked, cfg.get("token", {}).get("file") or "token.txt", src))
        else:
            print("[!] 没找到。请先在「企业微信客户端」里打开一次第二课堂。")
        return
    if args.discover:
        do_discover(cfg, args)
        return

    if args.demo:
        items = json.loads((HERE / "sample" / "sample_activities.json").read_text(encoding="utf-8"))
        print("[demo] 使用内置样例数据 %d 条" % len(items))
        run(cfg, args, items=items, note="这是内置样例数据，用于验证筛选逻辑是否正确。")
        return
    if args.offline:
        j = json.loads(pathlib.Path(args.offline).read_text(encoding="utf-8"))
        items = pull_items(j)
        print("[offline] 载入 %d 条" % len(items))
        run(cfg, args, items=items, note="离线模式：数据来自本地文件，不是实时接口。")
        return

    run(cfg, args)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[exit] 已取消")
        sys.exit(0)
