"""拉取调度：遍历 sources.yaml，分派给各 source 模块，产出统一 Item 列表。

关键约束：**每个源 try/except 隔离**——任何一个 feed/endpoint 挂掉只记一条
错误日志，不影响其他源。第③层 X（x.enabled=false）直接跳过。

独立运行做连通性/产出自检（此时还没接 store/score）：
    python -m radar.fetch          # 打印各源条数汇总 + 抽样
"""
from __future__ import annotations

import logging
import sys

from radar.config import FETCH_LOOKBACK_DAYS, RSS_GROUPS, load_sources
from radar.sources.bluesky import fetch_bluesky
from radar.sources.congress import fetch_congress
from radar.sources.federal_register import fetch_federal_register
from radar.sources.rss import fetch_feed
from radar.sources.x import fetch_x

log = logging.getLogger("radar.fetch")


def fetch_all(conn=None) -> list[dict]:
    """运行所有启用的源，返回去重（批内）后的 Item 列表。

    conn 用于 X 源的成本节流（meta.last_x_run）；为 None 时 X 不节流（独立测试用）。
    """
    cfg = load_sources()
    items: list[dict] = []

    # ① Federal Register（内部已按 document_number 去重）
    _run("federal_register", lambda: fetch_federal_register(cfg), items)

    # ① Congress（无 key 时返回空）
    _run("congress", lambda: fetch_congress(cfg), items)

    # ①② 各 RSS 组：逐 feed 隔离
    for grp in RSS_GROUPS:
        key, layer = grp["key"], grp["layer"]
        for feed in cfg.get(key) or []:
            name = feed.get("name", "?")
            url = feed.get("url", "")
            _run(
                f"{key}/{name}",
                lambda n=name, u=url, l=layer: fetch_feed(n, u, l, FETCH_LOOKBACK_DAYS),
                items,
            )

    # ③ Bluesky（人的实时观点）
    _run("bluesky", lambda: fetch_bluesky(cfg), items)

    # ③ X via TwitterAPI.io（带成本节流，需 conn 才生效）
    _run("x", lambda: fetch_x(cfg, conn), items)

    return _dedupe_batch(items)


def _run(label: str, fn, sink: list[dict]) -> None:
    """执行单个源，异常隔离 + 计数日志。"""
    try:
        got = fn() or []
        sink.extend(got)
        log.info("%-28s %3d 条", label, len(got))
    except Exception as e:  # noqa: BLE001 — 故意吞掉单源异常，保证其他源继续
        log.error("%-28s 失败: %s", label, e)


def _dedupe_batch(items: list[dict]) -> list[dict]:
    """同一批内按 id 去重（跨源极少撞，但 FR 多关键词命中已在源内处理）。"""
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append(it)
    return out


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-5s %(message)s",
        stream=sys.stderr,
    )
    from radar.store import connect
    conn = connect()
    items = fetch_all(conn)
    conn.close()

    # 汇总：按 source 计数
    by_source: dict[str, int] = {}
    for it in items:
        by_source[it["source"]] = by_source.get(it["source"], 0) + 1

    print("\n" + "=" * 60)
    print(f"共 {len(items)} 条（批内去重后）。按来源：")
    for src, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}  {src}")

    # 抽样看几条，确认字段填得对
    print("\n抽样（前 5 条）：")
    for it in items[:5]:
        print(f"  · [L{it['source_layer']}] {it['source']} | {it['published']}")
        print(f"    {it['title'][:90]}")
        print(f"    {it['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
