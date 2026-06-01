"""渲染看板：从 SQLite 读最近条目 → Jinja2 → docs/index.html（浅色编辑/简报风）。

布局：报头 → 今日主线综述 → sticky 多选筛选 → 🔴高信号头条卡片(含 影响/观点来自/涉及)
→ 🟡medium(观点优先) → ⚪low 折叠紧凑行。新鲜度：first_seen 在 24h 内标「新·24h」，
TOP SIGNALS 顶部显示「自上次运行新增 N 条」。**不使用任何浏览器本地存储**。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar.config import DOCS_DIR, TAGS, TEMPLATES_DIR, source_type
from radar.store import connect, get_meta, load_items, set_meta


def render(conn=None) -> str:
    close = conn is None
    if conn is None:
        conn = connect()
    try:
        items = load_items(conn)
        daily_thread = get_meta(conn, "daily_thread") or ""
        prev_build = get_meta(conn, "last_build_at")

        now = datetime.now(timezone.utc)
        # 「自上次运行」基准：有上次 build 用之，否则回退 24h（首次运行也有意义的 N）
        threshold = _parse(prev_build) or (now - timedelta(hours=24))

        new_count = 0
        for it in items:
            fs = _parse(it.get("first_seen"))
            it["_is_new"] = bool(fs and (now - fs) < timedelta(hours=24))
            it["_since_build"] = bool(fs and fs >= threshold)
            if it["_since_build"]:
                new_count += 1
            it["_rel"] = _reltime(it.get("published") or it.get("scored_at"), now)
            st, label = source_type(it.get("source"))
            it["_stype"], it["_stype_label"] = st, label
            it["_headline"] = (it.get("headline") or "").strip() or (it.get("title") or "")
            it["_disp"] = (it.get("synopsis") or "").strip() or (it.get("why") or "").strip()

        high = [i for i in items if i["signal"] == "high"]
        medium = [i for i in items if i["signal"] == "medium"]
        low = [i for i in items if i["signal"] not in ("high", "medium")]
        medium.sort(key=lambda it: 0 if it["_stype"] == "view" else 1)  # 观点优先

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
        )
        html = env.get_template("dashboard.html.j2").render(
            tags=TAGS,
            high=high,
            medium=medium,
            low=low,
            total=len(items),
            new_count=new_count,
            daily_thread=daily_thread,
            generated_full=now.strftime("%Y-%m-%d · %H:%M UTC"),
        )

        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        out = DOCS_DIR / "index.html"
        out.write_text(html, encoding="utf-8")

        # 本次 build 时间戳，供下次计算「自上次运行新增」
        set_meta(conn, "last_build_at", now.isoformat())
    finally:
        if close:
            conn.close()
    return str(out)


def _parse(iso: str | None) -> datetime | None:
    from radar.item import parse_iso  # 兼容 'Z' 后缀
    return parse_iso(iso)


def _reltime(iso: str | None, now: datetime) -> str:
    dt = _parse(iso)
    if not dt:
        return "—"
    secs = max(0, (now - dt).total_seconds())
    if secs < 3600:
        m = int(secs // 60)
        return "刚刚" if m < 1 else f"{m} 分钟前"
    if secs < 86400:
        return f"{int(secs // 3600)} 小时前"
    days = int(secs // 86400)
    return f"{days} 天前" if days <= 13 else dt.strftime("%m-%d")


def main() -> int:
    print(f"看板已生成：{render()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
