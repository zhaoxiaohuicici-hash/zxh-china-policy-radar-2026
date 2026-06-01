"""第①层 · Congress.gov API（需 CONGRESS_API_KEY，api.data.gov 免费申请）。

监测新法案/动态。Congress.gov v3 的列表端点不支持全文检索，所以做法是：
拉最近 lookback_days 天「有更新」的法案（按 updateDate 倒序），再用
sources.yaml 的 congress_api.watch 关键词在标题/最新动作文本里做客户端过滤。
这样能稳定抓到 BIOSECURE / outbound investment / connected vehicles / China 等。

未设 key 时返回空列表（fetch 流水线会跳过本源，不报错）。

TODO（以后扩）：听证 —— /committee-meeting/{congress} 端点，字段结构不同，
本期先只做法案；需要时按同样的「拉最近 + 关键词过滤」模式补一个 _fetch_hearings。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from radar.config import FETCH_LOOKBACK_DAYS, HTTP_TIMEOUT, USER_AGENT, env
from radar.item import make_item

_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# 法案 type → congress.gov 公开链接 slug
_BILL_SLUG = {
    "HR": "house-bill",
    "S": "senate-bill",
    "HJRES": "house-joint-resolution",
    "SJRES": "senate-joint-resolution",
    "HCONRES": "house-concurrent-resolution",
    "SCONRES": "senate-concurrent-resolution",
    "HRES": "house-resolution",
    "SRES": "senate-resolution",
}


def fetch_congress(cfg: dict) -> list[dict]:
    key = env("CONGRESS_API_KEY")
    if not key:
        # 没 key 不算错误，安静跳过
        return []

    cg = cfg.get("congress_api") or {}
    endpoint = cg["endpoint"].rstrip("/")
    congress = cg.get("congress", 119)
    watch = [w.lower() for w in cg.get("watch", [])]

    since = datetime.now(timezone.utc) - timedelta(days=FETCH_LOOKBACK_DAYS)
    url = f"{endpoint}/bill/{congress}"
    params = {
        "api_key": key,
        "sort": "updateDate+desc",
        "limit": 250,
        "format": "json",
        "fromDateTime": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    r = requests.get(url, params=params, headers=_HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    bills = r.json().get("bills", []) or []

    items: list[dict] = []
    for b in bills:
        title = b.get("title") or ""
        action = (b.get("latestAction") or {}).get("text") or ""
        haystack = f"{title} {action}".lower()
        if watch and not any(term in haystack for term in watch):
            continue

        btype = (b.get("type") or "").upper()
        number = b.get("number")
        items.append(
            make_item(
                id=f"congress:{congress}-{btype}{number}",
                source="Congress.gov",
                source_layer=1,
                title=f"[{btype} {number}] {title}",
                summary=action,
                url=_public_url(congress, btype, number),
                published=b.get("updateDate"),
            )
        )
    return items


def _public_url(congress: int, btype: str, number) -> str:
    slug = _BILL_SLUG.get(btype)
    if not slug or number is None:
        return "https://www.congress.gov/"
    return f"https://www.congress.gov/bill/{congress}th-congress/{slug}/{number}"
