"""统一 Item 数据结构 + 各源共用的小工具（时间解析、HTML 清洗）。

各 source 模块都产出同一形状的 dict，便于后续 dedupe / score / store 统一处理：

    {
      "id": 稳定唯一ID（用 url 或源 id）,
      "source": 来源名,
      "source_layer": 1=官方 2=智库/Substack 3=X,
      "title": ...,
      "summary": 原文摘要/前若干字,
      "url": ...,
      "published": ISO-8601 或 None,
      # 以下由 score.py 填充
      "signal": None,      # high / medium / low
      "tags": [],          # 见 config.TAGS
      "why": "",           # 一句话：为什么重要
    }
"""
from __future__ import annotations

import calendar
import html
import re
import time
from datetime import datetime, timezone

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def make_item(
    *,
    id: str,
    source: str,
    source_layer: int,
    title: str,
    summary: str,
    url: str,
    published: str | None,
) -> dict:
    return {
        "id": id,
        "source": source,
        "source_layer": source_layer,
        "title": (title or "").strip(),
        "summary": clean_text(summary),  # 原文摘录（喂给打分用）
        "url": url,
        "published": published,
        # 以下由 score.py 填充
        "signal": None,
        "tags": [],
        "headline": "",   # Claude 编辑化中文标题（展示用，原 title 仅留 url）
        "synopsis": "",   # 摘要：发生了什么 / 核心论点（2-3 句 / low 1 句）
        "impact": "",     # 影响：so what，影响谁 + 接下来看什么
        "author": "",     # 观点来自：本条作者/发布机构/署名记者
        "involved": [],   # 涉及：被讲的人/机构/国家
        "rumor": False,   # 未证实/未定调传闻
        "why": "",        # 一句话理由（保留兼容）
    }


def clean_text(s: str | None, limit: int = 600) -> str:
    """去 HTML 标签、反转义实体、压缩空白、截断。"""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s).strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s


def struct_to_iso(st: time.struct_time | None) -> str | None:
    """feedparser 的 *_parsed（UTC struct_time）→ ISO-8601 字符串。"""
    if not st:
        return None
    try:
        ts = calendar.timegm(st)
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except Exception:
        return None


def parse_iso(s: str | None) -> datetime | None:
    """解析 ISO-8601；兼容 Bluesky 的 'Z' 后缀（Py3.9 的 fromisoformat 不认 Z）。"""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def within_lookback(published_iso: str | None, days: int) -> bool:
    """published 在最近 days 天内则 True；无法解析时间的一律保留（True）。"""
    if not published_iso:
        return True
    dt = parse_iso(published_iso)
    if dt is None:
        return True
    age = datetime.now(timezone.utc) - dt
    return age.total_seconds() <= days * 86400


# 向后兼容别名（render 也复用）
_parse_iso = parse_iso
