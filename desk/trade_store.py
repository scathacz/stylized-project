# -*- coding: utf-8 -*-
"""交易跟踪：把每天笔记里 TradingPlan 一节拆成一条条计划，记录跟踪、想法变化与了结心得。

存在 data/trades.json；线程安全（进程内锁）+ 原子写入（临时文件 + os.replace）。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from desk import db

_LOCK = threading.RLock()
PLAN_SOURCE = "TradingPlan"
_SKIP_LINES = {"[图]"}


def _path() -> Path:
    return Path(db.DATA) / "trades.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:12]


def normalize(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).casefold()


def dedupe_key(source_day: str | None, text: str) -> str:
    return f"{source_day or ''}|{normalize(text)}"


def _empty() -> dict[str, Any]:
    return {"items": [], "dismissed": []}


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    if not isinstance(data.get("items"), list):
        data["items"] = []
    if not isinstance(data.get("dismissed"), list):
        data["dismissed"] = []
    return data


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".trades.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 解析

def subject_of(text: str) -> str:
    line = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    m = re.match(r"^\s*([^：:]{1,16})\s*[：:]", line)
    if m:
        return m.group(1).strip().strip("*#- ")
    return line[:16]


def _looks_like_header(line: str) -> bool:
    return 0 < len(line) <= 12 and not re.search(r"[：:，,。；;！!？?\s]", line)


def parse_trading_plan(raw: str, authors: Iterable[str] | None = None) -> list[str]:
    """返回 raw 里 TradingPlan 一节的每一条（每个非空行一条）。

    作者标题行 = 前一行为空（或文首）且是已知作者；未给作者表时用“短、无标点”的启发式。
    """
    known = {a.casefold() for a in authors} if authors else None
    lines = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    in_plan = False
    for i, line in enumerate(lines):
        s = line.strip()
        prev_blank = i == 0 or not lines[i - 1].strip()
        if s and prev_blank:
            is_plan = s.casefold() == PLAN_SOURCE.casefold()
            is_header = is_plan or (s.casefold() in known if known is not None else _looks_like_header(s))
            if is_header:
                in_plan = is_plan
                continue
        if in_plan and s and s not in _SKIP_LINES:
            out.append(s)
    return out


# ---------------------------------------------------------------- 读取

def _activity(item: dict[str, Any]) -> str:
    stamps = [item.get("created_at") or "", item.get("closed_at") or "", item.get("updated_at") or ""]
    stamps += [e.get("at") or "" for e in item.get("entries") or []]
    return max(stamps)


def _decorate(item: dict[str, Any]) -> dict[str, Any]:
    it = dict(item)
    it["last_activity"] = _activity(item)
    return it


def list_items(status: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        items = [_decorate(i) for i in _load()["items"]]
    if status in ("open", "closed"):
        items = [i for i in items if i.get("status") == status]
    items.sort(key=lambda i: i["last_activity"], reverse=True)
    items.sort(key=lambda i: 0 if i.get("status") == "open" else 1)
    return items


def get(item_id: str) -> dict[str, Any] | None:
    with _LOCK:
        for it in _load()["items"]:
            if it.get("id") == item_id:
                return _decorate(it)
    return None


def _mutate(item_id: str, fn) -> dict[str, Any] | None:
    with _LOCK:
        data = _load()
        for it in data["items"]:
            if it.get("id") == item_id:
                result = fn(it)
                if result is False:
                    return None
                it["updated_at"] = _now()
                _write(data)
                return _decorate(it)
    return None


# ---------------------------------------------------------------- 写入

def _new_item(title: str, plan_text: str, source_day: str | None, origin: str) -> dict[str, Any]:
    plan_text = str(plan_text or "").strip()
    return {
        "id": _id(), "title": (str(title or "").strip() or subject_of(plan_text) or "未命名计划"),
        "plan_text": plan_text, "source_day": source_day, "origin": origin,
        "created_at": _now(), "updated_at": _now(), "status": "open", "entries": [],
        "closed_at": None, "closing_note": "", "result": "",
    }


def create(title: str = "", plan_text: str = "", source_day: str | None = None) -> dict[str, Any]:
    if not str(plan_text or "").strip() and not str(title or "").strip():
        raise ValueError("计划内容不能为空")
    item = _new_item(title, plan_text or title, source_day, "manual")
    with _LOCK:
        data = _load()
        data["items"].append(item)
        _write(data)
    return _decorate(item)


def import_plans(day: str, raw: str, authors: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """幂等：同一天、规范化后相同文本的计划不会重复导入；删除过的也不会回来。"""
    plans = parse_trading_plan(raw, authors)
    if not plans:
        return []
    added: list[dict[str, Any]] = []
    with _LOCK:
        data = _load()
        seen = {dedupe_key(i.get("source_day"), i.get("plan_text") or "") for i in data["items"]}
        seen.update(data["dismissed"])
        for text in plans:
            key = dedupe_key(day, text)
            if key in seen:
                continue
            seen.add(key)
            item = _new_item("", text, day, "import")
            data["items"].append(item)
            added.append(item)
        if added:
            _write(data)
    return [_decorate(a) for a in added]


def update(item_id: str, title: str | None = None, plan_text: str | None = None) -> dict[str, Any] | None:
    def fn(it):
        if title is not None and title.strip():
            it["title"] = title.strip()
        if plan_text is not None and plan_text.strip():
            it["plan_text"] = plan_text.strip()
    return _mutate(item_id, fn)


def delete(item_id: str) -> bool:
    with _LOCK:
        data = _load()
        keep = [i for i in data["items"] if i.get("id") != item_id]
        if len(keep) == len(data["items"]):
            return False
        gone = next(i for i in data["items"] if i.get("id") == item_id)
        if gone.get("origin") == "import":
            data["dismissed"].append(dedupe_key(gone.get("source_day"), gone.get("plan_text") or ""))
        data["items"] = keep
        _write(data)
        return True


def add_entry(item_id: str, text: str, kind: str = "跟踪") -> dict[str, Any] | None:
    text = str(text or "").strip()
    if not text:
        raise ValueError("内容不能为空")
    entry = {"id": _id(), "at": _now(), "kind": (str(kind or "").strip() or "跟踪")[:20], "text": text}
    return _mutate(item_id, lambda it: it.setdefault("entries", []).append(entry))


def edit_entry(item_id: str, entry_id: str, text: str | None = None, kind: str | None = None) -> dict[str, Any] | None:
    def fn(it):
        for e in it.get("entries") or []:
            if e.get("id") == entry_id:
                if text is not None and text.strip():
                    e["text"] = text.strip()
                if kind is not None and kind.strip():
                    e["kind"] = kind.strip()[:20]
                e["edited_at"] = _now()
                return None
        return False
    return _mutate(item_id, fn)


def delete_entry(item_id: str, entry_id: str) -> dict[str, Any] | None:
    def fn(it):
        before = len(it.get("entries") or [])
        it["entries"] = [e for e in it.get("entries") or [] if e.get("id") != entry_id]
        return False if len(it["entries"]) == before else None
    return _mutate(item_id, fn)


def close(item_id: str, closing_note: str = "", result: str = "") -> dict[str, Any] | None:
    def fn(it):
        it["status"] = "closed"
        it["closed_at"] = _now()
        it["closing_note"] = str(closing_note or "").strip()
        it["result"] = str(result or "").strip()
    return _mutate(item_id, fn)


def reopen(item_id: str) -> dict[str, Any] | None:
    def fn(it):
        it["status"] = "open"
        it["closed_at"] = None
    return _mutate(item_id, fn)
