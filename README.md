# china-policy-radar

中美经贸/政策「早期信号雷达」— 自动拉取官方 + 智库/Substack 信号源，用 Claude 打分打标签，生成看板并在高信号时推送（ntfy）。

本期只做 **第①②层**（官方 API/RSS + 智库/Substack RSS）；第③层 X 预留接口、暂不实现。

## 流水线

```
fetch → dedupe → score (Claude Haiku) → 今日主线 → store (SQLite) → render (docs/index.html) → notify (ntfy)
```

一条命令端到端：`python -m radar.run`（各步异常隔离，任一源/调用失败不中断整体）。

## 本地起步

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 0) 把密钥填进 .env（config.py 启动自动加载）
# 1) 校验所有信号源能否拉通（失效的会列出来）
python validate.py
# 2) 跑一次完整流水线（本地写 radar.local.db + docs_local/index.html，均已 gitignore）
python -m radar.run
# 3) 单独测 ntfy 推送链路
python -m radar.notify --test
# 4) 只重渲染看板（本地预览开 docs_local/index.html）
python -m radar.render
```

> **本地 vs CI 的状态文件**：`radar.db` 与 `docs/` 是 **CI 专属产物**（只有 GitHub Actions 读写并提交）。
> 本地运行自动改用 `radar.local.db` 与 `docs_local/`（见 `config.py` 的 `IS_CI` 判断，均已 gitignore），
> 所以**本地改代码 / push 永远不会碰仓库的 `radar.db` / `docs/`，不再有这两个文件的合并冲突**。
> 本地预览看板请开 `docs_local/index.html`；线上看板由 CI 生成在 `docs/index.html`。
> 想让本地也连真实库可设环境变量 `RADAR_DB=/path/to.db`。

## 上线（GitHub Actions + Pages）

1. 推到 GitHub 仓库。
2. Settings → Secrets and variables → Actions 加三个 secret：`ANTHROPIC_API_KEY`、`CONGRESS_API_KEY`、`NTFY_TOPIC`。
3. Settings → Pages → Source 指向 `main` 分支的 `/docs` 目录。
4. workflow 每 30 分钟自动跑（也可 Actions 页手动 `workflow_dispatch`），跑完把 `docs/` 与 `radar.db` commit 回仓库。
5. 手机装 ntfy app，订阅你的 `NTFY_TOPIC`。

> `radar.db` 故意纳入版本控制：它保存 seen(去重)/pushed(防重复推送)/meta(今日主线、上次 build 时间)
> 等跨运行状态，CI 无状态环境靠提交它来持续去重、不重复轰炸。

## 环境变量 / Secrets

| 名称 | 用途 | 必需 |
|---|---|---|
| `ANTHROPIC_API_KEY` | 打分（score.py） | 是（score 阶段） |
| `CONGRESS_API_KEY` | Congress.gov API（api.data.gov 免费申请） | 是（congress 源） |
| `NTFY_TOPIC` | ntfy 推送 topic（随机难猜的字符串） | 是（推送） |

## 进度

- [x] 项目骨架 + requirements.txt + validate
- [x] fetch（各源，逐源隔离）
- [x] store / dedupe（SQLite：items / seen / meta）
- [x] score（Haiku：signal/rumor/tags/headline/summary/impact/author/involved）
- [x] 今日主线综述
- [x] render（浅色编辑风看板 + 新鲜度）
- [x] notify(ntfy) + run 端到端 + GitHub Actions
- [ ] 第③层 X（预留接口，未来再接）

## 目录

```
sources.yaml                 信号源配置（来自监测清单_v2.md 附录）
radar/
  config.py                  路径 / 标签集 / 模型 / 推送阈值 常量
  sources/x.py               第③层 X 占位（TODO）
validate.py                  逐源连通性校验
templates/ docs/ .github/    模板 / Pages 发布物 / 调度（后续模块填充）
```
