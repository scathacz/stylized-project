# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from desk import lookup, notes as capture
from desk.archive import archive_day, load_settings, notion_ready, render_markdown
from desk.db import (
    add_expansion, add_lookup, complete_lookup, connect, get_day, init_db, list_days, list_lookups,
    save_conclusion, update_snippet,
)
from desk.expand import (
    PROVIDERS, deepseek_available, expand_deepseek_reason,
    expand_deepseek_web, expand_paste, synthesize_day,
)
from desk import synthesis_store, trade_store
from desk.ingest import analyze_day, ingest_text, mood_summary
from desk.strategy import rule_conclude

load_dotenv()
ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
SEED = ROOT / "data" / "seed.txt"
app = FastAPI(title="交易笔记", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.middleware("http")
async def _no_stale_static(request, call_next):
    # 前端改得勤：让浏览器每次都向服务器确认 /static 文件（ETag 命中仍是 304，很便宜）。
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class InputModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class IngestIn(InputModel):
    text: str = Field(min_length=1, max_length=200_000)
    fallback_date: str | None = Field(default=None, max_length=32)
    use_llm: bool = True


class ExpandIn(InputModel):
    provider: Literal["deepseek_web", "deepseek_reason", "paste"]
    query: str = Field(default="", max_length=4000)
    paste: str = Field(default="", max_length=100_000)


class ConcludeIn(InputModel):
    use_llm: bool = True


class ArchiveIn(InputModel):
    to_notion: bool = False


class PatchSnippetIn(InputModel):
    layer: Literal["macro", "industry", "stock"] | None = None
    mood: Literal["偏多", "偏空", "分歧", "中性"] | None = None
    stance: Literal["hold", "add", "reduce", "watch", "switch", "short"] | None = None


class NoteItemIn(InputModel):
    id: int | None = None
    source: str = Field(default="", max_length=60)
    text: str = Field(default="", max_length=200_000)
    important: bool = False
    media: str | None = Field(default=None, max_length=200)


class AddNotesIn(InputModel):
    source: str = Field(default="", max_length=60)
    text: str = Field(default="", max_length=200_000)
    important: bool = False
    media: str | None = Field(default=None, max_length=200)
    items: list[NoteItemIn] | None = None


class GroupIn(InputModel):
    source: str = Field(min_length=1, max_length=60)
    items: list[NoteItemIn]


class ChatIn(InputModel):
    message: str = Field(min_length=1, max_length=8000)


class TradeIn(InputModel):
    title: str = Field(default="", max_length=60)
    plan_text: str = Field(default="", max_length=20_000)
    source_day: str | None = Field(default=None, max_length=10)


class TradePatchIn(InputModel):
    title: str | None = Field(default=None, max_length=60)
    plan_text: str | None = Field(default=None, max_length=20_000)


class TradeEntryIn(InputModel):
    kind: str = Field(default="跟踪", max_length=20)
    text: str = Field(min_length=1, max_length=20_000)


class TradeEntryPatchIn(InputModel):
    kind: str | None = Field(default=None, max_length=20)
    text: str | None = Field(default=None, max_length=20_000)


class TradeCloseIn(InputModel):
    closing_note: str = Field(default="", max_length=20_000)
    result: str = Field(default="", max_length=40)


class TradeImportIn(InputModel):
    day: str | None = Field(default=None, max_length=10)


class LookupIn(InputModel):
    query: str = Field(min_length=1, max_length=4000)
    context: str = Field(default="", max_length=12000)
    client_id: str = Field(default="", max_length=40)


def valid_day(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError("日期须为 YYYY-MM-DD")
        return parsed.isoformat()
    except ValueError as exc:
        raise HTTPException(400, "无效日期，请使用 YYYY-MM-DD") from exc


def require_day(day: str) -> dict:
    packed = get_day(valid_day(day))
    if not packed:
        raise HTTPException(404, "没有这一天")
    return packed


def regenerate(packed: dict, status: str = "结论") -> dict:
    conclusion = rule_conclude(packed)
    if packed.get("expansion_count"):
        conclusion["note"] = f"已保存 {packed['expansion_count']} 条外调材料；当前规则结论依据原文，未自动吸收外调内容。可重新运行 AI 综合。"
    return save_conclusion(packed["date"], conclusion, status=status, expected_updated_at=packed["updated_at"])


def import_trades(days: list[str] | None = None) -> list[dict]:
    """把这些天（默认全部）TradingPlan 一节导入交易跟踪；幂等。失败不影响笔记保存。"""
    try:
        authors = list(capture.known_author_index().values())
        if days is None:
            days = [d["date"] for d in list_days()]
        added: list[dict] = []
        for day in days:
            packed = get_day(day)
            if packed:
                added += trade_store.import_plans(day, packed.get("raw") or "", authors)
        return added
    except Exception:  # noqa: BLE001 - 跟踪导入是附带动作
        return []


@app.on_event("startup")
def _startup() -> None:
    init_db()
    import_trades()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/note")
def note_page() -> FileResponse:
    return FileResponse(ROOT / "prototype" / "quick-note.html", headers={"Cache-Control": "no-cache"})


@app.get("/synthesis")
def synthesis_page() -> FileResponse:
    return FileResponse(STATIC / "synthesis.html", headers={"Cache-Control": "no-cache"})


@app.get("/trades")
def trades_page() -> FileResponse:
    return FileResponse(STATIC / "trades.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/health")
def health() -> dict:
    ok, _ = deepseek_available()
    n_ok, _ = notion_ready()
    notion_config = load_settings().get("notion") or {}
    candidate = notion_config.get("database_url") if isinstance(notion_config, dict) else None
    database_url = None
    if isinstance(candidate, str):
        try:
            parsed = urlparse(candidate)
            host = parsed.hostname or ""
            if parsed.scheme == "https" and not parsed.username and not parsed.password and (host in {"notion.so", "notion.site"} or host.endswith((".notion.so", ".notion.site"))):
                database_url = candidate
        except ValueError:
            pass
    return {
        "ok": True,
        "deepseek": {"ok": ok, "configured": ok, "detail": "已配置；调用失败时回退规则" if ok else "未配置，使用规则整理"},
        "notion": {"ok": n_ok, "configured": n_ok, "database_url": database_url, "detail": "已配置 Notion token" if n_ok else "未配置，支持本地 Markdown"},
        "providers": PROVIDERS,
    }


@app.get("/api/search")
def api_search(q: str = Query(default="", max_length=200)) -> dict:
    query = q.strip()
    if not query:
        return {"results": []}
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, date, source, text, layer, mood FROM snippets "
            "WHERE text LIKE ? ESCAPE '\\' OR source LIKE ? ESCAPE '\\' "
            "ORDER BY date DESC, ord, id LIMIT 50", (pattern, pattern)
        ).fetchall()
    return {"results": [dict(row) for row in rows]}


@app.get("/api/days")
def api_days() -> dict:
    return {"days": list_days()}


@app.get("/api/days/{day}")
def api_day(day: str) -> dict:
    packed = require_day(day)
    packed["mood"] = mood_summary(packed)
    return packed


@app.get("/api/days/{day}/markdown")
def api_markdown(day: str) -> Response:
    packed = require_day(day)
    return Response(render_markdown(packed), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{packed["date"]}.md"'})


def _note_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(400, str(exc))
    return HTTPException(500, "速记保存失败")


@app.get("/api/lookups")
def api_lookups(day: str | None = Query(default=None, max_length=10)) -> dict:
    if day:
        day = valid_day(day)
    return {"items": list_lookups(day=day)}


def _lookup_failure(exc: Exception) -> str:
    message = str(exc)
    if "sk-" in message or "api_key" in message.lower():
        message = "联网检索失败"
    return message[:240] or "联网检索失败"


@app.post("/api/notes/lookup")
async def api_note_lookup(body: LookupIn) -> dict:
    saved = add_lookup(context=body.context, question=body.query, client_id=body.client_id)
    try:
        result = await asyncio.to_thread(lookup.web_search, body.query, body.context)
    except ValueError as exc:
        complete_lookup(saved["id"], answer=str(exc), urls=[], queries=[], model="", provider="error")
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        message = _lookup_failure(exc)
        complete_lookup(saved["id"], answer=message, urls=[], queries=[], model="", provider="error")
        raise HTTPException(502, message) from exc
    complete_lookup(
        saved["id"],
        answer=str(result.get("text") or ""),
        urls=list(result.get("urls") or []),
        queries=list(result.get("queries") or []),
        model=str(result.get("model") or ""),
        provider=str(result.get("provider") or ""),
    )
    return {
        **result,
        "id": saved["id"],
        "created_at": saved["created_at"],
        "context": body.context,
        "question": body.query,
        "client_id": body.client_id,
    }


@app.get("/api/notes/{day}")
def api_notes(day: str) -> dict:
    return capture.payload(valid_day(day))


@app.get("/api/roster")
def api_roster() -> dict:
    from desk.db import list_roster, pinned_roster
    return {"roster": list_roster(), "pinned": pinned_roster()}


@app.post("/api/notes/{day}/items")
def api_add_notes(day: str, body: AddNotesIn) -> dict:
    valid_day(day)
    if body.items is not None:
        raw_items = [it.model_dump() for it in body.items]
    else:
        raw_items = capture.split_capture(body.text, body.source)
        if body.important:
            for item in raw_items:
                item["important"] = True
        if body.media and len(raw_items) == 1:
            raw_items[0]["media"] = body.media
    try:
        result = capture.add_items(day, raw_items)
    except ValueError as exc:
        raise _note_error(exc) from exc
    import_trades([day])
    return result


@app.put("/api/notes/{day}/group")
def api_replace_group(day: str, body: GroupIn) -> dict:
    valid_day(day)
    try:
        result = capture.replace_group(day, body.source, [it.model_dump() for it in body.items])
    except ValueError as exc:
        raise _note_error(exc) from exc
    import_trades([day])
    return result


@app.delete("/api/notes/{day}")
def api_clear_notes(day: str) -> dict:
    return capture.clear_day(valid_day(day))


@app.delete("/api/notes/items/{sid}")
def api_delete_note(sid: int) -> dict:
    packed = capture.delete_item(sid)
    if not packed:
        raise HTTPException(404, "条目不存在")
    return packed


@app.post("/api/notes/{day}/media")
async def api_note_media(
    day: str,
    source: str = Form(default=""),
    file: UploadFile = File(...),
) -> dict:
    valid_day(day)
    data = await file.read()
    try:
        return capture.save_media(day, source, data, file.filename)
    except ValueError as exc:
        raise _note_error(exc) from exc


@app.get("/api/notes/media/{day}/{name}")
def api_note_file(day: str, name: str) -> FileResponse:
    valid_day(day)
    try:
        path = capture.media_path(day, name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path)


@app.post("/api/ingest")
async def api_ingest(body: IngestIn) -> dict:
    try:
        fb = date.fromisoformat(valid_day(body.fallback_date)) if body.fallback_date is not None else None
        saved = await asyncio.to_thread(ingest_text, body.text, fallback=fb, use_llm=body.use_llm)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    import_trades([str(d) for d in saved] if isinstance(saved, list) else None)
    return {"dates": saved, "days": list_days()}


@app.post("/api/snippets/{sid}/expand")
async def api_expand(sid: int, body: ExpandIn) -> dict:
    snippet = None
    for d in list_days():
        packed = get_day(d["date"])
        if packed:
            snippet = next((s for s in packed["snippets"] if s["id"] == sid), None)
        if snippet:
            break
    if not snippet:
        raise HTTPException(404, "卡片不存在")
    if body.provider == "paste" and not body.paste:
        raise HTTPException(400, "请粘贴外调材料")

    def run() -> dict:
        if body.provider == "deepseek_web":
            return expand_deepseek_web(snippet, body.query)
        if body.provider == "deepseek_reason":
            return expand_deepseek_reason(snippet, body.query)
        return expand_paste(snippet, body.query, body.paste)

    try:
        result = await asyncio.to_thread(run)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, "外调失败，请稍后重试或粘贴材料") from exc
    try:
        packed = add_expansion(sid, body.provider, result.get("query") or body.query,
                               result.get("text") or "", result.get("meta") or {})
    except KeyError as exc:
        raise HTTPException(404, "卡片不存在") from exc
    eid = packed.get("new_expansion_id")
    packed = regenerate(packed, status="展开")
    packed["new_expansion_id"] = eid
    packed["mood"] = mood_summary(packed)
    return packed


@app.patch("/api/snippets/{sid}")
def api_patch_snippet(sid: int, body: PatchSnippetIn) -> dict:
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(400, "至少提供一个修改字段")
    if body.layer:
        fields["layers"] = [body.layer]
    if body.mood:
        fields["sentiment"] = {"偏多": 0.5, "偏空": -0.5, "中性": 0.0, "分歧": 0.18}[body.mood]
    packed = update_snippet(sid, fields)
    if not packed:
        raise HTTPException(404, "卡片不存在")
    packed = regenerate(packed)
    packed["mood"] = mood_summary(packed)
    return packed


@app.post("/api/days/{day}/synthesize")
async def api_synthesize(day: str) -> dict:
    packed = require_day(day)
    try:
        result = await asyncio.to_thread(synthesize_day, packed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    saved = synthesis_store.save_result(packed["date"], result)
    return saved["result"]


def _grokbot_env() -> dict:
    """简单解析项目根目录的 .env（每次调用重新读，改了不用重启）。"""
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _post_grokbot(url: str, auth: str, payload: dict) -> int:
    import json as _json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        url, data=_json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": auth, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


class GrokIn(InputModel):
    type: Literal["summary", "chat"] = "summary"
    message: str = Field(default="", max_length=8000)


async def _fire_grokbot(day: str, kind: str = "summary", message: str = "") -> dict:
    env = _grokbot_env()
    url, auth = env.get("GROKBOT_WEBHOOK_URL", ""), env.get("GROKBOT_WEBHOOK_AUTH", "")
    if not url or not auth:
        raise HTTPException(500, "没有配置 GROKBOT_WEBHOOK_URL / GROKBOT_WEBHOOK_AUTH（.env）")
    payload = {"day": day, "source": "desknote", "type": kind}
    if kind == "chat":
        payload["message"] = message
    try:
        status = await asyncio.to_thread(_post_grokbot, url, auth, payload)
    except Exception as exc:  # 网络错误、超时等；不回显密钥
        raise HTTPException(502, f"连不上 Grok Bot：{type(exc).__name__}") from exc
    if not 200 <= status < 300:
        raise HTTPException(502, f"Grok Bot webhook 返回 HTTP {status}")
    return {"ok": True, "status": status}


@app.post("/api/days/{day}/grokbot")
async def api_grokbot(day: str, body: GrokIn | None = None) -> dict:
    day = valid_day(day)
    body = body or GrokIn()
    if body.type == "chat" and not body.message:
        raise HTTPException(422, "chat 需要 message")
    return await _fire_grokbot(day, body.type, body.message)


@app.get("/api/days/{day}/synthesis")
def api_synthesis(day: str) -> dict:
    data = synthesis_store.load(valid_day(day))
    data["result"] = synthesis_store.sections(data.get("result"))
    return data


@app.post("/api/days/{day}/synthesis/chat")
async def api_synthesis_chat(day: str, body: ChatIn) -> dict:
    """先把用户这句存进当天对话，再通知 Grok Bot（回复由外部用 append_chat 写回，前端轮询）。"""
    day = valid_day(day)
    saved = synthesis_store.append_chat(day, "user", body.message)
    sent = await _fire_grokbot(day, "chat", body.message)
    return {"ok": True, "status": sent["status"], "chat": saved["chat"]}


@app.delete("/api/days/{day}/synthesis/chat")
def api_synthesis_chat_clear(day: str) -> dict:
    return synthesis_store.clear_chat(valid_day(day))


@app.post("/api/days/{day}/conclude")
async def api_conclude(day: str, body: ConcludeIn) -> dict:
    packed = require_day(day)
    try:
        packed = await asyncio.to_thread(analyze_day, packed, use_llm=body.use_llm)
    except Exception:
        packed = regenerate(require_day(day))
        conclusion = dict(packed["conclusion"])
        conclusion["ai_error"] = "AI 分析暂不可用，已保留规则结论。"
        packed = save_conclusion(day, conclusion, expected_updated_at=packed["updated_at"])
    packed["mood"] = mood_summary(packed)
    return packed


@app.post("/api/days/{day}/archive")
def api_archive(day: str, body: ArchiveIn) -> dict:
    packed = require_day(day)
    if not packed.get("conclusion"):
        packed = regenerate(packed)
    result = archive_day(packed, to_notion=body.to_notion)
    result["mood"] = mood_summary(result["day"])
    return result


@app.post("/api/seed")
async def api_seed() -> dict:
    if not SEED.exists():
        raise HTTPException(404, "没有 data/seed.txt")
    try:
        saved = await asyncio.to_thread(ingest_text, SEED.read_text(encoding="utf-8"), use_llm=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"dates": saved, "days": list_days()}


# ---------------------------------------------------------------- 交易跟踪

def _trade_or_404(item: dict | None) -> dict:
    if not item:
        raise HTTPException(404, "交易不存在")
    return item


@app.get("/api/trades")
def api_trades(status: Literal["open", "closed", "all"] = "all") -> dict:
    return {"items": trade_store.list_items(None if status == "all" else status)}


@app.post("/api/trades")
def api_trade_create(body: TradeIn) -> dict:
    if body.source_day:
        valid_day(body.source_day)
    try:
        return trade_store.create(body.title, body.plan_text, body.source_day)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/trades/import")
def api_trade_import(body: TradeImportIn | None = None) -> dict:
    day = body.day if body else None
    added = import_trades([valid_day(day)] if day else None)
    return {"added": added, "items": trade_store.list_items()}


@app.get("/api/trades/{item_id}")
def api_trade(item_id: str) -> dict:
    return _trade_or_404(trade_store.get(item_id))


@app.patch("/api/trades/{item_id}")
def api_trade_patch(item_id: str, body: TradePatchIn) -> dict:
    return _trade_or_404(trade_store.update(item_id, body.title, body.plan_text))


@app.delete("/api/trades/{item_id}")
def api_trade_delete(item_id: str) -> dict:
    if not trade_store.delete(item_id):
        raise HTTPException(404, "交易不存在")
    return {"ok": True}


@app.post("/api/trades/{item_id}/entries")
def api_trade_entry(item_id: str, body: TradeEntryIn) -> dict:
    try:
        return _trade_or_404(trade_store.add_entry(item_id, body.text, body.kind))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/trades/{item_id}/entries/{entry_id}")
def api_trade_entry_patch(item_id: str, entry_id: str, body: TradeEntryPatchIn) -> dict:
    return _trade_or_404(trade_store.edit_entry(item_id, entry_id, body.text, body.kind))


@app.delete("/api/trades/{item_id}/entries/{entry_id}")
def api_trade_entry_delete(item_id: str, entry_id: str) -> dict:
    return _trade_or_404(trade_store.delete_entry(item_id, entry_id))


@app.post("/api/trades/{item_id}/close")
def api_trade_close(item_id: str, body: TradeCloseIn | None = None) -> dict:
    body = body or TradeCloseIn()
    return _trade_or_404(trade_store.close(item_id, body.closing_note, body.result))


@app.post("/api/trades/{item_id}/reopen")
def api_trade_reopen(item_id: str) -> dict:
    return _trade_or_404(trade_store.reopen(item_id))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8765, reload=True)
