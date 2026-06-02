"""第三层 · Bluesky（人的实时观点）。

用 Bluesky 免费公开 AppView API（读取公开帖子无需登录鉴权）：
  - base: https://public.api.bsky.app/xrpc
  - app.bsky.actor.searchActors      搜人（解析 handle/DID，建清单时用）
  - app.bsky.feed.getAuthorFeed      取某人最近帖子

sources.yaml 里 bluesky.accounts 是【已人工核实匹配】的账号清单。每人拉最近
lookback_days 天、至多 max_per_account 条，产出统一 Item：source_layer=3、
author=本人、url=帖子网页链接。默认过滤转发(repost)；回复是否保留可配置。

每个账号独立 try/except，一个挂不影响其他。
"""
from __future__ import annotations

import logging

import requests

from radar.config import HTTP_TIMEOUT, USER_AGENT
from radar.item import clean_text, make_item, within_lookback

log = logging.getLogger("radar.bluesky")

BASE = "https://public.api.bsky.app/xrpc"
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}


def fetch_bluesky(cfg: dict) -> list[dict]:
    bs = cfg.get("bluesky") or {}
    if not bs.get("enabled"):
        return []
    accounts = bs.get("accounts") or []
    lookback = int(bs.get("lookback_days", 7))
    max_per = int(bs.get("max_per_account", 8))
    drop_reposts = bs.get("drop_reposts", True)
    drop_replies = bs.get("drop_replies", False)

    items: list[dict] = []
    for acc in accounts:
        name = acc.get("name", "?")
        handle = acc.get("handle") or acc.get("did")
        ptype = acc.get("person_type", "")
        if not handle:
            continue
        try:
            got = _fetch_one(name, handle, lookback, max_per, drop_reposts, drop_replies, ptype)
            items.extend(got)
            log.info("bluesky/%-22s %2d 条", name, len(got))
        except Exception as e:  # noqa: BLE001 — 单账号失败不连累其他
            log.error("bluesky/%s 失败: %s", name, e)
    return items


def _fetch_one(name, handle, lookback, max_per, drop_reposts, drop_replies, ptype="") -> list[dict]:
    params = {
        "actor": handle,
        "limit": 30,  # 多取些再按时间窗/过滤裁剪
        "filter": "posts_no_replies" if drop_replies else "posts_with_replies",
    }
    r = requests.get(
        f"{BASE}/app.bsky.feed.getAuthorFeed", params=params, headers=_HEADERS, timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    feed = r.json().get("feed", []) or []

    out: list[dict] = []
    for entry in feed:
        if drop_reposts and entry.get("reason"):  # reasonRepost
            continue
        post = entry.get("post") or {}
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        if not text:
            continue
        created = record.get("createdAt")
        if not within_lookback(created, lookback):
            continue

        uri = post.get("uri") or ""
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        post_handle = (post.get("author") or {}).get("handle") or handle
        url = f"https://bsky.app/profile/{post_handle}/post/{rkey}" if rkey else ""

        it = make_item(
            id=f"bsky:{uri}",
            source=name,                 # 显示为本人名字 → source_type 归「观点」
            source_layer=3,
            title=clean_text(text, limit=120),
            summary=text,
            url=url,
            published=created,
        )
        it["author"] = name             # 预置作者，打分时作为 hint 并兜底
        it["person_type"] = ptype       # 供「人在说什么」板块分类 + 打分降噪
        out.append(it)
        if len(out) >= max_per:
            break
    return out
