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
    from datetime import datetime, timezone

    from radar.config import THREAD_STALE_HOURS, WATCHDOG_STALE_HOURS
    from radar.dedupe import filter_new
    from radar.fetch import fetch_all
    from radar.notify import notify_alert, notify_new
    from radar.render import render
    from radar.score import CreditError, score_items, summarize_daily
    from radar.store import (
        connect, delete_items, get_meta, load_items, mark_seen, meta_age_hours,
        prune, set_meta, upsert_items,
    )

    conn = connect()
    alerts: list[str] = []     # "核心付费 API 余额不足"类告警收集器（X 402 会往这里加）
    scoring_failed = False     # 本轮打分是否失败（供看门狗判定健康）
    credit_dead = False        # Anthropic 余额不足 → 跳过入库，不存 fallback junk

    # 1) fetch（逐源已隔离；X 402→alerts）+ 2) dedupe
    try:
        items = fetch_all(conn, alerts)
    except Exception as e:  # noqa: BLE001
        log.error("fetch 整体失败：%s", e)
        items = []
    new = filter_new(conn, items)
    log.info("fetch %d → 新增 %d", len(items), len(new))

    # 3) score + 5) store
    if new:
        try:
            score_items(new)
        except CreditError as e:
            credit_dead = True
            scoring_failed = True
            alerts.append("anthropic_credit")
            log.error("Anthropic 余额不足，跳过本轮入库（不存 junk，留待恢复后重打分）：%s", e)
        except Exception as e:  # noqa: BLE001
            scoring_failed = True
            log.error("打分整体失败：%s", e)
        # 余额不足时【不入库、不标 seen】→ 这些条目下轮重抓、充值后自动正确打分
        if not credit_dead:
            kept = [it for it in new if it.get("signal") != "drop"]
            dropped = [it for it in new if it.get("signal") == "drop"]
            try:
                upsert_items(conn, kept)
                if dropped:
                    delete_items(conn, [it["id"] for it in dropped])
                    mark_seen(conn, [it["id"] for it in dropped])
                    log.info("丢弃噪音 %d 条（真人源无政策实质）", len(dropped))
            except Exception as e:  # noqa: BLE001
                log.error("入库失败：%s", e)

    # 4) 今日主线：有新条目 或 无主线 或 主线已过期(>THREAD_STALE_HOURS) 就重算；余额不足时不试
    if not credit_dead:
        age = meta_age_hours(conn, "daily_thread")
        if new or age is None or age > THREAD_STALE_HOURS:
            try:
                thread = summarize_daily(load_items(conn))
                if thread:
                    set_meta(conn, "daily_thread", thread)
            except CreditError as e:
                credit_dead = True
                scoring_failed = True
                if "anthropic_credit" not in alerts:
                    alerts.append("anthropic_credit")
                log.error("今日主线：Anthropic 余额不足：%s", e)
            except Exception as e:  # noqa: BLE001
                log.error("今日主线生成失败：%s", e)

    # 健康心跳：本轮打分没失败(含"无新条目"的正常安静轮)→ 记 last_healthy_at
    if not scoring_failed:
        set_meta(conn, "last_healthy_at", datetime.now(timezone.utc).isoformat())

    # 5b) 保留期清理：删 >30天旧条目 / >90天 seen（控 DB 膨胀 + 清历史 fallback 垃圾）
    try:
        prune(conn)
    except Exception as e:  # noqa: BLE001
        log.error("prune 失败：%s", e)

    # 6) render
    try:
        log.info("看板已生成：%s", render(conn))
    except Exception as e:  # noqa: BLE001
        log.error("渲染失败：%s", e)

    # 7) notify（本批新 high）
    try:
        log.info("ntfy 推送 %d 条", notify_new(conn, new))
    except Exception as e:  # noqa: BLE001
        log.error("推送失败：%s", e)

    # 8) 健康告警（按 ALERT_COOLDOWN_HOURS 去重）+ 决定是否让本轮明确失败(变红+邮件)
    fail_run = False
    if "anthropic_credit" in alerts:
        if notify_alert(conn, "anthropic_credit", "🔴 Anthropic 余额不足，雷达已停打分",
                        "去 console.anthropic.com 充值后自动恢复"):
            fail_run = True
    if "twitterapi_credit" in alerts:
        if notify_alert(conn, "twitterapi_credit", "🔴 TwitterAPI 余额不足，X 已停抓",
                        "去 twitterapi.io 充值后自动恢复"):
            fail_run = True
    # 看门狗：连续 >WATCHDOG_STALE_HOURS 无成功打分 → 告警（兜底未知的静默故障）
    hage = meta_age_hours(conn, "last_healthy_at")
    if hage is not None and hage > WATCHDOG_STALE_HOURS and not alerts:
        if notify_alert(conn, "watchdog_stale", "⚠️ 雷达可能停更",
                        f"已连续 {hage:.1f}h 没有成功打分，请检查"):
            fail_run = True

    conn.close()
    # 余额不足/停更且本轮确实告警了 → 返回 1 让 Actions 变红+发失败邮件（同样按冷却去重）
    return 1 if fail_run else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s", stream=sys.stderr)
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
