#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
configure.py · 交互式适配向导

别的学校的学生跑这一个脚本就能配好。只要准备 4 样东西：

  1. 第二课堂的页面链接（在企业微信里打开，复制地址栏）
  2. 你的校区名
  3. 你的学院名
  4. 发信邮箱 + SMTP 授权码（不推送可留空）

外加两个前提：装了企业微信桌面端、并且在里面打开过一次第二课堂。

跑法：
    python configure.py
"""

import json
import pathlib
import re
import subprocess
import sys

import alert
from alert import CANDIDATE_PATHS, PLATFORMS, find_list_path, http_json, pull_items

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = pathlib.Path(__file__).resolve().parent


def ask(prompt, default="", required=False):
    while True:
        tip = prompt + (" [默认 %s]： " % default if default else "： ")
        try:
            v = input(tip).strip()
        except EOFError:
            raise SystemExit("\n[!] 读不到输入。请在交互式终端里运行（不要重定向 / 不要用管道）。")
        v = v or default
        if required and not v:
            print("    （这项必填，不然跑不起来）")
            continue
        return v


def pause(prompt):
    try:
        input(prompt)
    except EOFError:
        raise SystemExit("\n[!] 读不到输入。请在交互式终端里运行（不要用管道或重定向）。")


def choose(prompt, options, default_idx=0):
    print("\n" + prompt)
    for i, o in enumerate(options, 1):
        print("  %d) %s" % (i, o))
    try:
        raw = input("选择 [默认 %d]： " % (default_idx + 1)).strip()
    except EOFError:
        raise SystemExit("\n[!] 读不到输入。请在交互式终端里运行（不要用管道或重定向）。")
    if not raw:
        return default_idx
    try:
        return max(0, min(len(options) - 1, int(raw) - 1))
    except ValueError:
        return default_idx


def pick_college(colleges, hint_name):
    """从学院表里挑出自己的学院。

    大校的学院表有上百条，直接列出来用户根本翻不到，所以先按用户填的
    学院名模糊过滤；找不到也给「手动填 id」的退路。
    """
    def label(c):
        return "%s（id=%s%s）" % (c.get("name") or "(无名)",
                                 c.get("id"),
                                 "，campus_id=%s" % c.get("campus_id")
                                 if c.get("campus_id") is not None else "")

    pool = colleges
    if hint_name:
        hit = [c for c in colleges if hint_name in str(c.get("name") or "")]
        if hit:
            pool = hit
            print("   按「%s」筛出 %d 个（共 %d 个学院）" % (hint_name, len(pool), len(colleges)))

    # 还是太多就再让用户输关键词收一收
    while len(pool) > 40:
        kw = ask("   学院太多（%d 个），输个关键词再筛（直接回车显示全部）" % len(pool), "")
        if not kw:
            break
        narrowed = [c for c in pool if kw in str(c.get("name") or "")]
        if not narrowed:
            print("   没匹配到，换个词试试")
            continue
        pool = narrowed
        print("   剩 %d 个" % len(pool))

    opts = [label(c) for c in pool[:60]]
    opts.append("上面没有 / 我要手动填学院 id")
    idx = choose("   你的学院是", opts)
    if idx == len(pool[:60]):
        raw = ask("   直接填学院 id（可填多个，逗号分隔；不知道就留空，靠学院名关键词判定）", "")
        ids = []
        for x in raw.split(","):
            x = x.strip()
            if x.isdigit():
                ids.append(int(x))
            elif x:
                print("   「%s」不是数字 id，已忽略" % x)
        return {"id": ids[0] if ids else None, "college_ids": ids, "campus_id": None}
    c = pool[idx]
    return {"id": c.get("id"), "college_ids": [c.get("id")] if c.get("id") is not None else [],
            "campus_id": c.get("campus_id")}


def slugify(name):
    """中文校名也要保留 —— 否则「某某大学」会被清成 school，多个学校互相覆盖。"""
    s = re.sub(r"[^\w]+", "-", name, flags=re.UNICODE).strip("-").lower()
    return s or "school"


# 步骤编号：分支里有些步骤（接口探测、选学院）取不到 token 时会跳过，
# 写死编号就会出现「6) 之后直接 9)」。用计数器保证永远连续。
STEP = [6]


def next_step():
    STEP[0] += 1
    return STEP[0]


def preflight():
    print("=" * 64)
    print(" campus-activity-alert · 适配向导")
    print(" 开始前请确认下面 4 项，缺一项后面都会卡住")
    print("=" * 64)
    print("""
  [ ] 1. 电脑上装了【企业微信桌面端】并且已登录
         —— 不是手机端、不是网页版；手机和网页版拿不到登录凭据

  [ ] 2. 已经在企业微信里【打开过一次第二课堂】
         —— 打开一次，工具才能从企微缓存里取到 token
         —— 没做这一步的话，后面会提示取不到 token

  [ ] 3. 能打开第二课堂的活动列表页，并把地址栏链接复制出来

  [ ] 4. 有一个能发信的邮箱（QQ 邮箱即可），并知道 SMTP 授权码
         —— 授权码在邮箱设置里单独生成，不是登录密码；不推送可留空
""")
    pause("确认好了按回车继续（Ctrl+C 退出）... ")


def main():
    preflight()

    school = ask("\n1) 你的学校全称", "示例大学", required=True)

    pidx = choose(
        "2) 第二课堂平台类型（不确定就选「职迅」，多数高校是它）",
        ["职迅 zhixun（域名常为 erke.<学校>.edu.cn，已验证）",
         "趣拓 qutuo（模板，接口需自行校准）",
         "其他 generic（需要抓包后自己填）"],
    )
    platform = ["zhixun", "qutuo", "generic"][pidx]
    cfg = {"school": school, "platform": platform}
    preset = PLATFORMS[platform]
    cfg["api"] = dict(preset["api"])
    cfg["token"] = dict(preset["token"])
    cfg["fields"] = dict(preset["fields"])
    if preset.get("links"):
        cfg["links"] = dict(preset["links"])

    print("\n3) 第二课堂页面链接（必填）")
    print("   在企业微信里打开第二课堂 → 复制地址栏链接")
    print("   例：https://erke.example.edu.cn/public/index.php/mobile/my?is_from_work=true")
    url = ask("   粘贴到这里", required=True)
    base = (url.split("/public/index.php")[0] + "/public/index.php"
            if "/public/index.php" in url else url)
    cfg["base_url"] = base.rstrip("/")
    cfg["token"]["host_hint"] = cfg["base_url"].split("//")[-1].split("/")[0]

    campus_name = ask("\n4) 你所在的校区名（用于正文兜底，例如「中心校区」）", required=True)
    college_name = ask("5) 你的学院全名（例如「计算机学院」）", required=True)
    cfg["campus_name"] = campus_name
    cfg["college_name"] = college_name

    # ---------- 自动部分
    print("\n6) 取登录 token ...（从企业微信缓存里自动挖）")
    tok, src = alert.scan_token(cfg, verbose=False)
    campus_ids, college_ids = [], []
    if tok:
        (HERE / "token.txt").write_text(tok, encoding="utf-8")
        print("   [ok] 已取到，写入 token.txt")
        print("        " + str(src))

        print("\n%d) 探测活动接口 ..." % next_step())
        for p in CANDIDATE_PATHS:
            j = http_json(cfg["base_url"] + p, tok, {cfg["api"]["page_param"]: 1},
                          scheme=cfg["api"].get("auth_scheme", "Bearer"))
            if isinstance(j, dict) and (j.get("_unauth") or j.get("_error")):
                continue
            n = len(pull_items(j))
            if n:
                cfg["api"]["activity_path"] = p
                auto = find_list_path(j)
                if auto:
                    cfg["api"]["items_path"] = auto
                print("   [ok] %s -> %d 条，数组路径 %s" % (p, n, auto or cfg["api"]["items_path"]))
                break
        else:
            print("   [!] 常见路径都没命中。需要抓包确认，办法见 ADAPT.md。")

        cpath = cfg["api"].get("college_path") or ""
        colleges = []
        if cpath:
            j = http_json(cfg["base_url"] + cpath, tok,
                          scheme=cfg["api"].get("auth_scheme", "Bearer"))
            if not (isinstance(j, dict) and (j.get("_unauth") or j.get("_error"))):
                colleges = pull_items(j)
        if colleges:
            print("\n%d) 选你的学院（接口返回 %d 个）" % (n(), len(colleges)))
            chosen = pick_college(colleges, college_name)
            college_ids = chosen["college_ids"]
            if chosen["campus_id"] is not None:
                campus_ids = [chosen["campus_id"]]
            print("   [ok] 学院 id=%s，推断校区 campus_id=%s" % (college_ids, chosen["campus_id"]))
            # 学院表里未必带 campus_id，让用户自己补一下更稳
            manual = ask("   你校区的 campus_id（推断值是 %s，不确定就留空，靠校区名关键词兜底）"
                         % (chosen["campus_id"] if chosen["campus_id"] is not None else "无"),
                         str(chosen["campus_id"]) if chosen["campus_id"] is not None else "")
            if manual:
                ids = [int(x.strip()) for x in manual.split(",") if x.strip().isdigit()]
                if ids:
                    campus_ids = ids
            print("   [ok] 最终 campus_ids=%s" % campus_ids)
        else:
            print("\n%d) 拉不到学院列表，ID 留空（靠校区名/学院名关键词判定，一样能用）" % next_step())
            manual = ask("   知道你校区的 campus_id 吗？（多个用逗号分隔，不知道就留空）", "")
            if manual:
                campus_ids = [int(x.strip()) for x in manual.split(",") if x.strip().isdigit()]
    else:
        print("   [!] 缓存里没找到 token。")
        print("       去企业微信客户端里打开一次第二课堂，然后重新跑本向导。")
        print("       现在先按关键词方式生成配置，接口相关的自动探测跳过。")

    cfg["filter"] = {
        "campus_field": "campus_id",
        "campus_ids": campus_ids,
        "campus_keywords": [campus_name],
        "audience_fields": ["apply_college_limit"],
        "college_ids": college_ids,
        "college_keywords": [college_name],
        "audience_keywords": ["全体新生"],
        "major_fields": [],
        "major_keywords": [],
        "major_exclude_keywords": [],
        "status_field": "apply_status",
        "open_status": ["ongoing", "waiting"],
        "closed_status": ["ended"],
        "exclude_ids": [],
        "exclude_keywords": [],
    }

    # 不提醒名单：某些活动你已经知道了 / 不感兴趣，填进来就不再出现在任何报告里
    muting = ask("\n%d) 有没有不想再被提醒的活动？填标题关键词，多个用逗号分隔，可留空" % next_step(), "")
    if muting:
        cfg["filter"]["exclude_keywords"] = [k.strip() for k in muting.split(",") if k.strip()]
        print("   已加入不提醒名单：%s（改 config.json → filter.exclude_keywords 可恢复）"
              % "、".join(cfg["filter"]["exclude_keywords"]))

    major = ask("\n%d) 你的专业名（可留空，留空则只按校区和学院筛）" % next_step(), "")
    if major:
        cfg["filter"]["major_keywords"] = [major]

    print("\n%d) 邮件推送（可留空）" % next_step())
    print("    QQ 邮箱授权码：邮箱设置 → 账户 → POP3/SMTP 服务 → 生成授权码")
    if ask("    现在配置？y/n", "n").lower().startswith("y"):
        cfg["email"] = {
            "enabled": True,
            "smtp_host": ask("    SMTP 服务器", "smtp.qq.com"),
            "smtp_port": int(ask("    SMTP 端口", "465") or 465),
            "user": ask("    发件邮箱", ""),
            "password": ask("    SMTP 授权码", ""),
            "to": [a.strip() for a in ask("    收件邮箱，多个用逗号分隔", "").split(",") if a.strip()],
        }
    else:
        cfg["email"] = {"enabled": False, "smtp_host": "smtp.qq.com", "smtp_port": 465,
                        "user": "", "password": "", "to": []}

    cfg["max_page"] = 50

    (HERE / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "schools").mkdir(exist_ok=True)
    slug = slugify(school)
    sp = HERE / "schools" / (slug + ".json")
    sp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print(" [ok] 已生成 config.json 和 schools/%s.json" % slug)
    print("""
 以后就两条命令：
   python alert.py --demo          先看样例效果（不用联网）
   python alert.py                 实跑，报告在 outputs/report.html
""")
    if ask(" 现在就跑一次？y/n", "y").lower().startswith("y"):
        print("\n---- 运行 alert.py ----")
        try:
            subprocess.call([sys.executable, str(HERE / "alert.py")])
        except Exception as e:
            print("[warn] 直接运行失败：%s" % e)
        print("\n报告在 outputs/report.html —— 双击打开，"
              "里面有「复制链接」按钮，粘到企业微信里就能打开活动。")
    print("=" * 64)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[exit] 已取消")
    except SystemExit as e:
        print(e)
