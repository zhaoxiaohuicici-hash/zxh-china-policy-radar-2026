"""第①层 · Federal Register JSON API（免费、无需 key）。

按 sources.yaml 的 federal_register.queries 逐个关键词查最近 lookback_days 天
的文件（BIS 出口管制/实体清单、OFAC 制裁、USTR 301/232、FEOC、1260H 等）。
这是「事件源头」，优先级最高。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from radar.config import HTTP_TIMEOUT, USER_AGENT
from radar.item import make_item

_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# API 返回字段（精简，省流量）
_FIELDS = ["document_number", "title", "abstract", "html_url", "publication_date", "agencies"]


def fetch_federal_register(cfg: dict) -> list[dict]:
    fr = cfg.get("federal_register") or {}
    endpoint = fr["endpoint"]
    lookback = int(fr.get("lookback_days", 7))
    queries = fr.get("queries", [])
    agencies = fr.get("agencies", [])
    gte = (datetime.now(timezone.utc) - timedelta(days=lookback)).strftime("%Y-%m-%d")

    by_doc: dict[str, dict] = {}  # 同一文件可能命中多个关键词，按 document_number 去重
    for term in queries:
        for doc in _query(endpoint, term, gte, agencies):
            num = doc.get("document_number")
            if not num or num in by_doc:
                continue
            names = [a.get("name", "") for a in (doc.get("agencies") or []) if a.get("name")]
            agency_label = ", ".join(names[:2]) + ("…" if len(names) > 2 else "")
            source = f"Federal Register · {agency_label}" if agency_label else "Federal Register"
            by_doc[num] = make_item(
                id=f"fr:{num}",
                source=source,
                source_layer=1,
                title=doc.get("title") or "(无标题)",
                summary=doc.get("abstract") or "",
                url=doc.get("html_url") or "",
                published=doc.get("publication_date"),
            )
    return list(by_doc.values())


def _query(endpoint: str, term: str, gte: str, agencies: list[str]) -> list[dict]:
    params = {
        "per_page": 50,
        "order": "newest",
        "conditions[term]": term,
        "conditions[publication_date][gte]": gte,
        "fields[]": _FIELDS,  # requests 会把 list 展开成重复键
    }
    if agencies:
        params["conditions[agencies][]"] = agencies  # OR 关系，同样展开成重复键
    r = requests.get(endpoint, params=params, headers=_HEADERS, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json().get("results", []) or []
