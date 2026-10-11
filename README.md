# campus-activity-alert

**把「第二课堂活动太多刷不过来、想报的总是错过截止」这件事自动化。**

从一个学校的第二课堂平台拉取活动列表，按 **校区 + 学院/参与对象 + 报名未截止** 三条筛出你能报的活动，生成报告，并可选邮件推送。

- 只用 Python 标准库，`pip install` 都不需要
- 不需要你在电脑上登录任何东西（原理见下）
- 换学校只需换一份 JSON，不用改代码

已在一所真实高校的第二课堂上完整跑通（平台：职迅，域名形如 `erke.<学校>.edu.cn`），现在抽成了通用工具。

---

## 它解决了什么问题

第二课堂的活动有三个特点：总量大（实测接口里 716 条）、绝大多数已结束、能报的里大部分限别院。手动刷等于大海捞针，而且报名窗口常常只有几天。

这个工具把 716 条压成 1–2 条，并且**告诉你剩下那些为什么不能报**，而不是只丢个结果。

## 原理：为什么不用登录

大多数高校的第二课堂嵌在**企业微信**里，走的是企微 OAuth 免登。这意味着：

- 在外部浏览器打开会提示「请在企业微信客户端打开」——登录这条路是死的
- 但企业微信桌面端的 CEF 缓存里，`Local Storage` 的 leveldb 文件**明文存着登录 token**

所以正确做法不是登录，而是**直接把 token 读出来**：

```
<文档>/WXWork/<uid>/WXWorkCefCache/Local Storage/leveldb/*.log
```

工具里的 `scan_token()` 干的就是这个。前提只有一个：**你已经在企业微信客户端里打开过一次第二课堂**。

## 运行环境（必须满足，缺一不可）

| 条件 | 说明 |
|---|---|
| **Python 3.8 以上** | 不需要装任何第三方库。Windows 安装时务必勾选 **Add to PATH** |
| **企业微信桌面端已登录** | 手机端和网页版不行——登录凭据只存在桌面的企微缓存里 |
| **在企业微信里打开过一次第二课堂** | 打开一次，工具才能取到 token。没做过这会报「取不到 token」 |
| 能联网访问学校第二课堂域名 | 校园网 / 外网都行，只要能打开那个页面 |
| 邮箱 + SMTP 授权码 | 只有推送才需要；不推送可留空 |

**不支持的环境**：只有手机、只装了网页版企微、Python 没加入 PATH——这几种都跑不了。

## 你需要提供什么

**只有 4 项必填**，其余全部自动获取：

| 必填 | 说明 | 怎么拿到 |
|---|---|---|
| 第二课堂页面链接 | 用来推出接口地址 | 在企微里打开活动列表页，复制地址栏 |
| 你的校区名 | 正文兜底用 | 如「中心校区」 |
| 你的学院全名 | 判定参与对象 | 如「计算机学院」 |
| 学校全称 | 只用于命名配置和报告标题 | 你自己填 |

**选填 3 项**：专业名（填了会在报告里多一行「专业匹配」标注）、不提醒名单（默认空）、邮箱与 SMTP 授权码（不发邮件就完全不用管）。

**不用你填，工具自己搞定**：登录 token、活动接口路径与字段结构、学院表（实测拉到 44 个，让「参与对象」显示学院名而不是编号）。

## 30 秒试跑

```bash
python alert.py --demo
```

用内置样例数据跑一遍，不改任何配置，看输出长什么样。会命中 2 场、列出 1 场不能报的并说明原因，报告写到 `outputs/report.html`。

## 实测效果

在已验证的那所高校上跑一次的真实数据：

| 阶段 | 数量 |
|---|---|
| 接口里的活动总数 | 716 |
| 其中还在报名期 | 40 |
| 你校区内、还能报的 | 6 |
| **最终命中（你能报）** | **1** |

（这是一次真实跑的结果，数字会随活动发布和截止变动。）

每条命中活动输出这些字段：

```
【校园歌手大赛节目海选】
  活动时间：2026-10-15 14:00
  地点：学生活动中心内三厅
  主办：校团委（中心校区）
  参与对象：仅限 计算机学院、数学与统计学院
  报名时间：2026-10-13 09:00 至 2026-10-15 13:55
  报名截止：2026-10-15 13:55      ← 这一行标红
  联系人：李同学 138****0001
  链接：https://erke.example.edu.cn/public/index.php/mobile/activity-detail?id=1001
```

（上面是内置样例数据的输出，跑 `python alert.py --demo` 就能看到。）

命中之外，报告还会列出「校区内其他 N 场」并说明每一场为什么你报不了——方便你判断有没有被误杀。

## 两步上手

**Windows**：双击 `run.bat`。**macOS / Linux**：`bash run.sh`。

没配置过就自动进向导，配好了就直接跑——不用记命令。

## 5 分钟接入你的学校

```bash
python configure.py
```

向导开头会列出**前置检查**（装企微、在企微里打开过第二课堂、准备页面链接、准备邮箱授权码），
确认后才开始。然后依次做这些事：

1. 问学校名和平台类型（不确定选「职迅」，大多数高校是它）
2. 让你粘贴第二课堂页面链接，自动算出 `base_url`
3. **自动从企业微信缓存里挖 token**
4. **自动探测活动接口路径**，并把返回 JSON 里活动数组的路径也找出来
5. 拉学院列表让你点选自己的学院，自动拿到 `college_id` 和 `campus_id`
6. 配邮件（可跳过）

生成 `config.json` 和 `schools/<学校>.json`。

**其他学校需要准备哪些数据**，见 [ADAPT.md](ADAPT.md)（完整清单 + 抓包指引）。最少只要 4 样就能跑：页面链接、你的校区和学院名、邮箱授权码、装了企微并打开过第二课堂。也可以直接照着 `schools/_template.json` 填。然后：

```bash
python alert.py                    # 实跑
python alert.py --school example-zhixun      # 指定学校配置
python alert.py -v                 # 详细模式，看每页拉到多少条
```

## 平台支持矩阵

| 平台 | 状态 | 说明 |
|---|---|---|
| 职迅 zhixun（jobsys.cn） | ✅ 已验证 | 域名常为 `erke.<学校>.edu.cn`，示例大学实测通过 |
| 趣拓 qutuo | ⚠️ 模板 | 接口路径是猜的，需抓包校准 |
| 其他/自建 | ⚠️ 模板 | 用 `generic`，抓包后自己填 `api` 和 `filter` |

判断你的学校是哪一种：在企业微信里打开第二课堂 → 复制地址栏域名 → 看是不是 `erke.xxx.edu.cn`。是的话直接选「职迅」。

## 配置字段说明

| 字段 | 作用 |
|---|---|
| `base_url` | 接口前缀，通常是 `https://<域名>/public/index.php` |
| `api.activity_path` | 活动列表接口路径 |
| `api.items_path` | 返回 JSON 里活动数组的点号路径，如 `result.data` |
| `api.last_page_path` | 末页页码路径，用于提前终止翻页 |
| `token.host_hint` | 缓存扫描时用它过滤出本校的 token（填域名） |
| `token.regex` | token 的形状，职迅是 `数字|字母数字` |
| `filter.campus_ids` / `campus_keywords` | 校区 ID 白名单 + 正文关键词兜底 |
| `filter.college_ids` / `college_keywords` | 你的学院 ID 白名单 + 关键词 |
| `filter.audience_keywords` | 「全体新生」这类面向所有人的词 |
| `filter.major_keywords` | 你的专业名，正文出现就算与你相关 |
| `filter.major_exclude_keywords` | 明确限别的专业的短语，命中就剔除（如「限土地资源管理专业」） |
| `filter.exclude_ids` | 不提醒名单（活动 id），命中就彻底不出现 |
| `filter.exclude_keywords` | 不提醒名单（标题关键词），同上 |
| `fields.apply_start` | 报名开始时间字段，用来显示「报名时间 X 至 Y」 |
| `filter.open_status` | 还能报的状态值，职迅是 `ongoing` / `waiting` |
| `links.detail_url` | 活动详情页链接模板，`{base_url}` 和 `{id}` 会自动替换 |
| `email` | SMTP 配置，`to` 是数组，会给每个地址单独发一封 |

## 活动直达链接

报告和邮件里，每条命中活动都会带一个**点开就能报名**的链接，活动标题本身也是可点的。

职迅平台的默认模板已经内置：

```
{base_url}/mobile/activity-detail?id={id}
```

实际效果：`https://erke.example.edu.cn/public/index.php/mobile/activity-detail?id=1001`

**换学校怎么找这个模板**：打开第二课堂页面 → 右键查看源代码 → 找到引入的 JS 包（形如 `app-stu/.../dist/js/*.js`）→ 在里面搜 `activity-detail`，能看到路由定义；再搜 `name:"ActivityDetail"` 附近的 `push({...query:{id:...}})`，就能确认参数名是 `id`。也可以直接用手机打开某个活动，把地址栏链接复制出来，把里面的活动号换成 `{id}`。

### 为什么链接不能直接在浏览器打开

第二课堂走的是**企业微信 OAuth 免登**，所以链接必须在企业微信里点开。用普通浏览器打开只会看到「请在企业微信客户端打开」——这是平台的登录方式决定的，绕不过去。

工具为此做了引导，**不用你手动复制粘贴地址**：

- 打开 `outputs/report.html`，每条命中活动下面有 **「复制链接」按钮**，点一下就复制好
- 把链接粘贴到企业微信的**文件传输助手**（或任意群/好友），再点开，直接进活动页
- 旁边还有「尝试唤起企业微信」，装了桌面端的话可能直接拉起客户端
- 如果你本来就是在企业微信里打开这份报告，提示会自动隐藏，点链接直接跳转

邮件里因为大多数客户端会屏蔽脚本，改成静态提示文案，一样写清楚了怎么操作。

如果你的平台实在拿不到详情页链接，把 `links.detail_url` 删掉即可，报告会退化成不带链接的版本，其余功能不受影响。

## 三个筛选维度

1. **校区**：`campus_ids` + `campus_keywords`
2. **学院 / 参与对象**：`college_ids` + `college_keywords` + `audience_keywords`
3. **专业**：`major_keywords` + `major_exclude_keywords`

专业这一维默认关闭（两项都留空），因为多数学校的第二课堂**没有独立的专业字段**，专业限制只写在活动正文里。

两个词的作用不一样，别搞混：

- `major_keywords`（你的专业名，如「法学」）——**只标注，不剔除**。命中后报告里多一行「专业匹配：专业命中「法学」」，方便你扫一眼就知道这场跟你的专业有关。之所以不过滤，是因为绝大多数活动根本不限专业，硬过滤会把它们全误杀。
- `major_exclude_keywords`（明确限别的专业的短语，如「限土地资源管理专业」）——**真剔除**，正文出现就把这场拿掉。发现有误判就往这里加原话。

### 不提醒名单

有些活动你已经知道了、或者根本不感兴趣，不想每次都看到它：

```json
"filter": {
  "exclude_ids": [1001],
  "exclude_keywords": ["某个不想再看的活动名"]
}
```

命中后**彻底消失**：既不进命中列表，也不进「校区内其他」。运行日志会打印 `不提醒名单屏蔽 N 场`，让你知道被过滤了几条。想恢复，把对应项删掉即可。

**默认是空的，不屏蔽任何活动。** 已经结束的活动也不需要手动屏蔽——`apply_status` 变成 `ended` 后会自动从结果里消失。这个名单只用来处理「我知道了、别再推给我」的情况。

**为什么要关键词兜底**：字段会错。实测遇到过一场节目海选活动，`campus_id` 标成了 1（东校区），但主办方写的是「共青团示例大学委员会（中心校区）」、正文写着「仅限中心校区同学报名」——按内容判定才不会漏。**只信字段会漏活动，这是真实踩过的坑。**

## 定时运行

Windows 计划任务（每 2 天一次）：

```
schtasks /create /tn "第二课堂筛选" /tr "python C:\path\to\alert.py" /sc daily /mo 2 /st 09:00
```

macOS / Linux crontab（每 2 天一次）：

```
0 9 */2 * * cd /path/to/campus-activity-alert && python3 alert.py
```

想改频率就改 `*/2` 里的数字。邮件推送在 `config.json` 里把 `email.enabled` 设为 `true` 即可，每次跑完自动发。

## 排查

工具内置了自检：字段名配错、状态值对不上、连不上服务器，都会直接说清楚原因，不会只丢给你一个「命中 0 场」。

**命中 0 条**

按顺序查：

```bash
python alert.py --scan-token   # 先确认 token 还在
python alert.py --discover     # 确认接口路径对
python alert.py -v             # 看每页拉到几条
```

然后打开 `outputs/activities_raw.json`，看真实字段长什么样，再改 `filter`。最常见的原因是：你们平台的字段名不叫 `campus_id` / `apply_college_limit` / `apply_status`，改掉 `filter.campus_field`、`filter.audience_fields`、`filter.status_field` 即可。

拿到自己平台的原始返回后，可以离线反复试字段，不用联网：

```bash
python alert.py --offline 你的原始数据.json
```

自检会在两种典型误配下直接喊出来：

```
[!] 数据在样本里找不到这些字段：状态 字段「apply_state」、校区 字段「campus」
[!] 没命中。接口里 apply_status 的实际取值是 ongoing、waiting，
    但 open_status 配的是 open、signup —— 状态值对不上也会全军覆没。
```

**连不上服务器**：会明确报错退出，不会假装成「0 条活动」。常见原因是 `base_url` 填错、学校要内网/VPN、或 `api.items_path` 与返回结构对不上——报错里会分别提示。

**token 过期**：读不到就是过期了。在企业微信客户端里重新打开一次第二课堂，再跑就行；如果 `token.txt` 里存的是旧 token，工具会自动清掉重新从企微缓存里扫，不用你手动删文件。工具不会编造数据，只会如实告诉你取不到。

**邮件发不出**：QQ 邮箱要用 **SMTP 授权码**，不是登录密码，在邮箱设置里单独生成。端口 465 / SSL。

## 隐私与安全

- token 只存在本地 `token.txt`，不上传任何服务器
- 代码不采集、不回传任何信息，网络请求只有两个出口：你学校的第二课堂接口、你填的 SMTP 服务器
- 项目根目录已在 `.gitignore` 里排除 `token.txt`、`config.json`、`outputs/`、`__pycache__/`

### ⚠️ 传到 GitHub 之前，先看完这段

`outputs/` 里放的是**接口原始数据**，里面有一大片真实同学的手机号和邮箱——那不是你的隐私，是别人的。`activities_raw.json`、`report.html`、`report.txt` 都不能上传。`.gitignore` 已经挡住了，但如果你用 `git add -f` 或直接拖文件夹上传，就会漏出去。

发布前对着清单过一遍：

```bash
git status --ignored        # 确认 token.txt / config.json / outputs/ 都在忽略列表里
git check-ignore -v token.txt config.json outputs/activities_raw.json
```

要检查的还有这些：

| 位置 | 风险 |
|---|---|
| `token.txt` | 你的登录凭据，**绝不能上传** |
| `config.json` | 你的校区、学院、邮箱、SMTP 授权码 |
| `outputs/` | 全量活动原始数据，含几百个同学的手机号和邮箱 |
| `schools/<你的学校>.json` | 你的校区和学院，能定位到你是谁。不想公开就别放进来 |

本仓库里的示例配置和样例数据都已经做过匿名化处理（示例大学 / `erke.example.edu.cn` / 中心校区 / 计算机学院），不含任何真实凭据。

## 目录结构

```
campus-activity-alert/
  alert.py               主程序：取 token → 拉接口 → 筛选 → 报告 → 发信
  configure.py           交互式适配向导
  config.example.json    配置模板
  ADAPT.md               其他学校适配清单：要准备哪些数据、怎么拿到
  schools/example-zhixun.json      示例大学（已验证；换成你的学校就是 schools/<学校>.json）
  schools/_template.json 空白模板，带每项怎么填的说明
  schools/demo.json      样例配置，供 --demo 使用
  run.bat / run.sh       双击即用：没配置就进向导，配好了直接跑
  sample/                样例数据
  outputs/               运行产物（报告，含接口原始数据，别上传）
  .gitignore             已排除 token.txt / config.json / outputs/
```

## 许可证

MIT
