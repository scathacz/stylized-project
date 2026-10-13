# -*- coding: utf-8 -*-
"""速记检索：DeepSeek 官方 deepseek-flash，走 Anthropic Messages 的 web_search。"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

GEILI_BASE = "https://sub.geiliapi.com/v1"
FLASH_MODEL = "deepseek-v4.1-flash"
NATIVE_MODEL = "deepseek-flash"
SEARCH_MODEL = "gpt-5.6-luna"
DSH_ENV = Path.home() / ".dsh" / ".env"
SYSTEM = (
    "你是投研助理。涉及行情、利率、公告、新闻时必须调用 web_search，"
    "不要凭记忆编造数字和链接。拿到检索结果后用中文短答，先给结论，再写依据。"
    "回答用普通段落或条目，不要输出 DSML、XML 或工具调用标记。"
)
_INVOKE = re.compile(
    r"<\s*｜+\s*DSML\s*｜+\s*invoke\b([^>]*)>(.*?)<\s*/\s*｜+\s*DSML\s*｜+\s*invoke\s*>",
    re.S | re.I,
)
_PARAM = re.compile(
    r"<\s*｜+\s*DSML\s*｜+\s*parameter\b([^>]*)>(.*?)<\s*/\s*｜+\s*DSML\s*｜+\s*parameter\s*>",
    re.S | re.I,
)
_ATTR = re.compile(r"""name\s*=\s*["']([^"']+)["']""", re.I)
_DSML_TAG = re.compile(r"<\s*/?\s*｜+\s*DSML\s*｜+[^>]*>", re.I)
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "检索公开网页。query 用一句简短检索词。",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
}


def _dsh_value(name: str) -> str:
    if not DSH_ENV.is_file():
        return ""
    prefix = name + "="
    for line in DSH_ENV.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _key(env_name: str) -> str:
    return (os.environ.get(env_name) or _dsh_value(env_name)).strip()


def _chat_from_stream(lines: list[str]) -> dict[str, Any]:
    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    for raw in lines:
        line = raw.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        delta = ((obj.get("choices") or [{}])[0]).get("delta") or {}
        if not isinstance(delta, dict):
            continue
        if delta.get("content"):
            content.append(str(delta["content"]))
        for call in delta.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            slot = calls.setdefault(int(call.get("index") or 0), {"id": "", "name": "", "arguments": ""})
            if call.get("id"):
                slot["id"] = str(call["id"])
            fn = call.get("function") or {}
            if isinstance(fn, dict):
                if fn.get("name"):
                    slot["name"] += str(fn["name"])
                if fn.get("arguments"):
                    slot["arguments"] += str(fn["arguments"])
    tool_calls = [
        {
            "id": slot["id"] or f"search-{index}",
            "type": "function",
            "function": {"name": slot["name"] or "web_search", "arguments": slot["arguments"]},
        }
        for index, slot in sorted(calls.items())
        if slot["name"] or slot["arguments"]
    ]
    return _lift_dsml({"content": "".join(content), "tool_calls": tool_calls})


def _split_dsml(content: str) -> tuple[str, list[dict[str, Any]]]:
    """给力有时把 web_search 写成正文里的 DSML，而不是 tool_calls。"""
    calls: list[dict[str, Any]] = []
    for index, match in enumerate(_INVOKE.finditer(content or ""), start=1):
        name_attr = _ATTR.search(match.group(1) or "")
        name = name_attr.group(1) if name_attr else "web_search"
        if name != "web_search":
            continue
        params: dict[str, str] = {}
        for param in _PARAM.finditer(match.group(2) or ""):
            key_attr = _ATTR.search(param.group(1) or "")
            if key_attr:
                params[key_attr.group(1)] = param.group(2).strip()
        query = params.get("query") or params.get("q") or ""
        if not query:
            continue
        calls.append({
            "id": f"dsml-{index}",
            "type": "function",
            "function": {"name": "web_search", "arguments": json.dumps({"query": query}, ensure_ascii=False)},
        })
    prose = _INVOKE.sub("", content or "")
    prose = _DSML_TAG.sub("", prose).strip()
    return prose, calls


def _lift_dsml(message: dict[str, Any]) -> dict[str, Any]:
    prose, calls = _split_dsml(str(message.get("content") or ""))
    message = {**message, "content": prose}
    if calls and not message.get("tool_calls"):
        message["tool_calls"] = calls
    return message


def _chat(messages: list[dict[str, Any]], *, tools: bool) -> dict[str, Any]:
    key = _key("GEILI_DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("没有给力国模密钥 GEILI_DEEPSEEK_API_KEY")
    payload: dict[str, Any] = {
        "model": FLASH_MODEL,
        "messages": messages,
        "max_tokens": 1200,
        "thinking": {"type": "disabled"},
        "stream": True,
    }
    if tools:
        payload["tools"] = [SEARCH_TOOL]
        payload["tool_choice"] = "auto"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    last = "连接中断"
    lines: list[str] = []
    for attempt in range(2):
        lines = []
        try:
            timeout = httpx.Timeout(120.0, connect=20.0)
            with httpx.Client(timeout=timeout, proxy=None, trust_env=False, http2=False) as client:
                with client.stream("POST", f"{GEILI_BASE}/chat/completions", headers=headers, json=payload) as response:
                    if response.status_code != 200:
                        raise RuntimeError(f"Geili /chat/completions HTTP {response.status_code}")
                    for raw in response.iter_lines():
                        if raw.strip():
                            lines.append(raw)
        except httpx.HTTPError as exc:
            last = exc.__class__.__name__
            if lines:
                break
            if attempt == 0:
                time.sleep(0.6)
            continue
        break
    message = _chat_from_stream(lines)
    if not message["content"] and not message["tool_calls"]:
        raise RuntimeError(f"Geili /chat/completions {last}")
    return message


def _remember_item(output: list[dict[str, Any]], item: Any) -> None:
    if not isinstance(item, dict):
        return
    item_id = item.get("id")
    if item_id:
        for index, old in enumerate(output):
            if old.get("id") == item_id:
                output[index] = item
                return
    output.append(item)


def _absorb_response_event(obj: dict[str, Any], output: list[dict[str, Any]], texts: list[str]) -> dict[str, Any] | None:
    etype = str(obj.get("type") or "")
    if etype in {"response.output_item.done", "response.output_item.added"} or etype.startswith("response.web_search_call"):
        _remember_item(output, obj.get("item"))
    elif etype == "response.output_text.delta" and obj.get("delta"):
        texts.append(str(obj["delta"]))
    elif etype == "response.output_text.done" and obj.get("text"):
        whole = str(obj["text"])
        if whole != "".join(texts):
            texts[:] = [whole]
    elif etype == "response.completed":
        done = obj.get("response")
        if isinstance(done, dict) and (done.get("output") or done.get("output_text")):
            return done
    return None


def _merge_output(prior: list[dict[str, Any]], final: list[Any]) -> list[dict[str, Any]]:
    """收尾事件经常只剩 message，把前面已经收到的 web_search 留住。"""
    merged = [item for item in prior if isinstance(item, dict)]
    for item in final:
        _remember_item(merged, item)
    return merged


def _pack_response(done: dict[str, Any], output: list[dict[str, Any]], texts: list[str]) -> dict[str, Any]:
    final_output = _merge_output(output, list(done.get("output") or []))
    written = str(done.get("output_text") or "")
    extra = "".join(texts)
    if extra and extra not in written:
        written = f"{written}{extra}"
    return {**done, "output": final_output, "output_text": written}


def _responses(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """给力 /responses 会中途掐掉非流式连接。按 SSE 读，断线时留下已收到的检索。"""
    body = {**payload, "stream": True}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    last = "连接中断"
    for attempt in range(3):
        output: list[dict[str, Any]] = []
        texts: list[str] = []
        try:
            timeout = httpx.Timeout(180.0, connect=20.0)
            with httpx.Client(timeout=timeout, proxy=None, trust_env=False, http2=False) as client:
                with client.stream("POST", f"{GEILI_BASE}/responses", headers=headers, json=body) as response:
                    if response.status_code != 200:
                        last = f"HTTP {response.status_code}"
                        if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                            time.sleep(0.8 * (attempt + 1))
                            continue
                        raise RuntimeError(f"Geili /responses {last}")
                    kind = response.headers.get("content-type", "")
                    if "text/event-stream" not in kind:
                        data = response.json()
                        if not isinstance(data, dict):
                            raise RuntimeError("Geili 返回不是 JSON 对象")
                        return data
                    for raw in response.iter_lines():
                        line = raw.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(obj, dict):
                            continue
                        done = _absorb_response_event(obj, output, texts)
                        if done is not None:
                            return _pack_response(done, output, texts)
        except httpx.HTTPError as exc:
            last = exc.__class__.__name__
            if output or texts:
                return {"output": output, "output_text": "".join(texts)}
            if attempt < 2:
                time.sleep(0.8 * (attempt + 1))
            continue
        if output or texts:
            return {"output": output, "output_text": "".join(texts)}
        if attempt < 2:
            time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Geili /responses {last}")


def _hosted_search(query: str) -> dict[str, Any]:
    key = _key("GEILI_SUB2API_KEY") or _key("GEILI_KEY")
    if not key:
        raise RuntimeError("没有给力检索密钥 GEILI_SUB2API_KEY")
    payload = {
        "model": SEARCH_MODEL,
        "input": query,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": {"type": "web_search"},
        "reasoning": {"effort": "low"},
        "max_output_tokens": 800,
    }
    data = _responses(key, payload)
    urls: list[dict[str, str]] = []
    seen: set[str] = set()
    texts: list[str] = []
    searched = False

    def add(url: str, title: str = "", snippet: str = "") -> None:
        clean = (url or "").strip()
        if not clean.startswith("http") or clean in seen:
            return
        seen.add(clean)
        urls.append({"title": (title or clean).strip(), "url": clean, "snippet": (snippet or "").strip()})

    def take_sources(bucket: Any) -> None:
        if not isinstance(bucket, list):
            return
        for source in bucket:
            if isinstance(source, str):
                add(source)
            elif isinstance(source, dict):
                add(
                    str(source.get("url") or source.get("link") or ""),
                    str(source.get("title") or ""),
                    str(source.get("snippet") or source.get("text") or ""),
                )

    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if "web_search" in str(item.get("type") or ""):
            searched = True
            action = item.get("action") if isinstance(item.get("action"), dict) else {}
            for key in ("sources", "results", "citations"):
                take_sources(action.get(key))
                take_sources(item.get(key))
        if item.get("type") == "message":
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("text"):
                    texts.append(str(part["text"]))
                for note in part.get("annotations") or []:
                    if isinstance(note, dict) and note.get("url"):
                        add(str(note.get("url")), str(note.get("title") or ""))
    if not texts and data.get("output_text"):
        texts.append(str(data["output_text"]))
    body = "\n".join(texts).strip()
    if not urls:
        for found in re.findall(r"https?://[^\s)>\]]+", body):
            add(found.rstrip(".,;"))
    if not searched and not urls and not body:
        raise RuntimeError("给力没有执行 web_search")
    return {"text": body[:2000], "urls": urls[:6], "searched": searched or bool(urls)}


def _query_of(call: dict[str, Any], fallback: str) -> str:
    raw = ((call.get("function") or {}).get("arguments") or "").strip()
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        parsed = {}
    if isinstance(parsed, dict):
        text = str(parsed.get("query") or parsed.get("q") or "").strip()
        if text:
            return text[:300]
    return fallback[:300]


def _native_ask(user: str) -> dict[str, Any]:
    import sys

    from desk.expand import DEEPSEEK_DIR

    if str(DEEPSEEK_DIR) not in sys.path:
        sys.path.insert(0, str(DEEPSEEK_DIR))
    from deepseek_web import ask

    return ask(
        user,
        system=SYSTEM,
        model=NATIVE_MODEL,
        web_search=True,
        require_search=True,
        timeout=180,
        max_tokens=2500,
    )


def web_search(question: str, context: str = "") -> dict[str, Any]:
    text = (question or "").strip()
    background = (context or "").strip()
    if not text:
        raise ValueError("问题是空的")
    if len(text) > 4000:
        raise ValueError("问题太长")
    if len(background) > 12000:
        raise ValueError("材料太长")

    user = text
    if background:
        user = (
            "下面是我记下的材料，只作背景。请根据我的问题调用 web_search，"
            "不要把整段材料原样当成检索词。\n\n"
            f"材料：\n{background}\n\n问题：\n{text}"
        )
    parsed = _native_ask(user)
    answer = str(parsed.get("text") or "").strip()
    if not answer:
        raise RuntimeError("DeepSeek 没有返回正文")
    urls: list[dict[str, str]] = []
    for item in parsed.get("urls") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url.startswith("http") or url in {row["url"] for row in urls}:
            continue
        urls.append({"title": str(item.get("title") or url), "url": url})
    return {
        "text": answer,
        "queries": [str(item) for item in (parsed.get("queries") or [])][:8],
        "urls": urls[:6],
        "model": str(parsed.get("model") or NATIVE_MODEL),
        "provider": "deepseek",
        "note": "",
    }
