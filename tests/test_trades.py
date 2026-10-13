import pytest
from fastapi.testclient import TestClient

import app
from desk import archive, db, trade_store

RAW = """2026年10月12日

Fuelish
文化传媒这个板块：周一可以重点关注

狼大
首先大光出了小作文

TradingPlan
和林微纳：量放的有点大 没有过110压力 观察一下 不着急
生益科技: 回踩10日线低吸

VITO
国家队：静默增持
"""


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(archive, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr("desk.ingest.classify_batch", lambda _: (_ for _ in ()).throw(RuntimeError("mock offline")))
    with TestClient(app.app) as c:
        yield c


def test_parse_only_trading_plan():
    plans = trade_store.parse_trading_plan(RAW, ["Fuelish", "狼大", "TradingPlan", "VITO"])
    assert plans == ["和林微纳：量放的有点大 没有过110压力 观察一下 不着急", "生益科技: 回踩10日线低吸"]
    assert trade_store.parse_trading_plan(RAW) == plans  # 启发式也能切开
    assert trade_store.subject_of(plans[0]) == "和林微纳"
    assert trade_store.subject_of(plans[1]) == "生益科技"


def test_import_idempotent_and_dismissed(client):
    added = trade_store.import_plans("2026-10-12", RAW)
    assert len(added) == 2
    assert trade_store.import_plans("2026-10-12", RAW.replace("观察一下 不着急", "观察一下  不着急")) == []
    trade_store.delete(added[1]["id"])
    assert trade_store.import_plans("2026-10-12", RAW) == []
    assert [i["title"] for i in trade_store.list_items()] == ["和林微纳"]


def test_trade_api_flow(client):
    r = client.post("/api/notes/2026-10-12/items", json={"source": "TradingPlan", "text": "和林微纳：没有过110压力 观察"})
    assert r.status_code == 200
    items = client.get("/api/trades", params={"status": "open"}).json()["items"]
    assert [i["title"] for i in items] == ["和林微纳"] and items[0]["source_day"] == "2026-10-12"
    tid = items[0]["id"]
    assert client.post("/api/trades/import", json={}).json()["added"] == []
    m = client.post("/api/trades", json={"plan_text": "手动：试试"}).json()
    assert m["title"] == "手动" and client.post("/api/trades", json={}).status_code == 400
    t = client.post(f"/api/trades/{tid}/entries", json={"text": "放量过110", "kind": "跟踪"}).json()
    eid = t["entries"][0]["id"]
    t = client.patch(f"/api/trades/{tid}/entries/{eid}", json={"text": "放量过 110", "kind": "想法变化"}).json()
    assert t["entries"][0]["kind"] == "想法变化"
    assert client.get("/api/trades").json()["items"][0]["id"] == tid  # 最新动态在前
    t = client.post(f"/api/trades/{tid}/close", json={"closing_note": "等确认再上", "result": "盈"}).json()
    assert t["status"] == "closed" and t["closing_note"] == "等确认再上"
    assert [i["id"] for i in client.get("/api/trades", params={"status": "closed"}).json()["items"]] == [tid]
    assert client.get("/api/trades").json()["items"][-1]["id"] == tid  # 已了结在下
    assert client.post(f"/api/trades/{tid}/reopen").json()["status"] == "open"
    assert client.delete(f"/api/trades/{tid}/entries/{eid}").json()["entries"] == []
    assert client.delete(f"/api/trades/{tid}").status_code == 200
    assert client.get(f"/api/trades/{tid}").status_code == 404
    assert client.get("/trades").status_code == 200
