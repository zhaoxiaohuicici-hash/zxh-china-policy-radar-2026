"""去重：把本批 Item 里「已见过」的剔掉，只放行新条目去打分。

按 item 的 id 查 seen 表（id 已是稳定唯一键：url 或源 id）。本步只读不写——
真正登记进 seen 发生在 store.upsert_items（打分入库后），这样若打分阶段中途
失败，未入库的条目下一轮还会被当成新条目重试。
"""
from __future__ import annotations

import sqlite3

from radar.store import get_seen_ids


def filter_new(conn: sqlite3.Connection, items: list[dict]) -> list[dict]:
    """返回 items 中未在 seen 表出现过的子集（保持原顺序）。

    同时去掉本批内 id 重复的条目（fetch 已做一次，这里兜底）。
    """
    seen_db = get_seen_ids(conn, [it["id"] for it in items])
    out: list[dict] = []
    seen_batch: set[str] = set()
    for it in items:
        _id = it["id"]
        if _id in seen_db or _id in seen_batch:
            continue
        seen_batch.add(_id)
        out.append(it)
    return out
