# -*- coding: utf-8 -*-
"""每天一份：最近一次综合判断 + 围绕它的对话。存在 data/synthesis/<day>.json。"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from desk import db

_LOCK = threading.Lock()


def _dir() -> Path:
    return Path(db.DATA) / "synthesis"


def _path(day: str) -> Path:
    return _dir() / f"{day}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load(day: str) -> dict[str, Any]:
    empty = {"date": day, "result": None, "chat": [], "updated_at": None}
    path = _path(day)
    if not path.exists():
        return empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    data["date"] = day
    data.setdefault("result", None)
    if not isinstance(data.get("chat"), list):
        data["chat"] = []
    data.setdefault("updated_at", None)
    return data


def _write(day: str, data: dict[str, Any]) -> dict[str, Any]:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = _now()
    tmp = folder / f".{day}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, _path(day))
    return data


def save_result(day: str, result: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        data = load(day)
        data["result"] = {**result, "created_at": _now()}
        return _write(day, data)


def append_chat(day: str, role: Any, content: str | None = None, **extra: Any) -> dict[str, Any]:
    """追加对话，保留已有 chat。两种用法：

        append_chat(day, "assistant", "回复的 markdown")      # 外部写入（role: user/assistant）
        append_chat(day, [{"role": "user", "content": "..."}])  # 旧用法：一次追加多条

    在项目根目录：python -c "from desk import synthesis_store as s; s.append_chat('2026-10-12', 'assistant', '回复')"
    """
    messages = role if isinstance(role, list) else [{"role": str(role), "content": content or "", **extra}]
    with _LOCK:
        data = load(day)
        stamp = _now()
        data["chat"].extend({**m, "at": m.get("at") or stamp} for m in messages)
        return _write(day, data)


def clear_chat(day: str) -> dict[str, Any]:
    with _LOCK:
        data = load(day)
        data["chat"] = []
        return _write(day, data)


# ---------------------------------------------------------------- 三栏页用的分节结果 & 外部写入助手

def _layers_to_md(r: dict[str, Any]) -> str:
    parts = []
    for key, title in (("summary", "综合判断"), ("macro", "宏观"), ("industry", "行业"), ("stock", "个股")):
        text = str(r.get(key) or "").strip()
        if text:
            parts.append(f"## {title}\n\n{text}")
    return "\n\n".join(parts)


def _ideas_to_md(text: str) -> str:
    lines = []
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "｜" in line or "|" in line:
            cells = [c.strip() for c in line.replace("|", "｜").split("｜")]
            head, rest = cells[0], [c for c in cells[1:] if c]
            labels = ["标的", "理由", "做的信号", "放弃条件"]
            body = "；".join(f"**{labels[i]}**：{c}" if i < len(labels) else c for i, c in enumerate(rest))
            lines.append(f"- **{head}**　{body}")
        else:
            lines.append(line)
    return "\n".join(lines)


def sections(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """给 result 补上 analysis / priorities / plan_review（旧格式向后兼容，不改原字段）。"""
    if not isinstance(result, dict):
        return result
    r = dict(result)
    if not r.get("analysis"):
        r["analysis"] = _layers_to_md(r) or str(r.get("markdown") or "").strip()
    if not r.get("priorities"):
        r["priorities"] = _ideas_to_md(r.get("ideas") or "")
    r.setdefault("plan_review", "")
    r.setdefault("model", "")
    return r


def save_sections(day: str, analysis: str = "", priorities: str = "", plan_review: str = "",
                  model: str = "Grok Bot", thinking: str = "") -> dict[str, Any]:
    """外部写入一份三栏结果（覆盖 result，保留 chat）。

    在项目根目录：
        python -c "from desk import synthesis_store as s; s.save_sections('2026-10-12', analysis='## 分析\\n...', priorities='1. ...', plan_review='...')"
    三段均为 markdown 字符串；model 显示在左栏。返回整份 day 数据。
    """
    result = {"analysis": analysis or "", "priorities": priorities or "", "plan_review": plan_review or "",
              "model": model or "Grok Bot", "thinking": thinking or "", "urls": [], "note": ""}
    return save_result(day, result)
