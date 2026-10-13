# -*- coding: utf-8 -*-
"""Conservative date/source parsing; never discard an unrecognised short line."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .lexicon import KNOWN_SOURCES

KNOWN_CF = {s.casefold(): s for s in KNOWN_SOURCES}
DATE_RE = re.compile(r"^(?:(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?|(\d{1,2})月(\d{1,2})日)$")
SOURCE_RE = re.compile(r"^(?:来源|作者|source|author)\s*[:：]\s*(.{1,60})$", re.I)


@dataclass
class RawSnippet:
    day: date
    source: str
    text: str
    order: int = 0


@dataclass
class RawDay:
    day: date
    raw: str
    snippets: list[RawSnippet] = field(default_factory=list)


def _canonical_source(line: str) -> str | None:
    s = line.strip()
    explicit = SOURCE_RE.fullmatch(s)
    if explicit:
        return explicit.group(1).strip()
    return KNOWN_CF.get(s.casefold())


def is_source_line(line: str) -> bool:
    return _canonical_source(line) is not None


def _date_header(line: str, today: date) -> date | None:
    s = re.sub(r"^#{1,6}\s*", "", line.strip())
    s = re.sub(r"^(?:日期|date)\s*[:：]\s*", "", s, flags=re.I)
    s = s.strip("[]【】 ")
    s = re.sub(r"\s*(?:[（(]?(?:星期|周)[一二三四五六日天][）)]?)$", "", s).strip()
    relative = {"今天": 0, "今日": 0, "昨天": -1, "昨日": -1, "前天": -2, "明天": 1, "today": 0, "yesterday": -1, "tomorrow": 1}
    if s.lower() in relative:
        return today + timedelta(days=relative[s.lower()])
    m = DATE_RE.fullmatch(s)
    if not m:
        return None
    try:
        if m.group(1):
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return date(today.year, int(m.group(4)), int(m.group(5)))
    except ValueError as exc:
        raise ValueError(f"日期无效：{s}") from exc


def parse_notes(text: str, fallback_day: date | None = None) -> list[RawDay]:
    today = fallback_day or date.today()
    current_day = today
    current_source = "未署名"
    days: dict[date, RawDay] = {}
    raw: dict[date, list[str]] = {}
    buf: list[str] = []
    order = 0

    def flush() -> None:
        nonlocal order
        body = "\n".join(buf).strip()
        buf.clear()
        if body:
            order += 1
            item = days.setdefault(current_day, RawDay(current_day, ""))
            item.snippets.append(RawSnippet(current_day, current_source, body, order))

    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        header = _date_header(line, today)
        if header is not None:
            flush()
            current_day, current_source = header, "未署名"
            raw.setdefault(current_day, []).append(line)
            continue
        raw.setdefault(current_day, []).append(line)
        source = _canonical_source(line)
        if source:
            flush()
            current_source = source
        elif not line.strip():
            flush()
        else:
            buf.append(line)
    flush()
    for key, item in days.items():
        item.raw = "\n".join(raw.get(key, []))
    return sorted(days.values(), key=lambda x: x.day)
