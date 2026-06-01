#!/usr/bin/env python3
"""逐个校验 sources.yaml 里每个 feed / endpoint 能否拉通。

用途：首跑（以及之后改动源清单后）跑一遍，确认哪些源可用、哪些失效，
失效的列出来人工确认/修正后，再继续搭后面的模块。

  python validate.py

退出码：全部通过 0；有失效源 1。
不依赖项目其它模块（除 radar.config 读路径/常量），可独立运行。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import feedparser
import requests

from radar.config import (
    HTTP_TIMEOUT,
    RSS_GROUPS,
    USER_AGENT,
    env,
    load_sources,
)

# 终端颜色（非 TTY 自动降级为无色）
_TTY = sys.stdout.isatty()
GREEN = "\033[32m" if _TTY else ""
RED = "\033[31m" if _TTY else ""
YEL = "\033[33m" if _TTY else ""
DIM = "\033[2m" if _TTY else ""
RST = "\033[0m" if _TTY else ""

OK = f"{GREEN}OK  {RST}"
FAIL = f"{RED}FAIL{RST}"
WARN = f"{YEL}WARN{RST}"

HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}

results: list[tuple[str, str, str, str]] = []  # (status, group, name, detail)


def record(status: str, group: str, name: str, detail: str = "") -> None:
    results.append((status, group, name, detail))
    line = f"  [{status}] {name}"
    if detail:
        line += f"  {DIM}{detail}{RST}"
    print(line)


# ── 校验：Federal Register ────────────────────────────────────
def check_federal_register(cfg: dict) -> None:
    print(f"\n{group_title('federal_register (官方 API · 无需 key)')}")
    fr = cfg.get("federal_register") or {}
    endpoint = fr.get("endpoint")
    lookback = int(fr.get("lookback_days", 7))
    gte = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")
    queries = fr.get("queries", [])
    if not endpoint or not queries:
        record(FAIL, "federal_register", "federal_register", "endpoint/queries 缺失")
        return
    for term in queries:
        params = {
            "per_page": 1,
            "order": "newest",
            "conditions[term]": term,
            "conditions[publication_date][gte]": gte,
            "fields[]": "document_number",
        }
        try:
            r = requests.get(endpoint, params=params, headers=HEADERS, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            count = data.get("count", 0)
            record(OK, "federal_register", f'query="{term}"', f"近{lookback}天 count={count}")
        except Exception as e:
            record(FAIL, "federal_register", f'query="{term}"', short_err(e))


# ── 校验：Congress.gov ────────────────────────────────────────
def check_congress(cfg: dict) -> None:
    print(f"\n{group_title('congress_api (官方 API · 需 CONGRESS_API_KEY)')}")
    cg = cfg.get("congress_api") or {}
    endpoint = cg.get("endpoint")
    key = env("CONGRESS_API_KEY")
    if not key:
        record(WARN, "congress_api", "congress_api", "未设 CONGRESS_API_KEY，跳过（申请：api.data.gov）")
        return
    if not endpoint:
        record(FAIL, "congress_api", "congress_api", "endpoint 缺失")
        return
    # 用 bill 列表做连通性 + key 有效性校验
    url = f"{endpoint.rstrip('/')}/bill"
    try:
        r = requests.get(
            url,
            params={"api_key": key, "limit": 1, "format": "json"},
            headers=HEADERS,
            timeout=HTTP_TIMEOUT,
        )
        if r.status_code in (401, 403):
            record(FAIL, "congress_api", "congress_api", f"key 无效/无权限 HTTP {r.status_code}")
            return
        r.raise_for_status()
        data = r.json()
        n = len((data.get("bills") or []))
        record(OK, "congress_api", "congress_api", f"key 有效，bill 列表返回 {n} 条样本")
    except Exception as e:
        record(FAIL, "congress_api", "congress_api", short_err(e))


# ── 校验：RSS（official_rss / substacks / think_tanks）─────────
def check_rss_group(cfg: dict, key: str, label: str) -> None:
    print(f"\n{group_title(label)}")
    feeds = cfg.get(key) or []
    if not feeds:
        record(WARN, key, key, "该组为空")
        return
    for feed in feeds:
        name = feed.get("name", "?")
        url = feed.get("url", "")
        check_one_feed(key, name, url)


def check_one_feed(group: str, name: str, url: str) -> None:
    if not url:
        record(FAIL, group, name, "url 缺失")
        return
    try:
        r = requests.get(url, headers=HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
    except Exception as e:
        record(FAIL, group, name, f"{url}  {short_err(e)}")
        return

    if r.status_code != 200:
        record(FAIL, group, name, f"HTTP {r.status_code}  {url}")
        return

    parsed = feedparser.parse(r.content)
    n = len(parsed.entries)
    ctype = r.headers.get("Content-Type", "").split(";")[0]
    if n > 0:
        record(OK, group, name, f"{n} 条  ({ctype})")
    elif looks_like_feed(parsed, ctype):
        # 能解析成 feed 但暂时没条目（少见但可能）
        record(WARN, group, name, f"可解析但 0 条目  ({ctype})  {url}")
    else:
        # 多半是返回了 HTML 页面而非 RSS：地址需确认
        record(FAIL, group, name, f"非 feed/0 条目（疑似 HTML 页）({ctype})  {url}")


def looks_like_feed(parsed, ctype: str) -> bool:
    if parsed.get("version"):
        return True
    if "xml" in ctype or "rss" in ctype or "atom" in ctype:
        return True
    return bool(parsed.feed.get("title"))


# ── 工具 ──────────────────────────────────────────────────────
def group_title(s: str) -> str:
    return f"{DIM}── {s} {'─' * max(0, 48 - len(s))}{RST}"


def short_err(e: Exception) -> str:
    msg = str(e)
    return (msg[:120] + "…") if len(msg) > 121 else msg


def main() -> int:
    print("校验 sources.yaml 中所有 feed / endpoint …")
    cfg = load_sources()

    check_federal_register(cfg)
    check_congress(cfg)
    for grp in RSS_GROUPS:
        check_rss_group(cfg, grp["key"], grp["label"])

    # ── 汇总 ──
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    oks = [r for r in results if r[0] == OK]
    print("\n" + "=" * 60)
    print(f"汇总：{GREEN}{len(oks)} OK{RST} · {YEL}{len(warns)} WARN{RST} · {RED}{len(fails)} FAIL{RST}")

    if fails:
        print(f"\n{RED}失效（请确认/修正 sources.yaml 后再继续）：{RST}")
        for _, group, name, detail in fails:
            print(f"  - [{group}] {name}  {DIM}{detail}{RST}")
    if warns:
        print(f"\n{YEL}需注意：{RST}")
        for _, group, name, detail in warns:
            print(f"  - [{group}] {name}  {DIM}{detail}{RST}")

    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
