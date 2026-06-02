"""渲染看板：从 SQLite 读最近条目 → Jinja2 → docs/index.html（浅色编辑/简报风）。

布局：报头 → 今日主线综述 → sticky 多选筛选 → 🔴高信号头条卡片(含 影响/观点来自/涉及)
→ 🟡medium(观点优先) → ⚪low 折叠紧凑行。新鲜度：first_seen 在 24h 内标「新·24h」，
TOP SIGNALS 顶部显示「自上次运行新增 N 条」。**不使用任何浏览器本地存储**。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape

from radar.config import DOCS_DIR, TAGS, TEMPLATES_DIR, person_title, source_type
from radar.store import connect, get_meta, load_items, set_meta

# 真人源 person_type → (展示小标签, 子筛选分组)
PERSON_LABELS = {
    "reporter": ("记者", "reporter"),
    "scholar": ("学者", "scholar"),
    "vc_kol": ("创投", "vcind"),
    "industry": ("产业", "vcind"),
}


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
            # 新鲜度：① 刚进雷达(first_seen 24h 内，生产环境下每轮只有少数新条目)
            #          ② 且事件本身不是数月前的旧事(key_date 推断的事件日不早于 OLD_DAYS)
            ev = _event_date(it.get("key_date"), now)            # key_date 推断的事件日（含年份推断）
            it["_date_old"] = bool(ev and (now - ev).days > OLD_DAYS)   # 事件较早 → 灰色淡化角标
            it["_is_new"] = bool(fs and (now - fs) < timedelta(hours=24)) and not it["_date_old"]
            it["_since_build"] = bool(fs and fs >= threshold)
            if it["_since_build"]:
                new_count += 1
            it["_rel"] = _reltime(it.get("published") or it.get("scored_at"), now)
            st, label = source_type(it.get("source"))
            it["_stype"], it["_stype_label"] = st, label
            it["_headline"] = (it.get("headline") or "").strip() or (it.get("title") or "")
            it["_disp"] = (it.get("synopsis") or "").strip() or (it.get("why") or "").strip()
            pt = it.get("person_type") or ""
            lbl, grp = PERSON_LABELS.get(pt, ("", ""))
            it["_ptlabel"], it["_ptgroup"] = lbl, grp
            it["_title"] = person_title(it.get("source") or "", pt)
            # 是否「人物发言」：layer3 真人(govt 除外 → 仍算官方)
            it["_is_person"] = it.get("source_layer") == 3 and grp != ""
            # 来源标签：人物用「💬观点+头衔」(模板里拼)；govt=官方；其余按 _stype
            if it["_is_person"]:
                it["_srcbadge"] = "💬观点"
            elif pt == "govt":
                it["_srcbadge"], it["_stype"] = "官方", "gov"
            else:
                it["_srcbadge"] = label

        # 按【内容性质】切分，不按信号混排：
        #   机构信息 = 非个人发言（layer1/2 + layer3 的 govt）→ ②③⑤ 按信号
        #   个人发言 = _is_person（X/Bluesky 的 reporter/scholar/vc_kol/industry）→ ④ 内部按信号分组
        for it in items:
            it["_aid"] = ""
        inst = [i for i in items if not i["_is_person"]]
        high = [i for i in inst if i["signal"] == "high"]               # ② 机构 high（展开）
        medium = [i for i in inst if i["signal"] == "medium"]           # ③ 机构 medium（收起）
        low = [i for i in inst if i["signal"] not in ("high", "medium")]  # ⑤ 机构 low（收起）

        people = [i for i in items if i["_is_person"]]
        people_high = [i for i in people if i["signal"] == "high"]      # ④高（展开，全卡）
        people_med = [i for i in people if i["signal"] == "medium"]     # ④中（收起，全卡）
        people_low = [i for i in people if i["signal"] not in ("high", "medium")]  # ④低（收起，瘦卡）

        # 锚点 id：给所有"全卡"分配（机构 high/medium + 人物 high/medium）
        for n, it in enumerate(high + medium + people_high + people_med):
            it["_aid"] = f"sig-{n}"
        # 今日主线下钻 chips → 机构高信号卡
        thread_anchors = [{"aid": it["_aid"], "label": (it["_headline"] or "")[:16]} for it in high][:6]

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "j2"]),
        )
        html = env.get_template("dashboard.html.j2").render(
            tags=TAGS,
            high=high,
            medium=medium,
            people_high=people_high,
            people_med=people_med,
            people_low=people_low,
            people_total=len(people),
            low=low,
            total=len(items),
            new_count=new_count,
            daily_thread=daily_thread,
            thread_anchors=thread_anchors,
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


OLD_DAYS = 60     # 事件日(key_date 推断)早于这么多天 → 标「旧闻」、不标「新」

_MD = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")
_MD_CN = re.compile(r"(\d{1,2})月(?:(\d{1,2})日)?")


def _event_date(key_date: str | None, now: datetime) -> datetime | None:
    """从 key_date 抽事件日（M/D 或 M月[D日]），并做年份推断：
    若按今年落在远未来(>45天)，多半是去年的过去事件(如"9/5 签署")→ 回退一年。"""
    if not key_date:
        return None
    mm = dd = None
    m = _MD.search(key_date)
    if m:
        mm, dd = int(m.group(1)), int(m.group(2))
    else:
        m = _MD_CN.search(key_date)
        if m:
            mm, dd = int(m.group(1)), int(m.group(2) or 1)
    if not mm:
        return None
    try:
        cand = datetime(now.year, mm, dd, tzinfo=timezone.utc)
    except ValueError:
        return None
    if (cand - now).days > 45:           # 不合理的远未来 → 实为去年的过去事件
        cand = cand.replace(year=now.year - 1)
    return cand


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
