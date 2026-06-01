"""推送：ntfy。仅当 signal==high 且命中关注标签、且非传闻、且未推过时才推。

每条 ntfy 消息：Title=headline、正文=impact(没有就 summary)、Click=原文链接、
Priority=high；rumor=true 的不推（避免传闻打扰）；同一条只推一次（items.pushed 去重）。

用 ntfy 的 JSON 发布方式（POST 到根 URL、topic 放在 JSON 里），这样中文标题走
请求体 UTF-8，不会像 HTTP 头那样被 latin-1 编码弄乱。
"""
from __future__ import annotations

import logging

import requests

from radar.config import HTTP_TIMEOUT, USER_AGENT, WATCHED_TAGS, env
from radar.store import is_pushed, mark_pushed

log = logging.getLogger("radar.notify")

NTFY_BASE = "https://ntfy.sh"
PRIORITY_HIGH = 4  # ntfy: 5=max 4=high 3=default 2=low 1=min


def notify_new(conn, items: list[dict]) -> int:
    """对本批新条目里符合条件的 high 信号推 ntfy，返回成功推送条数。"""
    topic = env("NTFY_TOPIC")
    if not topic:
        log.warning("未设 NTFY_TOPIC，跳过推送")
        return 0
    watched = set(WATCHED_TAGS)
    sent = 0
    for it in items:
        if it.get("signal") != "high":
            continue
        if it.get("rumor"):
            continue
        if not (set(it.get("tags") or []) & watched):
            continue
        if is_pushed(conn, it["id"]):
            continue
        if _post(topic, it):
            mark_pushed(conn, it["id"])
            sent += 1
    return sent


def _post(topic: str, it: dict) -> bool:
    title = (it.get("headline") or it.get("title") or "中美政策雷达").strip()
    body = (it.get("impact") or it.get("synopsis") or "").strip() or title
    tags = it.get("tags") or []
    if tags:
        body += "\n标签：" + " ".join("#" + t for t in tags)

    payload = {
        "topic": topic,
        "title": title,
        "message": body,
        "priority": PRIORITY_HIGH,
        "tags": ["rotating_light"],  # 通知里显示一个 🚨
    }
    if it.get("url"):
        payload["click"] = it["url"]

    try:
        r = requests.post(
            NTFY_BASE, json=payload,
            headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — 推送失败不应中断流水线
        log.error("ntfy 推送失败：%s", e)
        return False


def main() -> int:
    """连通性自测：python -m radar.notify --test

    取库里最新一条 high（真实内容）发一条测试推送；没有 high 就发合成测试。
    不写 pushed 标记（纯测试）。
    """
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    topic = env("NTFY_TOPIC")
    if not topic:
        print("未设 NTFY_TOPIC，无法测试。请在 .env 填好后重试。")
        return 1

    from radar.store import connect, load_items

    conn = connect()
    highs = [i for i in load_items(conn) if i["signal"] == "high"]
    conn.close()

    sample = highs[0] if highs else {
        "headline": "测试推送 · 中美政策雷达",
        "impact": "这是一条 ntfy 连通性测试，收到即表示推送链路正常。",
        "tags": ["关税"],
        "url": "https://www.federalregister.gov/",
    }
    ok = _post(topic, sample)
    kind = "真实 high 条" if highs else "合成测试条"
    print(f"测试推送（{kind}）→ topic={topic}：{'成功 ✅ 看手机' if ok else '失败 ❌'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
