# -*- coding: utf-8 -*-
"""手动触发的外调：DeepSeek 联网 / 不联网推演 / 粘贴证据。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

DEEPSEEK_DIR = Path(r"D:\API\deepseek")


def deepseek_available() -> tuple[bool, str]:
    if not DEEPSEEK_DIR.exists():
        return False, "找不到 D:\\API\\deepseek"
    try:
        if str(DEEPSEEK_DIR) not in sys.path:
            sys.path.insert(0, str(DEEPSEEK_DIR))
        from deepseek_web import load_api_key, load_local_config  # type: ignore

        load_api_key(load_local_config())
        return True, "DeepSeek 密钥可读"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def _ask(user: str, *, web_search: bool, system: str) -> dict[str, Any]:
    if str(DEEPSEEK_DIR) not in sys.path:
        sys.path.insert(0, str(DEEPSEEK_DIR))
    from deepseek_web import ask  # type: ignore

    return ask(user, system=system, web_search=web_search, require_search=web_search,
               **({"timeout": 20, "max_tokens": 5000, "thinking": False} if not web_search else {}))


def expand_deepseek_web(snippet: dict, extra_query: str = "") -> dict[str, Any]:
    q = extra_query.strip() or (
        f"请核实并展开下面这条投研笔记。只查公开事实（利率、油价、个股基本面、近期新闻），"
        f"给出：1) 事实核对 2) 对原观点的支持或证伪 3) 还缺什么。不要荐股口号。\n"
        f"来源：{snippet.get('source')}\n层级：{snippet.get('layer')}\n原文：\n{snippet.get('text')}"
    )
    system = (
        "你是投研助理。涉及最新行情、利率、公告、新闻必须使用 web_search。"
        "禁止编造数字。中文，结论短，附来源标题或 URL。"
    )
    parsed = _ask(q, web_search=True, system=system)
    return {
        "text": parsed.get("text") or "",
        "meta": {
            "queries": parsed.get("queries") or [],
            "urls": parsed.get("urls") or [],
            "model": parsed.get("model"),
            "web_search": True,
        },
        "query": q[:500],
    }


def expand_deepseek_reason(snippet: dict, extra_query: str = "") -> dict[str, Any]:
    q = extra_query.strip() or (
        f"不要检索。只根据原文做逻辑展开：这条观点的推理链、可能证伪条件、"
        f"和宏观/行业/个股哪一层最相关。\n来源：{snippet.get('source')}\n原文：\n{snippet.get('text')}"
    )
    system = "你是交易台逻辑编辑。禁止编造未给出的价格与数据。中文短答。"
    parsed = _ask(q, web_search=False, system=system)
    return {
        "text": parsed.get("text") or "",
        "meta": {"model": parsed.get("model"), "web_search": False},
        "query": q[:500],
    }


def expand_paste(snippet: dict, extra_query: str, pasted: str) -> dict[str, Any]:
    body = (pasted or "").strip()
    if not body:
        raise ValueError("粘贴内容为空")
    note = extra_query.strip()
    text = body if not note else f"【检索说明】{note}\n\n{body}"
    return {
        "text": text,
        "meta": {"web_search": False, "manual": True},
        "query": note or f"手动证据 · {snippet.get('source')}",
    }


SYNTH_MODEL = "deepseek-flash"


def _plain(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _plain(item)))
    if value is None:
        return ""
    return str(value).strip()


def _json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


_LAYER_KEYS = {
    "macro": "macro",
    "industry": "industry",
    "stock": "stock",
    "宏观": "macro",
    "行业": "industry",
    "个股": "stock",
    "综合": "summary",
    "综合判断": "summary",
    "总结": "summary",
    "交易建议": "ideas",
    "交易机会": "ideas",
}
_HEADING = re.compile(
    r"^\s*(?:[#>*\-]+\s*)*(?:(?:\d+|[一二三四五六七八九十]+)[、.．)）]\s*)?"
    r"(综合判断|综合|总结|交易建议|交易机会|宏观|行业|个股)\*{0,2}\s*(?:[：:]\s*(.*))?$"
)


def _layers_from_mapping(data: dict[str, Any] | None) -> dict[str, str] | None:
    if not data:
        return None
    parts = {"macro": "", "industry": "", "stock": "", "summary": "", "ideas": ""}
    for key, value in data.items():
        slot = _LAYER_KEYS.get(str(key).strip())
        if slot and not parts[slot]:
            parts[slot] = _plain(value)
    nested = data.get("analysis") or data.get("判断")
    if isinstance(nested, dict) and not any(parts.values()):
        return _layers_from_mapping(nested)
    return parts if any(parts[k] for k in ("macro", "industry", "stock")) else None


def _layers_from_prose(text: str) -> dict[str, str]:
    parts = {"macro": "", "industry": "", "stock": "", "summary": "", "ideas": ""}
    current = ""
    for line in text.splitlines():
        match = _HEADING.match(line.strip())
        if match:
            current = _LAYER_KEYS[match.group(1)]
            rest = match.group(2) or ""
            if rest.strip():
                parts[current] = rest.strip()
            continue
        if current:
            parts[current] = (parts[current] + "\n" + line).strip()
    return parts


def _layers_from_text(text: str) -> dict[str, str]:
    mapped = _layers_from_mapping(_json_object(text))
    if mapped:
        return mapped
    return _layers_from_prose(text)


def synthesize_day(day: dict) -> dict[str, Any]:
    """把一天的笔记整份交给 deepseek-flash，并打开官方 web_search。"""
    snippets = [item for item in (day.get("snippets") or []) if str(item.get("text") or "").strip()]
    if not snippets:
        raise ValueError("今天还没有笔记")
    blocks: list[str] = []
    size = 0
    for snippet in snippets:
        block = f"[{snippet.get('id')}] {snippet.get('source') or '未署名'}\n{str(snippet.get('text') or '').strip()[:1500]}"
        if size + len(block) > 18000:
            break
        blocks.append(block)
        size += len(block)
    user = (
        f"日期：{day.get('date') or ''}\n"
        "下面是我这一天记下的盘面笔记（来自不同来源，可能零散、重复甚至互相矛盾）。"
        "先用 web_search 核对其中关键的宏观数据、行业新闻、个股公告和最新行情，再动笔。"
        "笔记里的数字只是待核实的线索，没查到就别当成事实。\n\n"
        + "\n\n".join(blocks)
    )
    system = (
        "你是一位资深的交易台策略主管，正在给自己的交易员写当日复盘。"
        "写作要求：像一个真正思考过的人在说话，而不是在逐条复述笔记。"
        "先找出今天这些材料背后的一两条主线逻辑（比如：什么驱动了什么、钱在往哪里走、市场在交易什么预期），"
        "再用这条主线把宏观、行业、个股串起来，讲清楚因果和传导链条；"
        "对互相矛盾的观点，要说出你更倾向哪边以及理由；对只是噪音的内容，直接略过。"
        "避免空话套话（如“值得关注”“存在不确定性”“建议谨慎”），每句话都要有具体内容。\n"
        "必须调用 web_search 核实，优先查最影响判断的关键事实，查完再写正文；禁止编造没有检索到的数字；不给具体买卖价位和仓位比例。\n"
        "输出纯文本，不要 JSON，严格按下面五个标题，每个标题单独占一行（可写成「综合判断：」）：\n"
        "综合判断：3-6 句，说清今天市场的核心矛盾/主线是什么，逻辑链怎么走，你的总体态度（进攻、防守、轮动或观望）和理由。\n"
        "宏观：宏观环境如何影响今天的主线，只写和主线有关的部分。\n"
        "行业：主线落到哪些行业，哪些是逻辑最顺的，哪些是被情绪带动但逻辑偏弱的。\n"
        "个股：笔记里提到的重点个股，各自在这条逻辑中的位置和看法。\n"
        "交易建议：按优先级从高到低列 3-5 条，每条单独一行，格式为"
        "「P1｜标的或方向｜一句话逻辑｜什么情况下做/验证信号｜什么情况下放弃」。"
        "P1 是今天最值得尝试、逻辑最清晰、赔率最好的机会；排序依据是逻辑强度、催化剂确定性和风险收益比。"
        "如果今天确实没有好机会，就直说并给出等待什么信号。"
    )
    if str(DEEPSEEK_DIR) not in sys.path:
        sys.path.insert(0, str(DEEPSEEK_DIR))
    from deepseek_web import ask

    parsed = ask(
        user,
        system=system,
        model=SYNTH_MODEL,
        web_search=True,
        require_search=True,
        timeout=600,
        max_tokens=64000,
        max_uses=8,
    )
    text = str(parsed.get("text") or "").strip()
    layers = _layers_from_text(text)
    if not any(layers.get(k) for k in ("macro", "industry", "stock")):
        # 检索把 token 或轮次用光（stop=tool_use/max_tokens）时，用检索到的标题补写一次，不再联网。
        found = "\n".join(f"- {u.get('title')} {u.get('url')}" for u in (parsed.get("urls") or [])[:20] if isinstance(u, dict))
        retry = ask(
            user + "\n\n已检索到的资料（只能引用这些，不得编造新数字）：\n" + (found or "（无）")
            + "\n\n不要再检索，直接按五个标题写出完整判断。",
            system=system,
            model=SYNTH_MODEL,
            web_search=False,
            timeout=600,
            max_tokens=64000,
        )
        retry_text = str(retry.get("text") or "").strip()
        if retry_text:
            text = retry_text
            layers = _layers_from_text(text)
            parsed = {**retry, "urls": parsed.get("urls") or [],
                      "thinking": (str(parsed.get("thinking") or "") + "\n\n" + str(retry.get("thinking") or "")).strip()}
    note = ""
    if not any(layers.values()):
        if not text:
            reason = str(parsed.get("stop_reason") or "没有正文")
            raise ValueError(f"DeepSeek 检索之后没有写出判断（{reason}）。")
        layers = {"macro": text, "industry": "", "stock": "", "summary": "", "ideas": ""}
        note = "模型没有按三段标题来写，原文先放在宏观下面。"
    urls: list[dict[str, str]] = []
    for item in parsed.get("urls") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("http") or url in {row["url"] for row in urls}:
            continue
        urls.append({"title": str(item.get("title") or url), "url": url})
    empty = "这一层没有写出判断。"
    return {
        "macro": layers["macro"] or empty,
        "industry": layers["industry"] or empty,
        "stock": layers["stock"] or empty,
        "summary": layers.get("summary", ""),
        "ideas": layers.get("ideas", ""),
        "thinking": str(parsed.get("thinking") or ""),
        "model": str(parsed.get("model") or SYNTH_MODEL),
        "urls": urls[:6],
        "note": note,
    }



def _clean_urls(items: Any, limit: int = 8) -> list[dict[str, str]]:
    urls: list[dict[str, str]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("http") or url in {row["url"] for row in urls}:
            continue
        urls.append({"title": str(item.get("title") or url), "url": url})
    return urls[:limit]


def _day_notes(day: dict, budget: int = 18000) -> str:
    blocks: list[str] = []
    size = 0
    for snippet in day.get("snippets") or []:
        body = str(snippet.get("text") or "").strip()
        if not body:
            continue
        block = f"[{snippet.get('id')}] {snippet.get('source') or '未署名'}\n{body[:1500]}"
        if size + len(block) > budget:
            break
        blocks.append(block)
        size += len(block)
    return "\n\n".join(blocks)


_SYNTH_LABELS = (("summary", "综合判断"), ("macro", "宏观"), ("industry", "行业"), ("stock", "个股"), ("ideas", "交易建议"))

CHAT_SYSTEM = (
    "你是我的交易台策略搭档。我们手上有今天的盘面笔记和你早些时候写的当日综合判断与交易建议（P1..P5）。"
    "现在我会就这些计划追问、挑战或补充信息。回答要求：直接回应我的问题，像一个真正想过的人说话，"
    "不复述已有内容；需要最新行情、公告、新闻时用 web_search 查，禁止编造没查到的数字；"
    "如果我的新信息改变了判断，明确说哪条计划要调整、怎么调整（优先级、信号或放弃条件）；"
    "不给具体买卖价位和仓位比例。中文，纯文本，可以分段，必要时用短列表。"
)


def chat_about_day(day: dict, synthesis: dict | None, history: list[dict], message: str) -> dict[str, Any]:
    """围绕当天综合继续对话：笔记 + 当前综合 + 历史对话 + 新问题，一并交给 deepseek-flash。"""
    message = (message or "").strip()
    if not message:
        raise ValueError("先写下想问的")
    notes = _day_notes(day, 14000) or "（今天还没有笔记）"
    if synthesis:
        synth = "\n\n".join(f"{label}：{str(synthesis.get(key) or '').strip()}" for key, label in _SYNTH_LABELS if str(synthesis.get(key) or "").strip())
    else:
        synth = "（还没有做当日综合）"
    turns = []
    for item in (history or [])[-16:]:
        who = "我" if item.get("role") == "user" else "你"
        turns.append(f"{who}：{str(item.get('content') or '').strip()[:4000]}")
    user = (
        f"日期：{day.get('date') or ''}\n\n【当天笔记】\n{notes}\n\n【你此前写的当日综合】\n{synth}\n\n"
        f"【我们之前的对话】\n{chr(10).join(turns) or '（还没有）'}\n\n【我现在想说的】\n{message}"
    )
    if str(DEEPSEEK_DIR) not in sys.path:
        sys.path.insert(0, str(DEEPSEEK_DIR))
    from deepseek_web import ask

    parsed = ask(user, system=CHAT_SYSTEM, model=SYNTH_MODEL, web_search=True, require_search=False,
                 timeout=600, max_tokens=32000, max_uses=5)
    text = str(parsed.get("text") or "").strip()
    urls = _clean_urls(parsed.get("urls"))
    thinking = str(parsed.get("thinking") or "")
    if not text:
        found = "\n".join(f"- {u['title']} {u['url']}" for u in urls)
        retry = ask(user + "\n\n已检索到的资料（只能引用这些）：\n" + (found or "（无）") + "\n\n不要再检索，直接回答。",
                    system=CHAT_SYSTEM, model=SYNTH_MODEL, web_search=False, timeout=600, max_tokens=32000)
        text = str(retry.get("text") or "").strip()
        thinking = (thinking + "\n\n" + str(retry.get("thinking") or "")).strip()
        parsed = {**retry, "model": retry.get("model") or parsed.get("model")}
    if not text:
        raise ValueError(f"DeepSeek 没有写出回复（{parsed.get('stop_reason') or '空回复'}）。")
    return {"content": text, "thinking": thinking, "urls": urls, "model": str(parsed.get("model") or SYNTH_MODEL)}


def conclude_with_llm(prompt: str) -> dict[str, Any]:
    system = (
        "你是交易台策略编辑。只输出 JSON 对象，不要 markdown 围栏。"
        "禁止编造名单里没有的股票。"
    )
    parsed = _ask(prompt, web_search=False, system=system)
    text = (parsed.get("text") or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
        else:
            raise ValueError("模型没有返回 JSON") from None
    if not isinstance(data, dict):
        raise ValueError("模型 JSON 必须是对象")
    data["engine"] = "llm"
    data["raw"] = text
    return data


PROVIDERS = [
    {
        "id": "deepseek_web",
        "label": "DeepSeek 联网核实",
        "hint": "查利率、油价、新闻、公告。手动点才会花额度。",
        "needs": "query optional",
    },
    {
        "id": "deepseek_reason",
        "label": "DeepSeek 逻辑展开",
        "hint": "不联网，只把这条的推理链写清楚。",
        "needs": "query optional",
    },
    {
        "id": "paste",
        "label": "粘贴外部证据",
        "hint": "智堡、进门纪要、自己查的结果，贴进来挂在这条卡片上。",
        "needs": "paste required",
    },
]
