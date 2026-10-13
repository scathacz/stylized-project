# -*- coding: utf-8 -*-
"""速记：结构化写入，不走 parse_notes 猜作者。"""
from __future__ import annotations

import re
import secrets
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import db
from .classify import classify_snippet
from .lexicon import KNOWN_SOURCES
from .parser import RawSnippet, _date_header

UNSIGNED = db.UNSIGNED
IMPORTANT_RE = re.compile(r"\*\*[^*\n]+\*\*")
MEDIA_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
MAX_ITEMS = 500
MAX_MEDIA = 8 * 1024 * 1024
_SIGS = (
    (b"\x89PNG\r\n\x1a\n", ".png", "image/png"),
    (b"\xff\xd8", ".jpg", "image/jpeg"),
    (b"GIF87a", ".gif", "image/gif"),
    (b"GIF89a", ".gif", "image/gif"),
)


def sniff_image(data: bytes) -> tuple[str, str]:
    if not data:
        raise ValueError("空文件")
    for sig, ext, mime in _SIGS:
        if data.startswith(sig):
            return ext, mime
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    raise ValueError("只收 png / jpeg / gif / webp 图片")


def is_important(text: str, flag: bool | None = None) -> bool:
    if flag:
        return True
    return bool(IMPORTANT_RE.search(text or ""))


def _cn_date(day: str) -> str:
    y, m, d = day.split("-")
    return f"{y}年{int(m)}月{int(d)}日"


def _media_of(row) -> str | None:
    extras = db._loads(row["extras_json"] if not isinstance(row, dict) else (row.get("extras_json") or row.get("extras") or {}), {})
    if isinstance(row, dict) and "extras" in row and isinstance(row["extras"], dict):
        extras = row["extras"]
    media = extras.get("media")
    return media if isinstance(media, str) and media else None


def rebuild_raw(day: str, rows: list) -> str:
    order: list[str] = []
    groups: dict[str, list[str]] = {}
    for row in rows:
        source = row["source"]
        extras = db._loads(row["extras_json"], {})
        media = extras.get("media")
        text = (row["text"] or "").strip()
        if media and text:
            body = f"[图]\n{text}"
        elif media:
            body = "[图]"
        else:
            body = row["text"] or ""
        if source not in groups:
            groups[source] = []
            order.append(source)
        groups[source].append(body)
    lines = [_cn_date(day), ""]
    for source in order:
        lines.append(source)
        lines.extend(groups[source])
        lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if order else "")


def _row_item(row) -> dict:
    extras = db._loads(row["extras_json"], {})
    text = row["text"] or ""
    media = extras.get("media") if isinstance(extras.get("media"), str) else None
    if media and text in ("", "[图]"):
        text = ""
    return {
        "id": row["id"],
        "source": row["source"],
        "text": text,
        "important": bool(extras.get("important")) or is_important(text),
        "media": media,
    }


def payload(day: str) -> dict:
    db.init_db()
    with db.connect() as conn:
        rows = db.snippet_rows(conn, day)
    return {"date": day, "items": [_row_item(r) for r in rows], "roster": db.list_roster(), "pinned": db.pinned_roster()}


def _classified(day: str, source: str, text: str, important: bool, media: str | None, old_extras: dict | None = None) -> dict:
    snippet = classify_snippet(RawSnippet(date.fromisoformat(day), source, text or ""))
    item = asdict(snippet)
    extras = dict(item.get("extras") or {})
    if old_extras:
        for key in ("media", "capture", "ingest_key"):
            if key in old_extras and old_extras[key] not in (None, ""):
                extras[key] = old_extras[key]
    extras["capture"] = "quick"
    extras["important"] = bool(important or is_important(text))
    if media:
        extras["media"] = media
    elif media is None and not (old_extras or {}).get("media"):
        extras.pop("media", None)
    item["extras"] = extras
    item["source"] = source
    item["text"] = text or ""
    return item


def _unlink_media(rel: str | None) -> None:
    if not rel or not isinstance(rel, str):
        return
    parts = rel.replace("\\", "/").split("/")
    if len(parts) != 2 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]) or not MEDIA_NAME_RE.fullmatch(parts[1]):
        return
    path = db.media_root() / parts[0] / parts[1]
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _refresh_raw(conn, day: str) -> None:
    db.write_day_raw(conn, day, rebuild_raw(day, db.snippet_rows(conn, day)))


def _media_file_ok(rel: str) -> bool:
    try:
        day, name = str(rel).split("/", 1)
        return media_path(day, name).is_file()
    except (ValueError, OSError):
        return False


def _clean_line(line: str) -> str:
    return line.strip().strip("\u200b\ufeff")


def known_author_index() -> dict[str, str]:
    """已有作者：名册、历史来源、词典。词典只补缺，不改已经写下的写法。"""
    index: dict[str, str] = {}
    for name in db.list_roster():
        if name and name != UNSIGNED:
            index[name.casefold()] = name
    db.init_db()
    with db.connect() as conn:
        rows = conn.execute("SELECT DISTINCT source FROM snippets").fetchall()
    for row in rows:
        name = (row["source"] or "").strip()
        if name and name != UNSIGNED:
            index.setdefault(name.casefold(), name)
    for name in KNOWN_SOURCES:
        index.setdefault(name.casefold(), name)
    index[UNSIGNED.casefold()] = UNSIGNED
    return index


def _is_date_line(line: str) -> bool:
    try:
        return _date_header(line, date.today()) is not None
    except ValueError:
        return False


def split_capture(text: str, fallback: str) -> list[dict]:
    """整段粘贴：单独一行是已有作者时，下面的内容归到这个人，直到下一个作者行。"""
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not raw.strip():
        return []
    index = known_author_index()

    def match(line: str) -> str | None:
        return index.get(_clean_line(line).casefold())

    lines = raw.split("\n")
    if not any(match(line) for line in lines):
        return [{"source": fallback or UNSIGNED, "text": raw.strip()}]
    current = fallback or UNSIGNED
    items: list[dict] = []
    for line in lines:
        hit = match(line)
        if hit:
            current = hit
            continue
        body = _clean_line(line)
        if not body or _is_date_line(body):
            continue
        items.append({"source": current, "text": body})
    return items


def add_items(day: str, items: list[dict]) -> dict:
    if not items:
        raise ValueError("没有可保存的条目")
    if len(items) > MAX_ITEMS:
        raise ValueError(f"一次最多 {MAX_ITEMS} 条")
    prepared = []
    for raw in items:
        source = db.canonical_source(raw.get("source") or UNSIGNED)
        text = raw.get("text") or ""
        media = raw.get("media")
        if media is not None and not isinstance(media, str):
            raise ValueError("media 无效")
        if not str(text).strip() and not media:
            continue
        prepared.append(
            _classified(
                day,
                source,
                str(text),
                is_important(str(text), raw.get("important")),
                media if isinstance(media, str) and media else None,
            )
        )
    if not prepared:
        raise ValueError("没有可保存的正文")
    ts = db.now_iso()
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        db.ensure_day_row(conn, day, ts)
        order = db.next_ord(conn, day)
        for i, item in enumerate(prepared):
            db.touch_roster(conn, item["source"], ts)
            db.insert_snippet_row(conn, day, item, order + i)
        _refresh_raw(conn, day)
    return payload(day)


def replace_group(day: str, source: str, items: list[dict]) -> dict:
    source = db.canonical_source(source)
    if len(items) > MAX_ITEMS:
        raise ValueError(f"一次最多 {MAX_ITEMS} 条")
    ts = db.now_iso()
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = db.snippet_rows(conn, day)
        mine = [r for r in rows if (r["source"] or "").casefold() == source.casefold()]
        by_id = {r["id"]: r for r in mine}
        first_pos = next((i for i, r in enumerate(rows) if r["id"] in by_id), len(rows))
        before_ids = [r["id"] for r in rows[:first_pos] if r["id"] not in by_id]
        after_ids = [r["id"] for r in rows[first_pos:] if r["id"] not in by_id]
        known_media = {m for r in rows if (m := _media_of(r))}
        keep: set[int] = set()
        rebuilt: list[int] = []
        db.ensure_day_row(conn, day, ts)
        db.touch_roster(conn, source, ts)
        for raw in items:
            text = raw.get("text") or ""
            media = raw.get("media") if isinstance(raw.get("media"), str) and raw.get("media") else None
            if media and media not in known_media and not _media_file_ok(media):
                raise ValueError("图片不存在")
            if not str(text).strip() and not media:
                continue
            sid = raw.get("id")
            important = is_important(str(text), raw.get("important"))
            old = None
            if sid is not None:
                old = by_id.get(int(sid))
                if old is None:
                    raise ValueError(f"条目 {sid} 不属于 {source}")
                keep.add(old["id"])
                classified = _classified(
                    day,
                    source,
                    str(text),
                    important,
                    media or _media_of(old),
                    db._loads(old["extras_json"], {}),
                )
                conn.execute(
                    """
                    UPDATE snippets SET source=?, text=?, layer=?, layers_json=?, industries_json=?,
                        tickers_json=?, sentiment=?, stance=?, mood=?, actions_json=?, weight=?, extras_json=?
                    WHERE id=?
                    """,
                    (
                        source,
                        classified["text"],
                        classified["layer"],
                        db._j(classified.get("layers") or []),
                        db._j(classified.get("industries") or []),
                        db._j(classified.get("tickers") or []),
                        float(classified.get("sentiment") or 0),
                        classified.get("stance") or "hold",
                        classified.get("mood") or "中性",
                        db._j(classified.get("actions") or []),
                        float(classified.get("weight") or 1),
                        db._j(classified["extras"]),
                        old["id"],
                    ),
                )
                rebuilt.append(old["id"])
            else:
                classified = _classified(day, source, str(text), important, media)
                new_id = db.insert_snippet_row(conn, day, classified, 0)
                rebuilt.append(new_id)
                if media:
                    known_media.add(media)
        dropped = [r for r in mine if r["id"] not in keep]
        for row in dropped:
            db.delete_snippet_row(conn, row["id"])
        db.reassign_ord(conn, day, before_ids + rebuilt + after_ids)
        _refresh_raw(conn, day)
    return payload(day)


def delete_item(sid: int) -> dict:
    db.init_db()
    ts = db.now_iso()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = db.delete_snippet_row(conn, sid)
        if not row:
            return {}
        day = row["date"]
        db.ensure_day_row(conn, day, ts)
        _refresh_raw(conn, day)
        rest = db.snippet_rows(conn, day)
        if not rest:
            conn.execute("DELETE FROM days WHERE date=?", (day,))
            _unlink_media(_media_of(row))
            return {"date": day, "items": [], "roster": db.list_roster(), "pinned": db.pinned_roster()}
        _unlink_media(_media_of(row))
    return payload(day)


def clear_day(day: str) -> dict:
    db.init_db()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = db.delete_day_snippets(conn, day)
    for row in rows:
        _unlink_media(_media_of(row))
    return {"date": day, "items": [], "roster": db.list_roster(), "pinned": db.pinned_roster()}


def save_media(day: str, source: str, data: bytes, filename: str | None = None) -> dict:
    if len(data) > MAX_MEDIA:
        raise ValueError("图片超过 8MB")
    ext, _mime = sniff_image(data)
    name = secrets.token_hex(8) + ext
    if filename and MEDIA_NAME_RE.fullmatch(Path(filename).name):
        stem = Path(filename).stem
        if stem:
            name = secrets.token_hex(4) + "-" + re.sub(r"[^A-Za-z0-9._-]+", "", stem)[:24] + ext
            if not MEDIA_NAME_RE.fullmatch(name):
                name = secrets.token_hex(8) + ext
    folder = db.media_root() / day
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    rel = f"{day}/{name}"
    try:
        return add_items(day, [{"source": source, "text": "", "media": rel}])
    except Exception:
        path.unlink(missing_ok=True)
        raise


def media_path(day: str, name: str) -> Path:
    if not MEDIA_NAME_RE.fullmatch(name):
        raise ValueError("文件名无效")
    path = (db.media_root() / day / name).resolve()
    root = db.media_root().resolve()
    if root not in path.parents and path.parent != root:
        raise ValueError("文件名无效")
    return path
