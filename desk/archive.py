# -*- coding: utf-8 -*-
"""归档：本地 markdown + 可选 Notion REST。MCP 归档由 agent 走另一条路径。"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .db import ROOT, mark_archived

ARCHIVE = ROOT / "data" / "archive"
SETTINGS = ROOT / "data" / "settings.json"


def load_settings() -> dict:
    if SETTINGS.exists():
        try:
            return json.loads(SETTINGS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def render_markdown(day: dict) -> str:
    c = day.get("conclusion") or {}
    lines = [
        f"# 交易笔记 {day['date']}",
        "",
        f"- 状态：{day.get('status')}",
        f"- 立场：{c.get('stance') or '—'}",
        f"- 情绪：{c.get('overall_mood') or '—'}",
        "",
        "## 综合结论",
        "",
        c.get("headline") or "尚未综合",
        "",
        c.get("macro_view") or "",
        "",
    ]
    if c.get("note"):
        lines += [str(c["note"]), ""]
    if c.get("conditional_plans"):
        lines += ["", "### 条件计划", ""]
        for plan in c["conditional_plans"]:
            if isinstance(plan, dict):
                labels = {"theme": "主线", "viewpoint": "观点", "condition": "触发", "trigger": "触发", "invalidation": "失效", "action": "应对", "response": "应对", "source_ids": "依据"}
                lines.append("- " + "；".join(f"{labels.get(k, k)}：{', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in plan.items() if v))
            else:
                lines.append(f"- {plan}")
    if c.get("source_ids"):
        lines += ["", "依据卡片：" + ", ".join(map(str, c["source_ids"])), ""]
    if c.get("prefer"):
        lines.append("**偏向：** " + "、".join(c["prefer"]))
    if c.get("avoid"):
        lines.append("**避开：** " + "、".join(c["avoid"]))
    if c.get("actions"):
        lines += ["", "### 作者动作", ""]
        for a in c["actions"]:
            bit = a.get("ticker") or ""
            if a.get("from"):
                bit = f"{a['from']} → {a.get('ticker')}"
            lines.append(f"- `{a.get('action')}` {bit} — {a.get('note') or ''}（{a.get('source') or ''}）")
            if a.get("source_ids"):
                lines.append("  依据卡片：" + ", ".join(map(str, a["source_ids"])))
    if c.get("risks"):
        lines += ["", "### 风险", ""]
        for r in c["risks"]:
            lines.append(f"- {r}")

    def dump_layer(title: str, key: str) -> None:
        lines.extend(["", f"## {title}", ""])
        rows = [s for s in day.get("snippets") or [] if s.get("layer") == key]
        if not rows:
            lines.append("_无_")
            return
        for s in rows:
            tick = " ".join(s.get("tickers") or [])
            lines.append(f"### {s['source']} · {s['mood']} {tick}".rstrip())
            lines.append("")
            lines.append(s["text"])
            extras = s.get("extras") or {}
            viewpoint = s.get("viewpoint") or extras.get("viewpoint")
            if viewpoint:
                lines.extend(["", f"**观点提炼：** {viewpoint}"])
            lines.extend(["", f"卡片编号：{s.get('id', '—')}"])
            for e in s.get("expansions") or []:
                lines.append("")
                lines.append(f"> 外调 · {e['provider']}")
                lines.append(">")
                for para in (e.get("result") or "").split("\n"):
                    lines.append(f"> {para}")
            lines.append("")

    dump_layer("宏观层", "macro")
    dump_layer("行业层", "industry")
    dump_layer("个股层", "stock")
    lines += ["", "## 原文", "", "```", day.get("raw") or "", "```", ""]
    return "\n".join(lines).strip() + "\n"


def save_local(day: dict) -> Path:
    day_key = date.fromisoformat(day["date"]).isoformat()
    if day_key != day["date"]:
        raise ValueError("日期须为 YYYY-MM-DD")
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{day_key}.md"
    path.write_text(render_markdown(day), encoding="utf-8")
    return path


def notion_ready() -> tuple[bool, str]:
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    if not token:
        return False, "未配置 NOTION_TOKEN。本地 markdown 仍会保存；也可让 Cursor 用 MCP 写入笔记库。"
    return True, "Notion token 可用"


def _notion_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def push_notion_rest(day: dict) -> dict[str, Any]:
    """有内部集成 token 时直写。没有 token 时不要调用。"""
    token = (os.environ.get("NOTION_TOKEN") or "").strip()
    settings = load_settings()
    db_id = (os.environ.get("NOTION_DATABASE_ID") or settings.get("notion", {}).get("database_id") or "").replace("-", "")
    if not token:
        raise RuntimeError("NOTION_TOKEN empty")
    if not db_id:
        raise RuntimeError("NOTION_DATABASE_ID empty")
    c = day.get("conclusion") or {}
    actions = c.get("actions") or []
    action_txt = "；".join(
        f"{a.get('action')} {a.get('from') or ''} {a.get('ticker') or ''}".strip()
        for a in actions[:8]
    )
    props = {
        "标题": {"title": [{"text": {"content": f"{day['date']} {c.get('headline') or '交易笔记'}"[:80]}}]},
        "日期": {"date": {"start": day["date"]}},
        "状态": {"select": {"name": "已归档"}},
        "情绪": {"select": {"name": c.get("overall_mood") or "中性"}},
        "立场": {"select": {"name": c.get("stance") or "中性"}},
        "宏观要点": {"rich_text": [{"text": {"content": (c.get("macro_view") or "")[:1800]}}]},
        "行业要点": {
            "rich_text": [{"text": {"content": "、".join(c.get("prefer") or [])[:1800]}}]
        },
        "个股动作": {"rich_text": [{"text": {"content": action_txt[:1800]}}]},
        "风险": {"rich_text": [{"text": {"content": "；".join(c.get("risks") or [])[:1800]}}]},
    }
    md = render_markdown(day)
    # 保守限制每批 25 blocks，兼顾 500KB 请求上限及中文 JSON 转义。
    batch_size = 25
    children = [
        {
            "object": "block", "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": md[i:i + 1800]}}]},
        }
        for i in range(0, len(md), 1800)
    ]
    body = {"parent": {"database_id": db_id}, "properties": props, "children": children[:batch_size]}
    with httpx.Client(timeout=40.0) as client:
        r = client.post("https://api.notion.com/v1/pages", headers=_notion_headers(token), json=body)
        if r.status_code >= 300:
            raise RuntimeError(f"Notion HTTP {r.status_code}")
        data = r.json()
        page_id = data.get("id")
        if len(children) > batch_size and not page_id:
            raise RuntimeError("Notion 未返回页面 ID，正文未完整写入")
        for i in range(batch_size, len(children), batch_size):
            r = client.patch(f"https://api.notion.com/v1/blocks/{page_id}/children",
                             headers=_notion_headers(token), json={"children": children[i:i + batch_size]})
            if r.status_code >= 300:
                raise RuntimeError(f"Notion 正文追加失败 HTTP {r.status_code}；远端页面可能仅含部分内容，本地 Markdown 完整保留")
    return {"url": data.get("url"), "id": page_id}


def archive_day(day: dict, to_notion: bool = False) -> dict[str, Any]:
    path = save_local(day)
    notion_url = None
    notion_error = None
    if to_notion:
        try:
            pushed = push_notion_rest(day)
            notion_url = pushed.get("url")
        except Exception as e:  # noqa: BLE001
            notion_error = str(e)
    packed = mark_archived(day["date"], str(path), notion_url)
    return {
        "day": packed,
        "archive_path": str(path),
        "markdown": path.read_text(encoding="utf-8"),
        "notion_url": notion_url,
        "notion_error": notion_error,
        "notion_payload": _mcp_payload(day),
    }


def _mcp_payload(day: dict) -> dict[str, Any]:
    """给 Cursor Notion MCP 用的结构化稿，不依赖内部集成 token。"""
    c = day.get("conclusion") or {}
    settings = load_settings()
    return {
        "parent_data_source_id": settings.get("notion", {}).get("data_source_id"),
        "database_url": settings.get("notion", {}).get("database_url"),
        "properties": {
            "标题": f"{day['date']} {c.get('headline') or '交易笔记'}"[:80],
            "date:日期:start": day["date"],
            "date:日期:is_datetime": 0,
            "状态": "已归档",
            "情绪": c.get("overall_mood") or "中性",
            "立场": c.get("stance") or "中性",
            "宏观要点": (c.get("macro_view") or "")[:1800],
            "行业要点": "、".join(c.get("prefer") or []),
            "个股动作": "；".join(
                f"{a.get('action')} {a.get('ticker') or ''}".strip()
                for a in (c.get("actions") or [])[:8]
            ),
            "风险": "；".join(c.get("risks") or []),
        },
        "content": render_markdown(day),
    }
