"""端到端流水线：fetch → dedupe → score → 今日主线 → store → render → notify。

每步异常隔离：任一源/调用失败都只记日志、不中断整体（fetch 内部已逐源隔离、
score 逐批隔离）。GitHub Actions 每 30 分钟跑一次本模块。

    python -m radar.run
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("radar.run")


def run() -> int:
    from radar.dedupe import filter_new
    from radar.fetch import fetch_all
    from radar.notify import notify_new
    from radar.render import render
    from radar.score import score_items, summarize_daily
    from radar.store import (
        connect, delete_items, get_meta, load_items, mark_seen, set_meta, upsert_items,
    )

    conn = connect()

    # 1) fetch（逐源已隔离；conn 供 X 节流）+ 2) dedupe
    try:
        items = fetch_all(conn)
    except Exception as e:  # noqa: BLE001
        log.error("fetch 整体失败：%s", e)
        items = []
    new = filter_new(conn, items)
    log.info("fetch %d → 新增 %d", len(items), len(new))

    # 3) score（逐批已隔离）+ 5) store
    if new:
        try:
            score_items(new)
        except Exception as e:  # noqa: BLE001
            log.error("打分整体失败：%s", e)
        kept = [it for it in new if it.get("signal") != "drop"]
        dropped = [it for it in new if it.get("signal") == "drop"]
        try:
            upsert_items(conn, kept)
            if dropped:
                # 噪音：不入库展示；删掉可能已存的旧版本，但标记 seen 避免重复打分
                delete_items(conn, [it["id"] for it in dropped])
                mark_seen(conn, [it["id"] for it in dropped])
                log.info("丢弃噪音 %d 条（真人源无政策实质）", len(dropped))
        except Exception as e:  # noqa: BLE001
            log.error("入库失败：%s", e)

    # 4) 今日主线（仅在有新条目或尚无主线时重算，省 token）
    if new or not get_meta(conn, "daily_thread"):
        try:
            thread = summarize_daily(load_items(conn))
            if thread:
                set_meta(conn, "daily_thread", thread)
        except Exception as e:  # noqa: BLE001
            log.error("今日主线生成失败：%s", e)

    # 6) render
    try:
        path = render(conn)
        log.info("看板已生成：%s", path)
    except Exception as e:  # noqa: BLE001
        log.error("渲染失败：%s", e)

    # 7) notify（只推本批新 high）
    try:
        n = notify_new(conn, new)
        log.info("ntfy 推送 %d 条", n)
    except Exception as e:  # noqa: BLE001
        log.error("推送失败：%s", e)

    conn.close()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s", stream=sys.stderr)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
