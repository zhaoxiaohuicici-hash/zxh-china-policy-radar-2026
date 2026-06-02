"""SQLite 存储：两张表。

- items：打分后的条目，供看板读取（含 signal/tags/why）。
- seen ：去重账本，记录每个 id 首次见到的时间。

设计要点：
- seen 与 items 分离——一条 low 信号即使不在看板突出显示，也要进 seen，
  避免下一轮被重复拉取/打分。
- 保留窗口：items 留最近 RETENTION_DAYS 天；seen 留更久（SEEN_RETENTION_DAYS），
  防止刚滚出 items 的旧条目下一轮又被当成「新」重新打分。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from radar.config import DB_PATH, RETENTION_DAYS

SEEN_RETENTION_DAYS = 90


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS items (
            id           TEXT PRIMARY KEY,
            source       TEXT,
            source_layer INTEGER,
            title        TEXT,
            summary      TEXT,   -- 原文摘录
            url          TEXT,
            published    TEXT,
            signal       TEXT,
            tags         TEXT,   -- JSON 数组
            headline     TEXT,   -- 编辑化标题
            synopsis     TEXT,   -- 摘要
            impact       TEXT,   -- 影响 so-what
            author       TEXT,   -- 观点来自
            involved     TEXT,   -- JSON 数组：涉及的人与机构
            rumor        INTEGER,-- 0/1 未证实
            person_type  TEXT,   -- reporter/scholar/vc_kol/govt/industry（layer3 真人）
            why          TEXT,
            scored_at    TEXT
        );
        CREATE TABLE IF NOT EXISTS seen (
            id         TEXT PRIMARY KEY,
            first_seen TEXT
        );
        CREATE TABLE IF NOT EXISTS meta (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_items_published ON items(published);
        CREATE INDEX IF NOT EXISTS idx_items_signal ON items(signal);
        """
    )
    _ensure_columns(conn, "items", {
        "synopsis": "TEXT", "involved": "TEXT",
        "headline": "TEXT", "impact": "TEXT", "author": "TEXT", "rumor": "INTEGER",
        "pushed": "INTEGER",  # 1=已 ntfy 推送，避免重复轰炸
        "person_type": "TEXT",
        "key_date": "TEXT", "client_soval": "TEXT",
    })
    conn.commit()


def delete_items(conn: sqlite3.Connection, ids: list[str]) -> None:
    """从 items 删除（用于把已落库的噪音条目清掉；不动 seen 以免重复打分）。"""
    if not ids:
        return
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        conn.execute(f"DELETE FROM items WHERE id IN ({','.join('?' * len(chunk))})", chunk)
    conn.commit()


def is_pushed(conn: sqlite3.Connection, item_id: str) -> bool:
    r = conn.execute("SELECT pushed FROM items WHERE id=?", (item_id,)).fetchone()
    return bool(r and r[0])


def mark_pushed(conn: sqlite3.Connection, item_id: str) -> None:
    conn.execute("UPDATE items SET pushed=1 WHERE id=?", (item_id,))
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value, updated_at) VALUES(?, ?, ?)",
        (key, value, _now_iso()),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    """为已存在的表补齐新列（老库平滑升级）。"""
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


# ── 去重账本 ──────────────────────────────────────────────────
def get_seen_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    """返回 ids 中已在 seen 表里的子集。"""
    if not ids:
        return set()
    out: set[str] = set()
    # 分批避免 SQL 变量上限
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        q = f"SELECT id FROM seen WHERE id IN ({','.join('?' * len(chunk))})"
        out.update(r[0] for r in conn.execute(q, chunk))
    return out


def mark_seen(conn: sqlite3.Connection, ids: list[str]) -> None:
    now = _now_iso()
    conn.executemany(
        "INSERT OR IGNORE INTO seen(id, first_seen) VALUES(?, ?)",
        [(i, now) for i in ids],
    )
    conn.commit()


# ── items ─────────────────────────────────────────────────────
def upsert_items(conn: sqlite3.Connection, items: list[dict]) -> None:
    """写入打分后的条目，并把它们登记进 seen。"""
    now = _now_iso()
    rows = [
        (
            it["id"],
            it.get("source"),
            it.get("source_layer"),
            it.get("title"),
            it.get("summary"),
            it.get("url"),
            it.get("published"),
            it.get("signal"),
            json.dumps(it.get("tags") or [], ensure_ascii=False),
            it.get("headline") or "",
            it.get("synopsis") or "",
            it.get("impact") or "",
            it.get("author") or "",
            json.dumps(it.get("involved") or [], ensure_ascii=False),
            1 if it.get("rumor") else 0,
            it.get("person_type") or "",
            it.get("key_date") or "",
            it.get("client_soval") or "",
            it.get("why"),
            now,
        )
        for it in items
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO items
          (id, source, source_layer, title, summary, url, published,
           signal, tags, headline, synopsis, impact, author, involved, rumor, person_type,
           key_date, client_soval, why, scored_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    mark_seen(conn, [it["id"] for it in items])


def load_items(conn: sqlite3.Connection, days: int = RETENTION_DAYS) -> list[dict]:
    """读取最近 days 天的条目（按时间倒序），供 render 用；tags 解析回 list。"""
    cutoff = _days_ago_iso(days)
    rows = conn.execute(
        """
        SELECT i.*, s.first_seen AS first_seen
        FROM items i LEFT JOIN seen s ON s.id = i.id
        WHERE COALESCE(i.published, i.scored_at) >= ?
        ORDER BY COALESCE(i.published, i.scored_at) DESC
        """,
        (cutoff,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for fld in ("tags", "involved"):
            try:
                d[fld] = json.loads(d.get(fld) or "[]")
            except Exception:
                d[fld] = []
        d["rumor"] = bool(d.get("rumor"))
        out.append(d)
    return out


# ── 保留窗口清理 ──────────────────────────────────────────────
def prune(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM items WHERE COALESCE(published, scored_at) < ?",
        (_days_ago_iso(RETENTION_DAYS),),
    )
    conn.execute(
        "DELETE FROM seen WHERE first_seen < ?",
        (_days_ago_iso(SEEN_RETENTION_DAYS),),
    )
    conn.commit()


def _days_ago_iso(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
