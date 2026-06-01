"""第三层 · X（via TwitterAPI.io）—— 一线记者/学者/意见领袖的「风声」。

鉴权：.env 的 TWITTERAPI_KEY，走 HTTP 头 `x-api-key`。
- base: https://api.twitterapi.io
- /twitter/user/info        按 screen name 解析用户(拿 numeric id，建清单时用)
- /twitter/user/last_tweets 取某人最近推文(userId 优先，更稳更快)

省钱护栏（约 30 人，务必）：
- 每人每轮最近 N 条(max_per_account=10)、仅最近 lookback_days 天、只留原创(滤掉纯转发/纯回复)；
- 每轮每人 **1 次**调用，绝不分页(只取第一页 ~20 条再裁)；
- **节流阀** min_interval_minutes：X 拉取与主流水线(30min)解耦——距上次 X 拉取不足该间隔就整源跳过，
  用 meta.last_x_run 记录(CI 无状态也能跨轮节流)，把月成本压进预算。

产出统一 Item：source_layer=3、source=本人名、author=本人、person_type 元数据(reporter/
scholar/vc_kol，供打分对 vc_kol 降噪)、url=推文链接。每账号独立 try/except。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from radar.config import HTTP_TIMEOUT, USER_AGENT, env
from radar.item import clean_text, make_item, parse_iso

log = logging.getLogger("radar.x")

BASE = "https://api.twitterapi.io"


def _headers():
    key = env("TWITTERAPI_KEY")
    if not key:
        raise RuntimeError("缺少 TWITTERAPI_KEY")
    return {"x-api-key": key, "User-Agent": USER_AGENT, "Accept": "application/json"}


# ── 解析：screen name → 用户档案（建清单/回填 user_id 时用）──────
def resolve_user(handle: str) -> dict | None:
    """GET /twitter/user/info?userName=handle → {id,userName,name,description,followers}。"""
    r = requests.get(
        f"{BASE}/twitter/user/info",
        params={"userName": handle},
        headers=_headers(),
        timeout=HTTP_TIMEOUT,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "success" or not data.get("data"):
        return None
    return data["data"]


# ── 抓取：流水线主入口 ─────────────────────────────────────────
def fetch_x(cfg: dict, conn=None) -> list[dict]:
    xc = cfg.get("x") or {}
    if not xc.get("enabled"):
        return []
    accounts = xc.get("accounts") or []
    lookback = int(xc.get("lookback_days", 7))
    max_per = int(xc.get("max_per_account", 10))
    include_replies = bool(xc.get("include_replies", False))
    drop_retweets = bool(xc.get("drop_retweets", True))

    # 节流：距上次 X 拉取不足 min_interval_minutes 就整源跳过（控成本）
    interval = int(xc.get("min_interval_minutes", 240))
    if conn is not None and interval > 0:
        from radar.store import get_meta, set_meta

        last = parse_iso(get_meta(conn, "last_x_run"))
        now = datetime.now(timezone.utc)
        if last and (now - last).total_seconds() < interval * 60:
            mins = int((now - last).total_seconds() // 60)
            log.info("x 节流：距上次拉取 %d 分钟 < %d，跳过", mins, interval)
            return []
        set_meta(conn, "last_x_run", now.isoformat())

    items: list[dict] = []
    calls = 0
    for acc in accounts:
        name = acc.get("name", "?")
        uid = acc.get("user_id")
        handle = acc.get("handle")
        ptype = acc.get("person_type", "")
        if not (uid or handle):
            continue
        try:
            got, n_calls = _fetch_one(name, uid, handle, ptype, lookback, max_per,
                                      include_replies, drop_retweets)
            items.extend(got)
            calls += n_calls
            log.info("x/%-22s %2d 条", name, len(got))
        except Exception as e:  # noqa: BLE001 — 单账号失败不连累其他
            log.error("x/%s 失败: %s", name, e)
    log.info("x 本轮调用 %d 次（≤名单人数）", calls)
    return items


def _fetch_one(name, uid, handle, ptype, lookback, max_per, include_replies, drop_retweets):
    params = {"includeReplies": "true" if include_replies else "false"}
    if uid:
        params["userId"] = uid          # 优先用 id：更稳更快
    else:
        params["userName"] = handle
    # 只取第一页（绝不翻页深挖历史）
    r = requests.get(
        f"{BASE}/twitter/user/last_tweets", params=params, headers=_headers(), timeout=HTTP_TIMEOUT
    )
    r.raise_for_status()
    # 响应结构：{status, code, msg, data:{tweets:[...]}, has_next_page, next_cursor}
    body = r.json()
    tweets = ((body.get("data") or {}).get("tweets")) or body.get("tweets") or []

    out: list[dict] = []
    for tw in tweets:
        # 滤掉纯转发 / 纯回复，只留原创
        if drop_retweets and (tw.get("retweeted_tweet") or tw.get("isRetweet")):
            continue
        if not include_replies and tw.get("isReply"):
            continue
        text = (tw.get("text") or "").strip()
        if not text:
            continue
        created = _to_iso(tw.get("createdAt"))
        if not _within(created, lookback):
            continue

        tid = str(tw.get("id") or tw.get("id_str") or "")
        screen = (tw.get("author") or {}).get("userName") or handle or ""
        url = tw.get("url") or (f"https://x.com/{screen}/status/{tid}" if tid and screen else "")

        it = make_item(
            id=f"x:{tid}" if tid else f"x:{name}:{created}",
            source=name,
            source_layer=3,
            title=clean_text(text, limit=120),
            summary=text,
            url=url,
            published=created,
        )
        it["author"] = name
        it["person_type"] = ptype       # 供打分对 vc_kol 降噪
        out.append(it)
        if len(out) >= max_per:
            break
    return out, 1


def _to_iso(created: str | None) -> str | None:
    """TwitterAPI.io 的 createdAt：兼容 ISO/Z 与 Twitter 经典格式。统一转 ISO。"""
    if not created:
        return None
    dt = parse_iso(created)
    if dt is None:
        try:  # Twitter 经典："Wed Mar 19 16:54:50 +0000 2025"
            dt = datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
        except Exception:
            return None
    return dt.astimezone(timezone.utc).isoformat()


def _within(iso: str | None, days: int) -> bool:
    dt = parse_iso(iso)
    if dt is None:
        return False  # X 这里宁可漏判旧帖：解析不出时间就不收（避免灌进旧推）
    return (datetime.now(timezone.utc) - dt).total_seconds() <= days * 86400


# ── 解析 CLI：python -m radar.sources.x --resolve ──────────────
def _name_tokens(s: str) -> list[str]:
    import re
    return [t for t in re.split(r"\s+", (s or "").lower()) if len(t) > 1]


def resolve_all(cfg: dict) -> dict:
    """对 x.accounts 逐个核实，分三类：success/uncertain/fail。

    success：handle 解析到 + 显示名能对上人名(姓与名 token 命中)；
    uncertain：解析到但显示名对不太上(可能错号)，需人工看一眼；
    fail：handle 不存在。
    """
    accounts = (cfg.get("x") or {}).get("accounts") or []
    out = {"success": [], "uncertain": [], "fail": []}
    for acc in accounts:
        name, handle, pt = acc.get("name"), acc.get("handle"), acc.get("person_type")
        try:
            prof = resolve_user(handle)
        except Exception as e:  # noqa: BLE001
            out["fail"].append((name, handle, pt, None, f"请求错误:{e}"))
            continue
        if not prof:
            out["fail"].append((name, handle, pt, None, "handle 不存在"))
            continue
        dn = prof.get("name") or ""
        toks = _name_tokens(name)
        matched = sum(1 for t in toks if t in dn.lower())
        uid = str(prof.get("id") or "")
        info = f"id={uid} 显示名={dn!r} 粉丝={prof.get('followers')} verified={prof.get('isBlueVerified')}"
        if matched >= max(1, len(toks) - 0):  # 全部姓名 token 命中
            out["success"].append((name, handle, pt, uid, info))
        elif matched >= 1:
            out["uncertain"].append((name, handle, pt, uid, info))
        else:
            out["uncertain"].append((name, handle, pt, uid, "显示名对不上：" + info))
    return out


def main() -> int:
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)-5s %(message)s")
    if "--resolve" not in sys.argv:
        print("用法：python -m radar.sources.x --resolve（核实 handle→user_id，需 TWITTERAPI_KEY）")
        return 0
    from radar.config import load_sources
    res = resolve_all(load_sources())
    for kind, label in [("success", "✅ 解析成功"), ("uncertain", "⚠️ 存疑(可能错号)"), ("fail", "❌ 失败")]:
        rows = res[kind]
        print(f"\n===== {label}（{len(rows)}）=====")
        for name, handle, pt, uid, info in rows:
            print(f"· {name} [{pt}] @{handle}\n    {info}")
    # 便于回填 yaml：成功项的 name→user_id
    print("\n--- 成功项 user_id（回填 sources.yaml）---")
    for name, handle, pt, uid, info in res["success"]:
        print(f"  {name}: {uid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
