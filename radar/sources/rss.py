"""通用 RSS 源：官方机构 RSS、Substack、智库 RSS 都走这里。

每个 feed 独立抓取 + 解析，产出统一 Item 列表。异常向上抛，由 fetch.py
做 try/except 隔离（一个 feed 挂不影响其他）。
"""
from __future__ import annotations

import feedparser
import requests

from radar.config import HTTP_TIMEOUT, MAX_ITEMS_PER_FEED, USER_AGENT
from radar.item import make_item, struct_to_iso, within_lookback

_HEADERS = {"User-Agent": USER_AGENT, "Accept": "*/*"}


def fetch_feed(name: str, url: str, source_layer: int, lookback_days: int) -> list[dict]:
    """抓取并解析单个 RSS/Atom feed，返回最近 lookback_days 天的 Item。"""
    r = requests.get(url, headers=_HEADERS, timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    parsed = feedparser.parse(r.content)

    items: list[dict] = []
    for entry in parsed.entries[:MAX_ITEMS_PER_FEED]:
        link = entry.get("link") or ""
        # 稳定 id：优先 feed 提供的 guid/id，其次链接
        uid = entry.get("id") or link
        if not uid:
            continue
        published = struct_to_iso(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        if not within_lookback(published, lookback_days):
            continue
        summary = entry.get("summary") or entry.get("description") or ""
        items.append(
            make_item(
                id=f"{name}:{uid}",
                source=name,
                source_layer=source_layer,
                title=entry.get("title") or "(无标题)",
                summary=summary,
                url=link,
                published=published,
            )
        )
    return items
