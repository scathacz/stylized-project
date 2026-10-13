from io import BytesIO
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app
from desk import archive, db


PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc``\x00\x00"
    b"\x00\x02\x00\x01\xe5'\xde\xfc\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(archive, "ARCHIVE", tmp_path / "archive")
    with TestClient(app.app) as client:
        yield client


def test_enter_saves_structured_author_not_parsed(isolated):
    c = isolated
    with patch("desk.expand.conclude_with_llm", side_effect=AssertionError("no llm")):
        r = c.post(
            "/api/notes/2026-09-21/items",
            json={"source": "老王", "text": "看多光模块 但别追高"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["source"] == "老王"
    assert body["items"][0]["text"] == "看多光模块 但别追高"
    packed = c.get("/api/days/2026-09-21").json()
    assert packed["snippets"][0]["source"] == "老王"
    assert packed["snippets"][0]["text"] == "看多光模块 但别追高"


def test_right_pane_reads_and_edits_sync(isolated):
    c = isolated
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "债券市场比FED还鹰派"})
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "市场在杀融资"})
    got = c.get("/api/notes/2026-09-21").json()
    assert [i["text"] for i in got["items"]] == ["债券市场比FED还鹰派", "市场在杀融资"]
    sid = got["items"][0]["id"]
    r = c.put(
        "/api/notes/2026-09-21/group",
        json={
            "source": "VITO",
            "items": [
                {"id": sid, "text": "**债券市场比FED还鹰派**", "important": True},
                {"text": "新加的一句"},
            ],
        },
    )
    assert r.status_code == 200
    items = r.json()["items"]
    assert items[0]["id"] == sid
    assert items[0]["important"] is True
    assert items[0]["text"] == "**债券市场比FED还鹰派**"
    assert items[1]["text"] == "新加的一句"
    assert len(items) == 2
    again = c.get("/api/notes/2026-09-21").json()
    assert [i["text"] for i in again["items"]] == ["**债券市场比FED还鹰派**", "新加的一句"]


def test_unknown_author_and_casefold_and_duplicates(isolated):
    c = isolated
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "第一句"})
    r = c.post("/api/notes/2026-09-21/items", json={"source": "vito", "text": "第一句"})
    items = r.json()["items"]
    assert len(items) == 2
    assert {i["source"] for i in items} == {"VITO"}
    assert r.json()["roster"][:3] == ["TradingPlan", "Thought", "VITO"]
    assert r.json()["pinned"][0]["name"] == "TradingPlan"
    assert r.json()["pinned"][1]["kind"] == "thought"
    folded = c.post("/api/notes/2026-09-21/items", json={"source": "thought", "text": "先写计划再写理由"})
    assert folded.json()["items"][-1]["source"] == "Thought"


def test_media_roundtrip_and_clear(isolated, tmp_path):
    c = isolated
    r = c.post(
        "/api/notes/2026-09-21/media",
        data={"source": "Agent"},
        files={"file": ("shot.png", BytesIO(PNG), "image/png")},
    )
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["source"] == "Agent"
    assert item["media"].startswith("2026-09-21/")
    img = c.get("/api/notes/media/" + item["media"])
    assert img.status_code == 200
    assert img.content.startswith(b"\x89PNG")
    assert c.delete("/api/notes/2026-09-21").status_code == 200
    assert c.get("/api/notes/2026-09-21").json()["items"] == []
    assert c.get("/api/days").json()["days"] == []


def test_keeps_internal_newlines(isolated):
    c = isolated
    r = c.post("/api/notes/2026-09-21/items", json={"source": "换行", "text": "第一行\n\n第二行"})
    assert r.status_code == 200
    item = r.json()["items"][0]
    assert item["text"] == "第一行\n\n第二行"
    sid = item["id"]
    r = c.put(
        "/api/notes/2026-09-21/group",
        json={"source": "换行", "items": [{"id": sid, "text": "第一行\n第二行"}]},
    )
    assert r.json()["items"][0]["text"] == "第一行\n第二行"


def test_append_keeps_author_group_order(isolated):
    c = isolated
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "债券"})
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "融资"})
    c.post("/api/notes/2026-09-21/items", json={"source": "老王", "text": "光模块"})
    c.post("/api/notes/2026-09-21/items", json={"source": "VITO", "text": "日元"})
    texts = [i["text"] for i in c.get("/api/notes/2026-09-21").json()["items"] if i["source"] == "VITO"]
    assert texts == ["债券", "融资", "日元"]


SAMPLE = """策略
衍生品：黄金股比沪金有几个点折价 - 考虑到领先性，可能预期提前反应在股票上 - 黄金卖PUT是更好的方案
Beta因子全面走强，动量超额有恢复，削弱了微盘，加强了AI硬方向
博弈：MUSE - 利好海外算力基建
CPU之前跌的仓位拥挤 叙事没什么问题 - 但是海光这种反弹不对 海光高PE是错的分子

VITO
代工厂：价格里已经含了更高的预期，或者分母在收缩（比如中芯国际）
联电特色：绝缘体上硅工艺直接接到硅光与先进封装


Fuelish
接下来准备继续做成本的票是：
亨通 中材光迅 但是因为旅游可能没时间做，先观察一下(中材连续三天炸板都不补缺口，等一个低吸)

雨神
再格局两天 - 分歧中上涨 一致中出货

猫
下午的平衡被PCB打破了
张三
这句还是猫的，因为张三还不是作者
"""


def test_long_paste_splits_on_existing_authors(isolated):
    c = isolated
    r = c.post("/api/notes/2026-09-22/items", json={"source": "未署名", "text": SAMPLE})
    assert r.status_code == 200
    got = [(i["source"], i["text"]) for i in r.json()["items"]]
    assert got == [
        ("策略", "衍生品：黄金股比沪金有几个点折价 - 考虑到领先性，可能预期提前反应在股票上 - 黄金卖PUT是更好的方案"),
        ("策略", "Beta因子全面走强，动量超额有恢复，削弱了微盘，加强了AI硬方向"),
        ("策略", "博弈：MUSE - 利好海外算力基建"),
        ("策略", "CPU之前跌的仓位拥挤 叙事没什么问题 - 但是海光这种反弹不对 海光高PE是错的分子"),
        ("VITO", "代工厂：价格里已经含了更高的预期，或者分母在收缩（比如中芯国际）"),
        ("VITO", "联电特色：绝缘体上硅工艺直接接到硅光与先进封装"),
        ("Fuelish", "接下来准备继续做成本的票是："),
        ("Fuelish", "亨通 中材光迅 但是因为旅游可能没时间做，先观察一下(中材连续三天炸板都不补缺口，等一个低吸)"),
        ("雨神", "再格局两天 - 分歧中上涨 一致中出货"),
        ("猫", "下午的平衡被PCB打破了"),
        ("猫", "张三"),
        ("猫", "这句还是猫的，因为张三还不是作者"),
    ]
    c.post("/api/notes/2026-09-22/items", json={"source": "老王", "text": "先记一次"})
    pasted = "老王\n看多光模块 但别追高\nvito\n日元这句归到已有的 VITO"
    r = c.post("/api/notes/2026-09-22/items", json={"source": "未署名", "text": pasted})
    tail = [(i["source"], i["text"]) for i in r.json()["items"][-2:]]
    assert tail == [
        ("老王", "看多光模块 但别追高"),
        ("VITO", "日元这句归到已有的 VITO"),
    ]


def test_restore_deleted_line_without_old_id(isolated):
    c = isolated
    c.post("/api/notes/2026-09-22/items", json={"source": "策略", "text": "第一句"})
    c.post("/api/notes/2026-09-22/items", json={"source": "策略", "text": "第二句"})
    c.put("/api/notes/2026-09-22/group", json={"source": "策略", "items": [{"text": "第一句"}]})
    r = c.put("/api/notes/2026-09-22/group", json={"source": "策略", "items": [
        {"text": "第一句"}, {"text": "第二句"},
    ]})
    assert [i["text"] for i in r.json()["items"] if i["source"] == "策略"] == ["第一句", "第二句"]


def test_note_page_and_bad_date(isolated):
    c = isolated
    assert c.get("/note").status_code == 200
    assert "速记" in c.get("/note").text
    assert c.post("/api/notes/2026-02-30/items", json={"source": "A", "text": "x"}).status_code == 400
    assert c.post("/api/notes/2026-09-21/items", json={"source": "A", "text": ""}).status_code == 400


def test_lookup_uses_geili_flash(isolated):
    packed = {
        "text": "利差大约 2.4 个点",
        "queries": ["美日10年利差"],
        "urls": [{"title": "财政部", "url": "https://example.com/spread"}],
        "model": "deepseek-v4.1-flash",
        "provider": "geili",
        "note": "",
    }
    with patch("desk.lookup.web_search", return_value=packed):
        response = isolated.post("/api/notes/lookup", json={"query": "美日10年利差"})
    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "geili"
    assert body["model"] == "deepseek-v4.1-flash"
    assert body["text"] == "利差大约 2.4 个点"


def test_flash_search_uses_native_deepseek(monkeypatch):
    from desk import lookup

    seen = {}

    def ask(user):
        seen["user"] = user
        return {
            "text": "中间价 6.74",
            "queries": ["人民币中间价"],
            "urls": [{"title": "央行", "url": "https://example.com/pbc"}],
            "model": "deepseek-flash",
            "search_count": 1,
        }

    monkeypatch.setattr(lookup, "_native_ask", ask)
    result = lookup.web_search("今天中间价", "美元兑人民币")
    assert lookup.NATIVE_MODEL == "deepseek-flash"
    assert result["provider"] == "deepseek"
    assert result["model"] == "deepseek-flash"
    assert "今天中间价" in seen["user"]
    assert "美元兑人民币" in seen["user"]
    assert result["urls"][0]["url"] == "https://example.com/pbc"


def test_lookup_refuses_without_deepseek_key(monkeypatch):
    from desk import lookup

    def ask(user):
        raise RuntimeError("DeepSeek API Key 未配置")

    monkeypatch.setattr(lookup, "_native_ask", ask)
    with pytest.raises(RuntimeError, match="API Key"):
        lookup.web_search("中间价")


def test_lookup_rejects_blank(isolated):
    assert isolated.post("/api/notes/lookup", json={"query": "   "}).status_code == 422


def test_failed_lookup_still_keeps_today_material(isolated, monkeypatch):
    def boom(question, context=""):
        raise RuntimeError("检索超时")

    monkeypatch.setattr("desk.lookup.web_search", boom)
    response = isolated.post(
        "/api/notes/lookup",
        json={"query": "现在多少", "context": "美日利差那段笔记", "client_id": "L9"},
    )
    assert response.status_code == 502
    rows = isolated.get("/api/lookups").json()["items"]
    assert rows[0]["context"] == "美日利差那段笔记"
    assert rows[0]["question"] == "现在多少"
    assert rows[0]["client_id"] == "L9"
    assert rows[0]["provider"] == "error"


def test_lookup_saves_question_apart_from_the_note(isolated):
    packed = {
        "text": "大约两个点",
        "queries": ["美日利差"],
        "urls": [{"title": "财政部", "url": "https://example.com/spread"}],
        "model": "deepseek-v4.1-flash",
        "provider": "geili",
        "note": "",
    }
    with patch("desk.lookup.web_search", return_value=packed) as search:
        response = isolated.post("/api/notes/lookup", json={"query": "现在多少", "context": "美日利差那段笔记"})
    assert response.status_code == 200
    search.assert_called_once_with("现在多少", "美日利差那段笔记")
    body = response.json()
    assert body["question"] == "现在多少"
    assert body["context"] == "美日利差那段笔记"
    rows = isolated.get("/api/lookups").json()["items"]
    assert rows[0]["question"] == "现在多少"
    assert rows[0]["context"] == "美日利差那段笔记"
    assert rows[0]["answer"] == "大约两个点"
    assert rows[0]["id"] == body["id"]


def test_dsml_tool_markup_becomes_a_search_not_the_answer():
    import json
    from desk.lookup import _split_dsml

    raw = (
        "<｜｜DSML｜｜ calls>\n"
        '<｜｜DSML｜｜ invoke name="web_search">\n'
        '<｜｜DSML｜｜ parameter name="query" string="true">Codelco 2026上半年 铜产量 同比</｜｜DSML｜｜ parameter>\n'
        "</｜｜DSML｜｜ invoke>\n"
        '<｜｜DSML｜｜ invoke name="web_search">\n'
        '<｜｜DSML｜｜ parameter name="query" string="true">PT Smelting Gresik 停产 2026 铜</｜｜DSML｜｜ parameter>\n'
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    )
    prose, calls = _split_dsml(raw)
    assert prose == ""
    assert json.loads(calls[0]["function"]["arguments"])["query"] == "Codelco 2026上半年 铜产量 同比"
    assert json.loads(calls[1]["function"]["arguments"])["query"] == "PT Smelting Gresik 停产 2026 铜"


def test_dsml_answer_is_replaced_by_prose():
    import json

    from desk import lookup

    raw = (
        '<｜｜DSML｜｜ invoke name="web_search">'
        '<｜｜DSML｜｜ parameter name="query" string="true">Codelco 2026 铜产量</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    message = lookup._lift_dsml({"content": raw, "tool_calls": []})
    assert json.loads(message["tool_calls"][0]["function"]["arguments"])["query"] == "Codelco 2026 铜产量"
    assert "DSML" not in message["content"]


def test_chat_stream_joins_tool_arguments():
    from desk.lookup import _chat_from_stream

    message = _chat_from_stream([
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"web_search","arguments":""}}]}}]}',
        'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"query\\":\\"PCB 催化\\"}"}}]}}]}',
        "data: [DONE]",
    ])
    assert message["tool_calls"][0]["function"]["arguments"] == '{"query":"PCB 催化"}'
    assert message["tool_calls"][0]["id"] == "c1"


def test_response_stream_keeps_search_items():
    from desk.lookup import _absorb_response_event

    output, texts = [], []
    done = _absorb_response_event({
        "type": "response.output_item.done",
        "item": {"type": "web_search_call", "action": {"sources": [{"url": "https://example.com/a"}]}},
    }, output, texts)
    assert done is None
    assert output[0]["type"] == "web_search_call"
    _absorb_response_event({"type": "response.output_text.delta", "delta": "有催化"}, output, texts)
    finished = _absorb_response_event({
        "type": "response.completed",
        "response": {"output": [{"type": "message"}]},
    }, output, texts)
    assert finished["output"][0]["type"] == "message"
    assert texts == ["有催化"]


def test_completed_message_keeps_the_search_call():
    from desk.lookup import _pack_response

    packed = _pack_response(
        {"output": [{"type": "message", "content": [{"text": "供给偏紧"}]}]},
        [{"id": "ws1", "type": "web_search_call", "action": {"sources": [{"url": "https://example.com/a", "title": "ICSG"}]}}],
        ["供给偏紧"],
    )
    kinds = [item["type"] for item in packed["output"]]
    assert kinds == ["web_search_call", "message"]
    assert "供给偏紧" in packed["output_text"]


def test_search_text_without_call_is_still_usable(monkeypatch):
    from desk import lookup

    monkeypatch.setattr(lookup, "_key", lambda name: "test-key")
    monkeypatch.setattr(
        lookup,
        "_responses",
        lambda key, payload: {"output": [{"type": "message", "content": [{"text": "先看矿端会不会继续减产"}]}], "output_text": ""},
    )
    found = lookup._hosted_search("之后如何推演")
    assert "减产" in found["text"]


def test_context_is_background_not_the_search_query(monkeypatch):
    from desk import lookup

    seen = {}

    def ask(user):
        seen["user"] = user
        return {"text": "大约两个点", "queries": ["美日10年利差"], "urls": [], "model": "deepseek-flash", "search_count": 1}

    monkeypatch.setattr(lookup, "_native_ask", ask)
    result = lookup.web_search("现在多少", "美日十年利差的整段笔记")
    assert "美日十年利差的整段笔记" in seen["user"]
    assert "现在多少" in seen["user"]
    assert result["text"] == "大约两个点"
    assert result["provider"] == "deepseek"
