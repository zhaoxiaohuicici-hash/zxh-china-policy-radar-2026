# Build Brief：中美政策「早期信号雷达」（C 模式 · 第①②层）

> 把这份文件整份粘进 **Claude Code**，让它照着搭。目标：**一周内上线、零基础设施成本**。先只跑官方信号 + 智库/Substack（第①②层），X（第③层）预留接口、以后再接。

---

## 1. 目标与范围

做一个**自动运行的看板 + 突发推送**系统：
- 定时拉取一批 RSS / API 信号源 → 去重 → 用 Claude 给每条打「信号分 + 标签 + 一句话为什么重要」→ 存库 → 重新生成看板网页 → 命中「高信号 + 我关心的标签」时推送一条提醒。
- 看板永远显示最新状态、手机可查；推送只在真出大事时触发。
- **本期不接 X**（成本/合规考虑）。代码留好 `sources/x.py` 的空接口，以后加。

**不做**：LinkedIn 自动监测（无可靠合规接口，跳过）。

---

## 2. 架构（全部跑在免费层）

```
GitHub Actions (cron, 每 30 min)
        │
        ▼
  fetch  →  dedupe  →  score (Claude API)  →  store (SQLite/JSON)
        │                                          │
        │                                          ├─► render → docs/index.html ──► GitHub Pages（看板）
        │                                          │
        │                                          └─► notify（高信号才推）──► 邮件 / ntfy / Slack（可切换）
```

- **调度**：GitHub Actions 定时任务（免费额度对个人项目足够）。
- **看板**：每次跑完生成静态 HTML，commit 到仓库的 `docs/`，用 **GitHub Pages** 发布（免费、永远最新、手机能开）。设成 private repo + Pages，或加一个简单口令。
- **推送**：模块化，默认邮件，可一键换 ntfy / Slack。
- **打分**：调用 Anthropic Messages API，用便宜快的 **Haiku 4.5** 模型。

---

## 3. 技术栈

- **语言**：Python 3.11+
- **依赖**：`feedparser`（RSS）、`requests`（Federal Register / Congress API）、`anthropic`（打分）、`pyyaml`（读 sources 配置）、`jinja2`（渲染看板）。存储用标准库 `sqlite3`（或简单 JSON 文件）。
- **托管/调度**：GitHub Actions + GitHub Pages。
- **密钥**：放 GitHub Repo Secrets（见 §9）。

---

## 4. 仓库结构

```
china-policy-radar/
├── sources.yaml                # 所有信号源配置（见 §5）
├── radar/
│   ├── fetch.py                # 拉取各类源，产出统一 Item 列表
│   ├── sources/
│   │   ├── rss.py              # 通用 RSS（智库、Substack、机构 RSS）
│   │   ├── federal_register.py # Federal Register JSON API
│   │   ├── congress.py         # Congress.gov API（法案/听证）
│   │   └── x.py                # 【占位】以后接 X，先留空函数 + TODO
│   ├── dedupe.py               # 基于 item id/url 去重（查 seen 表）
│   ├── score.py                # 调 Claude 打分+打标签+一句话理由
│   ├── store.py                # SQLite：items 表 + seen 表
│   ├── render.py               # Jinja2 → docs/index.html
│   └── notify.py               # 模块化推送（email/ntfy/slack）
├── templates/
│   └── dashboard.html.j2       # 看板模板
├── docs/                       # GitHub Pages 发布目录（生成物）
│   └── index.html
├── .github/workflows/radar.yml # 定时任务（见 §8）
├── requirements.txt
└── README.md
```

**统一 Item 数据结构**（各 source 都产出这个）：
```python
{
  "id": "稳定唯一ID（用url或源id）",
  "source": "Sinocism",            # 来源名
  "source_layer": 2,               # 1=官方 2=智库/Substack 3=X
  "title": "...",
  "summary": "原文摘要/前若干字",
  "url": "https://...",
  "published": "ISO-8601 时间",
  # 以下由 score.py 填充
  "signal": None,                  # high / medium / low
  "tags": [],                      # 见 §7 标签集
  "why": ""                        # 一句话：为什么重要
}
```

---

## 5. sources.yaml（信号源配置）

> 把 `监测清单_v2.md` 里的 feed 填进来。下面是结构示例 + 关键源的具体地址。**让 Claude Code 启动时先逐个验证每个 feed 能否拉通，拉不通的标出来。**

```yaml
# 第①层 · 官方信号（最高优先）
federal_register:
  # 免费 JSON API，无需 key。按机构 + 关键词订阅。
  endpoint: "https://www.federalregister.gov/api/v1/documents.json"
  agencies: ["industry-and-security-bureau", "foreign-assets-control-office",
             "trade-representative-office-of-united-states"]
  terms: ["China", "export control", "entity list", "tariff", "Section 301", "outbound investment"]

congress:
  # Congress.gov API，需免费 key（api.data.gov）。监测新法案/听证。
  endpoint: "https://api.congress.gov/v3"
  watch_bills_terms: ["China", "BIOSECURE", "outbound", "export control", "tariff"]

rss_official:
  - {name: "BIS press", url: "https://www.bis.gov/rss.xml"}            # 验证实际地址
  - {name: "OFAC Recent Actions", url: "https://ofac.treasury.gov/..."} # 验证实际地址
  - {name: "USTR press", url: "https://ustr.gov/rss.xml"}              # 验证实际地址
  - {name: "House Select Committee on the CCP", url: "https://selectcommitteeontheccp.house.gov/..."}
  - {name: "Senate Finance", url: "..."}
  - {name: "House Ways & Means", url: "..."}

# 第②层 · 智库 & Substack（RSS）
rss_analysis:
  # Substack 一律在域名后加 /feed
  - {name: "Sinocism", url: "https://sinocism.com/feed"}
  - {name: "ChinaTalk", url: "https://www.chinatalk.media/feed"}
  - {name: "Trivium China", url: "..."}
  - {name: "Sinification", url: "https://www.sinification.com/feed"}
  - {name: "The Wire China", url: "..."}
  - {name: "PIIE - China", url: "https://www.piie.com/.../rss"}
  - {name: "CSIS - China", url: "https://www.csis.org/.../rss"}
  - {name: "Rhodium Group notes", url: "..."}
  - {name: "CNAS", url: "..."}
  # ……其余按清单补

# 第③层 · X —— 本期不启用
x:
  enabled: false
  # 以后启用时在这里填 handles（见清单第③层），并实现 radar/sources/x.py
```

---

## 6. 各模块要点

- **fetch.py**：遍历 sources.yaml，分派给对应 source 模块；每个源做超时与异常隔离（一个源挂了不影响其他）；返回统一 Item 列表。
- **federal_register.py**：按 agencies × terms 查 API，取最近 N 天；这是「事件源头」，优先级最高。
- **congress.py**：查新法案/动态（BIOSECURE、outbound、关税相关）；需 api.data.gov 免费 key。
- **dedupe.py**：对每个 item 的 id 查 `seen` 表，没见过才进入打分；处理完写入 seen。
- **store.py**：SQLite 两张表——`items`（含打分结果，供看板读取）、`seen`（去重）。保留最近 30 天。
- **render.py**：见 §7 看板设计。
- **notify.py**：见 §9 推送。

---

## 7. Claude 打分（核心）+ 标签 + 看板

### 打分调用
- 模型：`claude-haiku-4-5-20251001`（便宜、快；30 分钟节奏不要用 Batch API，那是 24h 异步的）。
- 端点：`POST https://api.anthropic.com/v1/messages`。
- 把**本批新 item 一起喂进去**（一次请求处理多条，省钱），让模型按统一 JSON 返回。系统提示词保持精简、固定，便于 prompt caching 命中。

### 打分提示词（给 score.py 用，可直接套）
```
你是一个中美政策情报分析助手，服务一位给企业做中美经贸/政策咨询的顾问。
对下面每条内容，判断它对「在事情成为正式新闻之前抢先掌握信号」有多大价值。

输出严格的 JSON 数组，每条对应一个对象，不要任何额外文字：
[{"id": "...", "signal": "high|medium|low", "tags": [...], "why": "一句话(≤20字)为什么重要"}]

signal 评级标准：
- high：记者成稿前的独家/风声("hearing that"/"sources")；官方规则、制裁、关税、实体清单落地；高层会晤/访问团确认；关键人物首次明确表态。
- medium：智库深度分析、政策预判、重要数据更新。
- low：转发、寒暄、旧闻复读、与中美经贸/政策无关。

tags 只能从这个集合里选(可多选)：
["关税","出口管制","制裁金融","华盛顿政治","高层互动","新能源","AI","医药"]

待评内容：
<<这里塞本批 items 的 id/source/title/summary>>
```

### 标签集（与上面一致）
`关税` `出口管制` `制裁金融` `华盛顿政治` `高层互动` `新能源` `AI` `医药`

### 看板（dashboard.html.j2）
- 顶部 **🔴 高信号区**：按时间倒序列出最近的 high。每条显示：来源、时间、标题、一句话 why、原文链接、标签。
- 中部 **按标签筛选**：8 个标签做成可点的 tab/筛选器（纯前端 JS 即可，数据已在页面里）。
- 下部 **全部动态**：medium/low 折叠或弱化展示。
- 顶部显示「最后更新时间」。
- 设计要求：干净、信息密度高、像一个简洁的情报终端；手机宽度友好。**不要用浏览器本地存储**，所有数据直接渲染进页面。

---

## 8. GitHub Actions 定时任务（.github/workflows/radar.yml）

```yaml
name: radar
on:
  schedule:
    - cron: "*/30 * * * *"   # 每 30 分钟
  workflow_dispatch: {}        # 也支持手动触发
permissions:
  contents: write              # 允许提交生成的看板
jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -r requirements.txt
      - run: python -m radar.fetch && python -m radar.render
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          CONGRESS_API_KEY: ${{ secrets.CONGRESS_API_KEY }}
          NOTIFY_CHANNEL: ${{ vars.NOTIFY_CHANNEL }}        # email | ntfy | slack
          # 按所选渠道提供对应 secret（见 §9）
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
          SLACK_WEBHOOK: ${{ secrets.SLACK_WEBHOOK }}
          SMTP_URL: ${{ secrets.SMTP_URL }}
          ALERT_EMAIL: ${{ secrets.ALERT_EMAIL }}
      - name: commit dashboard
        run: |
          git config user.name "radar-bot"
          git config user.email "bot@local"
          git add docs/ && git commit -m "update $(date -u +%FT%TZ)" || echo "no change"
          git push
```
（看板用 GitHub Pages 指向 `docs/` 目录发布。）

---

## 9. 推送：ntfy（你的默认）

`notify.py` 只在 `signal=="high"` 且命中你关注的标签时推送。**已选定 ntfy**（`NOTIFY_CHANNEL=ntfy`，代码默认走这条）：

- **ntfy（默认）**：手机装 ntfy app（iOS/Android 都有），订阅一个**随机难猜**的 topic（例如 `cn-policy-7f3k9q2x`——别人猜不到就等于私有）。推送即 `POST https://ntfy.sh/<你的topic>`。
  - 每条消息：`Title` 头 = 「来源 + 标题」；正文 = 「一句话 why + 标签」；`Click` 头 = 原文链接（点通知直接打开）；`Priority: high`。
  - secret：`NTFY_TOPIC`（= 你那个随机 topic 名）。
- 备选（以后想换）：`email`（SMTP）或 `slack`（Incoming Webhook）——改 `NOTIFY_CHANNEL` 一个变量即可，代码已留好接口。

**需要的 Secrets**：`ANTHROPIC_API_KEY`、`CONGRESS_API_KEY`（api.data.gov 免费申请）、`NTFY_TOPIC`。

---

## 10. 成本预估（C 模式）

- **GitHub Actions / Pages**：个人项目免费额度内。
- **Claude 打分**：用 Haiku 4.5（标准价约 $1 / $5 每百万 tokens 输入/输出）。每天几百条新内容、每条几百 token，**月成本大约个位数到十几美元**。可在 Anthropic 后台设花费上限。
- **其余源**：Federal Register / Congress / RSS 全免费。
- **合计：约 $5–15/月**，无基础设施费。

---

## 11. 以后加第③层（X）

等第①②层跑顺，再加 X：
1. 选取数方式：官方 X API 按量付费（设花费上限）或第三方取数服务（便宜、属灰色地带）。
2. 实现 `radar/sources/x.py`：按清单 handles 拉各账号最新 post，产出同样的 Item 结构（`source_layer: 3`）。
3. sources.yaml 里 `x.enabled: true` 并填 handles。
4. 打分提示词不用改——X 的"hearing that"会自然被判成 high。

---

## 12. 给 Claude Code 的启动指令（把下面整段粘进去开干）

> 先把本文件和 `中美政策早期信号雷达_监测清单_v2.md` 两份都放进项目文件夹，然后粘下面这段：

```text
我要在当前目录搭一个名为 china-policy-radar 的 Python 项目，监测中美经贸/政策的「早期信号」。文件夹里有两份规格：「Claude_Code_搭建说明_中美政策雷达.md」(总体设计) 和「中美政策早期信号雷达_监测清单_v2.md」(信号源清单，末尾有机器可读的 sources 附录)。请先读这两份，再照着搭。

本期只做第①②层：
- ① 官方：Federal Register JSON API(免费无 key，按 v2 附录 federal_register.queries 的关键词拉最近 7 天)；Congress.gov API(需 CONGRESS_API_KEY，监测 congress_api.watch 的关键词法案/听证)；附录 official_rss 的机构 RSS。
- ② 智库/Substack：附录 substacks 与 think_tanks 的 RSS(Substack 一律域名 + /feed)。
- ③ X：只在 radar/sources/x.py 留带 TODO 的空函数，本期不实现。

技术：
- Python 3.11；依赖 feedparser / requests / anthropic / pyyaml / jinja2；存储用标准库 sqlite3(两张表：items 存打分结果、seen 去重)。
- 流水线 fetch(各源 try/except 隔离，一个挂不影响其他) → dedupe(按 url/id 查 seen) → score → store → render → notify。
- 打分：Anthropic Messages API，模型 claude-haiku-4-5-20251001，把本批新内容一次性喂入，返回严格 JSON 数组 [{"id","signal":"high|medium|low","tags":[...],"why":"≤20字"}]；signal/tags 判定标准与标签集用规格里「信号分与标签」那节；系统提示固定精简。
- 看板：jinja2 → docs/index.html。顶部「🔴 高信号区」按时间倒序；8 个标签做成纯前端可点筛选；medium/low 弱化或折叠；显示最后更新时间；干净、信息密度高、手机友好；禁止使用任何浏览器本地存储，数据直接渲染进页面。
- 推送 = ntfy：notify.py 仅当 signal=="high" 且 tags 命中我关注的标签时，POST 到 https://ntfy.sh/${NTFY_TOPIC}；每条带 Title 头(来源+标题)、正文为 why+标签、Click 头放原文链接、Priority: high。我关注的标签先设为全部 8 个，做成可配置常量。
- 调度：.github/workflows/radar.yml，cron 每 30 分钟 + workflow_dispatch；跑完 commit docs/ 回仓库，GitHub Pages 指向 docs/。Secrets：ANTHROPIC_API_KEY、CONGRESS_API_KEY、NTFY_TOPIC。
- 先写一个 validate 脚本：逐个测附录里每个 feed/endpoint 能否拉通，把失效的列出来让我确认后再继续。

工作方式：分模块来，顺序 = 骨架+requirements → fetch+validate → store/dedupe → score → render → notify+Actions；每完成一个就停下让我本地跑通确认再继续。现在从项目骨架 + requirements.txt + validate 脚本开始。
```

---
*配套文件：监测清单_v2.md（信号源与人物清单）。建议先跑第①②层，体验顺手后再接 X。*
