# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lexicon import PINNED_AUTHORS

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("LUOJIAN_DATA") or (ROOT / "data"))
DB_PATH = DATA / "desk.db"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


class _Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, factory=_Connection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS days (
                date TEXT PRIMARY KEY,
                raw TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '投入',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                conclusion_json TEXT,
                notion_url TEXT,
                archive_path TEXT
            );
            CREATE TABLE IF NOT EXISTS snippets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                source TEXT NOT NULL,
                text TEXT NOT NULL,
                layer TEXT NOT NULL,
                layers_json TEXT NOT NULL DEFAULT '[]',
                industries_json TEXT NOT NULL DEFAULT '[]',
                tickers_json TEXT NOT NULL DEFAULT '[]',
                sentiment REAL NOT NULL DEFAULT 0,
                stance TEXT NOT NULL DEFAULT 'hold',
                mood TEXT NOT NULL DEFAULT '中性',
                actions_json TEXT NOT NULL DEFAULT '[]',
                weight REAL NOT NULL DEFAULT 1,
                ord INTEGER NOT NULL DEFAULT 0,
                extras_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(date) REFERENCES days(date) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS expansions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snippet_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                provider TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(snippet_id) REFERENCES snippets(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS roster (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                last_used TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lookups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                context TEXT NOT NULL DEFAULT '',
                question TEXT NOT NULL,
                answer TEXT NOT NULL DEFAULT '',
                urls_json TEXT NOT NULL DEFAULT '[]',
                queries_json TEXT NOT NULL DEFAULT '[]',
                model TEXT NOT NULL DEFAULT '',
                provider TEXT NOT NULL DEFAULT '',
                client_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(lookups)")}
        if "client_id" not in columns:
            conn.execute("ALTER TABLE lookups ADD COLUMN client_id TEXT NOT NULL DEFAULT ''")


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False)


def _loads(s: str | None, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return default


def snippet_to_dict(row: sqlite3.Row, expansions: list[dict] | None = None) -> dict:
    d = dict(row)
    return {
        "id": d["id"],
        "date": d["date"],
        "source": d["source"],
        "text": d["text"],
        "layer": d["layer"],
        "layers": _loads(d["layers_json"], []),
        "industries": _loads(d["industries_json"], []),
        "tickers": _loads(d["tickers_json"], []),
        "sentiment": d["sentiment"],
        "stance": d["stance"],
        "mood": d["mood"],
        "actions": _loads(d["actions_json"], []),
        "weight": d["weight"],
        "order": d["ord"],
        "extras": _loads(d["extras_json"], {}),
        "expansions": expansions or [],
    }


def list_days() -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT d.date, d.status, d.updated_at, d.notion_url,
                   COUNT(s.id) AS n,
                   AVG(s.sentiment) AS avg_sent
            FROM days d
            LEFT JOIN snippets s ON s.date = d.date
            GROUP BY d.date
            ORDER BY d.date DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        item = dict(r)
        item["avg_sent"] = round(float(item["avg_sent"] or 0), 3)
        out.append(item)
    return out


def get_day(day: str) -> dict | None:
    init_db()
    with connect() as conn:
        drow = conn.execute("SELECT * FROM days WHERE date=?", (day,)).fetchone()
        if not drow:
            return None
        snips = conn.execute(
            "SELECT * FROM snippets WHERE date=? ORDER BY ord, id", (day,)
        ).fetchall()
        exps = conn.execute(
            "SELECT * FROM expansions WHERE date=? ORDER BY id", (day,)
        ).fetchall()
    by_sid: dict[int, list[dict]] = {}
    for e in exps:
        by_sid.setdefault(e["snippet_id"], []).append(
            {
                "id": e["id"],
                "provider": e["provider"],
                "query": e["query"],
                "result": e["result"],
                "meta": _loads(e["meta_json"], {}),
                "created_at": e["created_at"],
            }
        )
    snippets = [snippet_to_dict(s, by_sid.get(s["id"], [])) for s in snips]
    dayd = dict(drow)
    return {
        "date": dayd["date"],
        "raw": dayd["raw"],
        "status": dayd["status"],
        "created_at": dayd["created_at"],
        "updated_at": dayd["updated_at"],
        "conclusion": _loads(dayd["conclusion_json"], None),
        "notion_url": dayd["notion_url"],
        "archive_path": dayd["archive_path"],
        "snippets": snippets,
        "expansion_count": sum(len(v) for v in by_sid.values()),
    }


def replace_day(day: str, raw: str, classified: list[dict], status: str = "投入") -> None:
    init_db()
    ts = now_iso()
    with connect() as conn:
        exists = conn.execute("SELECT date FROM days WHERE date=?", (day,)).fetchone()
        if exists:
            conn.execute("DELETE FROM expansions WHERE date=?", (day,))
            conn.execute("DELETE FROM snippets WHERE date=?", (day,))
            conn.execute(
                """
                UPDATE days SET raw=?, status=?, updated_at=?, conclusion_json=NULL
                WHERE date=?
                """,
                (raw, status, ts, day),
            )
        else:
            conn.execute(
                """
                INSERT INTO days(date, raw, status, created_at, updated_at)
                VALUES (?,?,?,?,?)
                """,
                (day, raw, status, ts, ts),
            )
        for i, c in enumerate(classified):
            conn.execute(
                """
                INSERT INTO snippets(
                    date, source, text, layer, layers_json, industries_json,
                    tickers_json, sentiment, stance, mood, actions_json,
                    weight, ord, extras_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    day,
                    c["source"],
                    c["text"],
                    c["layer"],
                    _j(c.get("layers") or []),
                    _j(c.get("industries") or []),
                    _j(c.get("tickers") or []),
                    float(c.get("sentiment") or 0),
                    c.get("stance") or "hold",
                    c.get("mood") or "中性",
                    _j(c.get("actions") or []),
                    float(c.get("weight") or 1),
                    int(c.get("order") or i),
                    _j(c.get("extras") or {}),
                ),
            )


def snippet_key(source: str, text: str) -> tuple[str, str]:
    return source.strip().casefold(), " ".join(text.split())


def append_day(day: str, raw: str, classified: list[dict], status: str = "投入") -> int:
    """Append unseen source/text pairs atomically, preserving snippet/expansion IDs."""
    init_db()
    ts = now_iso()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute("SELECT * FROM days WHERE date=?", (day,)).fetchone()
        rows = conn.execute("SELECT source, text, ord, extras_json FROM snippets WHERE date=?", (day,)).fetchall()
        seen = {snippet_key(r["source"], r["text"]) for r in rows}
        for row in rows:
            original = _loads(row["extras_json"], {}).get("ingest_key")
            if isinstance(original, list) and len(original) == 2:
                seen.add(tuple(original))
        fresh = []
        for item in classified:
            key = snippet_key(item["source"], item["text"])
            if key not in seen:
                seen.add(key)
                fresh.append(item)
        if not fresh:
            return 0
        if existing:
            conn.execute("UPDATE days SET raw=?, status=?, updated_at=?, conclusion_json=NULL WHERE date=?",
                         (existing["raw"] + "\n\n" + raw, status, ts, day))
        else:
            conn.execute("INSERT INTO days(date,raw,status,created_at,updated_at) VALUES (?,?,?,?,?)", (day,raw,status,ts,ts))
        order = max((r["ord"] for r in rows), default=0)
        for i, c in enumerate(fresh, 1):
            extras = dict(c.get("extras") or {})
            extras["ingest_key"] = list(snippet_key(c["source"], c["text"]))
            conn.execute("""INSERT INTO snippets(date,source,text,layer,layers_json,industries_json,tickers_json,
                sentiment,stance,mood,actions_json,weight,ord,extras_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (day,c["source"],c["text"],c["layer"],_j(c.get("layers",[])),_j(c.get("industries",[])),
                 _j(c.get("tickers",[])),c.get("sentiment",0),c.get("stance","hold"),c.get("mood","中性"),
                 _j(c.get("actions",[])),c.get("weight",1),order+i,_j(extras)))
        return len(fresh)


def update_snippet(sid: int, fields: dict) -> dict | None:
    init_db()
    from .classify import mood_label
    import math
    fields = dict(fields)
    if "sentiment" in fields:
        value = fields["sentiment"]
        if type(value) not in (int,float) or not math.isfinite(value) or not -1 <= value <= 1:
            raise ValueError("情绪分必须是-1到1之间的有限数字")
        fields["mood"] = mood_label(value)
    elif "mood" in fields:
        values = {"偏多":0.5,"偏空":-0.5,"中性":0.0,"分歧":0.15}
        if fields["mood"] not in values:
            raise ValueError("情绪标签无效")
        fields["sentiment"] = values[fields["mood"]]
    if "layer" in fields:
        if fields["layer"] not in {"macro","industry","stock"}:
            raise ValueError("分层标签无效")
        fields["layers"] = [fields["layer"]]
    if "stance" in fields and fields["stance"] not in {"hold","watch","add","reduce","switch","short"}:
        raise ValueError("立场标签无效")
    allowed = {
        "layer",
        "source",
        "text",
        "sentiment",
        "stance",
        "mood",
        "weight",
    }
    json_map = {
        "layers": "layers_json",
        "industries": "industries_json",
        "tickers": "tickers_json",
        "actions": "actions_json",
    }
    sets = []
    vals: list[Any] = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        vals.append(v)
    for key, column in json_map.items():
        if key in fields:
            sets.append(f"{column}=?")
            vals.append(_j(fields[key]))
    if not sets and "extras" not in fields:
        return None
    with connect() as conn:
        row = conn.execute("SELECT date, extras_json FROM snippets WHERE id=?", (sid,)).fetchone()
        if not row:
            return None
        if "extras" in fields and isinstance(fields["extras"], dict):
            merged = {**_loads(row["extras_json"], {}), **fields["extras"]}
            sets.append("extras_json=?")
            vals.append(_j(merged))
        if not sets:
            return None
        vals.append(sid)
        conn.execute(f"UPDATE snippets SET {', '.join(sets)} WHERE id=?", vals)
        conn.execute(
            "UPDATE days SET updated_at=?, conclusion_json=NULL, status='投入' WHERE date=?",
            (now_iso(), row["date"]),
        )
        day = row["date"]
    return get_day(day)


def add_expansion(snippet_id: int, provider: str, query: str, result: str, meta: dict | None = None) -> dict:
    init_db()
    ts = now_iso()
    with connect() as conn:
        row = conn.execute("SELECT date FROM snippets WHERE id=?", (snippet_id,)).fetchone()
        if not row:
            raise KeyError(f"snippet {snippet_id} not found")
        day = row["date"]
        cur = conn.execute(
            """
            INSERT INTO expansions(snippet_id, date, provider, query, result, meta_json, created_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (snippet_id, day, provider, query, result, _j(meta or {}), ts),
        )
        conn.execute("UPDATE days SET status=?, updated_at=?, conclusion_json=NULL WHERE date=?", ("展开", ts, day))
        eid = cur.lastrowid
    packed = get_day(day)
    packed["new_expansion_id"] = eid
    return packed


def save_conclusion(day: str, conclusion: dict, status: str = "结论", *, expected_updated_at: str | None = None) -> dict:
    init_db()
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            "UPDATE days SET conclusion_json=?, status=?, updated_at=? WHERE date=? AND (? IS NULL OR updated_at=?)",
            (_j(conclusion), status, ts, day, expected_updated_at, expected_updated_at),
        )
    return get_day(day)  # type: ignore[return-value]


def mark_archived(day: str, archive_path: str, notion_url: str | None = None) -> dict:
    init_db()
    ts = now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE days SET status=?, archive_path=?, notion_url=COALESCE(?, notion_url), updated_at=?
            WHERE date=?
            """,
            ("已归档", archive_path, notion_url, ts, day),
        )
    return get_day(day)  # type: ignore[return-value]


UNSIGNED = "未署名"


def media_root() -> Path:
    return DATA / "media"


def pinned_roster() -> list[dict]:
    return [dict(item) for item in PINNED_AUTHORS]


def canonical_source(name: str) -> str:
    n = (name or "").strip()
    if not n or n == UNSIGNED:
        return UNSIGNED
    key = n.casefold()
    for item in PINNED_AUTHORS:
        if item["name"].casefold() == key:
            return item["name"]
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT name FROM roster WHERE key=?", (key,)).fetchone()
        if row:
            return row["name"]
        rows = conn.execute("SELECT DISTINCT source FROM snippets").fetchall()
    for row in rows:
        if (row["source"] or "").casefold() == key:
            return row["source"]
    return n


def touch_roster(conn: sqlite3.Connection, name: str, ts: str | None = None) -> None:
    n = (name or "").strip()
    if not n or n == UNSIGNED:
        return
    stamp = ts or now_iso()
    conn.execute(
        """
        INSERT INTO roster(key, name, last_used) VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET last_used=excluded.last_used
        """,
        (n.casefold(), n, stamp),
    )


def _pin_roster(names: list[str]) -> list[str]:
    pinned = [item["name"] for item in PINNED_AUTHORS]
    seen = {name.casefold() for name in pinned}
    rest = []
    for name in names:
        key = (name or "").casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        rest.append(name)
    return pinned + rest


def list_roster() -> list[str]:
    init_db()
    with connect() as conn:
        rows = conn.execute("SELECT name FROM roster ORDER BY last_used DESC").fetchall()
        if rows:
            return _pin_roster([r["name"] for r in rows])
        rows = conn.execute(
            """
            SELECT source FROM snippets
            WHERE source != ?
            GROUP BY source
            ORDER BY MAX(id) DESC
            """,
            (UNSIGNED,),
        ).fetchall()
    return _pin_roster([r["source"] for r in rows])


def ensure_day_row(conn: sqlite3.Connection, day: str, ts: str, status: str = "投入") -> None:
    exists = conn.execute("SELECT date FROM days WHERE date=?", (day,)).fetchone()
    if exists:
        conn.execute(
            "UPDATE days SET updated_at=?, conclusion_json=NULL, status=? WHERE date=?",
            (ts, status, day),
        )
        return
    conn.execute(
        "INSERT INTO days(date, raw, status, created_at, updated_at) VALUES (?,?,?,?,?)",
        (day, "", status, ts, ts),
    )


def insert_snippet_row(conn: sqlite3.Connection, day: str, classified: dict, order: int) -> int:
    extras = dict(classified.get("extras") or {})
    cur = conn.execute(
        """
        INSERT INTO snippets(
            date, source, text, layer, layers_json, industries_json,
            tickers_json, sentiment, stance, mood, actions_json,
            weight, ord, extras_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            day,
            classified["source"],
            classified.get("text") or "",
            classified["layer"],
            _j(classified.get("layers") or []),
            _j(classified.get("industries") or []),
            _j(classified.get("tickers") or []),
            float(classified.get("sentiment") or 0),
            classified.get("stance") or "hold",
            classified.get("mood") or "中性",
            _j(classified.get("actions") or []),
            float(classified.get("weight") or 1),
            int(order),
            _j(extras),
        ),
    )
    return int(cur.lastrowid)


def snippet_rows(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM snippets WHERE date=? ORDER BY ord, id", (day,)
    ).fetchall()


def write_day_raw(conn: sqlite3.Connection, day: str, raw: str) -> None:
    conn.execute("UPDATE days SET raw=? WHERE date=?", (raw, day))


def delete_snippet_row(conn: sqlite3.Connection, sid: int) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM snippets WHERE id=?", (sid,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM snippets WHERE id=?", (sid,))
    return row


def delete_day_snippets(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM snippets WHERE date=?", (day,)).fetchall()
    conn.execute("DELETE FROM snippets WHERE date=?", (day,))
    conn.execute("DELETE FROM days WHERE date=?", (day,))
    return list(rows)


def reassign_ord(conn: sqlite3.Connection, day: str, ids: list[int]) -> None:
    for i, sid in enumerate(ids):
        conn.execute("UPDATE snippets SET ord=? WHERE id=?", (i, sid))


def add_lookup(
    *,
    context: str,
    question: str,
    answer: str = "",
    urls: list | None = None,
    queries: list | None = None,
    model: str = "",
    provider: str = "",
    client_id: str = "",
) -> dict:
    init_db()
    ts = now_iso()
    with connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO lookups
                (context, question, answer, urls_json, queries_json, model, provider, client_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (context, question, answer, _j(urls or []), _j(queries or []), model, provider, client_id, ts),
        )
        rid = int(cur.lastrowid)
    return {"id": rid, "created_at": ts}


def complete_lookup(
    lookup_id: int,
    *,
    answer: str,
    urls: list,
    queries: list,
    model: str,
    provider: str,
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """
            UPDATE lookups
            SET answer=?, urls_json=?, queries_json=?, model=?, provider=?
            WHERE id=?
            """,
            (answer, _j(urls), _j(queries), model, provider, lookup_id),
        )


def list_lookups(day: str | None = None, limit: int = 300) -> list[dict]:
    init_db()
    sql = "SELECT * FROM lookups"
    params: list = []
    if day:
        sql += " WHERE substr(created_at, 1, 10) = ?"
        params.append(day)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [
        {
            "id": row["id"],
            "context": row["context"],
            "question": row["question"],
            "answer": row["answer"],
            "urls": _loads(row["urls_json"], []),
            "queries": _loads(row["queries_json"], []),
            "model": row["model"],
            "provider": row["provider"],
            "client_id": row["client_id"] or "",
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def next_ord(conn: sqlite3.Connection, day: str) -> int:
    row = conn.execute("SELECT COALESCE(MAX(ord), -1) AS m FROM snippets WHERE date=?", (day,)).fetchone()
    return int(row["m"]) + 1 if row else 0
