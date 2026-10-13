import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import app
from desk import archive, db


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(archive, "ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr("desk.ingest.classify_batch", lambda _: (_ for _ in ()).throw(RuntimeError("mock offline")))
    with TestClient(app.app) as client:
        yield client


def test_api_flow(isolated, tmp_path):
    c = isolated
    assert c.post('/api/ingest', json={'text': '  test research  ', 'fallback_date': '2026-09-20'}).status_code == 200
    d = c.get('/api/days/2026-09-20').json()
    sid = d['snippets'][0]['id']
    assert d['conclusion']['engine'] == 'rule'
    assert d['snippets'][0]['text'] == 'test research'
    for url in ['/api/days/nope', '/api/days/2026-02-30/markdown']:
        assert c.get(url).status_code == 400
    for body, code in [({'text': 'x', 'fallback_date': 'bad'}, 400), ({'text': '  '}, 422), ({'text': 'x'*200001}, 422)]:
        assert c.post('/api/ingest', json=body).status_code == code
    assert c.patch(f'/api/snippets/{sid}', json={'stance': 'working'}).status_code == 422
    assert c.patch(f'/api/snippets/{sid}', json={}).status_code == 400
    r = c.patch(f'/api/snippets/{sid}', json={'mood': '偏空'})
    assert r.status_code == 200 and r.json()['conclusion']['engine'] == 'rule'
    r = c.post(f'/api/snippets/{sid}/expand', json={'provider': 'paste', 'paste': ' evidence '})
    assert r.status_code == 200 and '未自动吸收' in r.json()['conclusion']['note']
    assert c.post('/api/ingest', json={'text': 'test research', 'fallback_date': '2026-09-20', 'use_llm': False}).status_code == 200
    assert c.get('/api/days/2026-09-20').json()['expansion_count'] == 1
    assert len(c.get('/api/search', params={'q': ' research '}).json()['results']) == 1
    assert c.get('/api/search', params={'q': '%'}).json()['results'] == []
    with patch.object(app, 'analyze_day', side_effect=RuntimeError('offline')):
        r = c.post('/api/days/2026-09-20/conclude', json={})
        assert r.status_code == 200 and r.json()['conclusion']['engine'] == 'rule'
    r = c.get('/api/days/2026-09-20/markdown')
    assert r.status_code == 200 and '2026-09-20.md' in r.headers['content-disposition']
    assert not (tmp_path / 'archive').exists()
    r = c.post('/api/days/2026-09-20/archive', json={})
    assert r.status_code == 200 and (tmp_path / 'archive' / '2026-09-20.md').exists()
    with patch.object(app, 'load_settings', return_value={'notion': {'token': 'SECRET', 'database_url': 'javascript:bad'}}):
        h = c.get('/api/health').json()
        assert 'SECRET' not in json.dumps(h) and h['notion']['database_url'] is None


def test_notion_full_content_and_fallback(isolated, monkeypatch, tmp_path):
    isolated.post('/api/ingest', json={'text': 'research', 'fallback_date': '2026-09-20', 'use_llm': False})
    day = db.get_day('2026-09-20')
    day['raw'] = '研究' * 200000
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {'id': 'fake', 'url': 'https://notion.so/fake'}
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kw):
            calls.append(kw['json'])
            return Response()
        patch = post
    monkeypatch.setenv('NOTION_TOKEN', 'fake')
    monkeypatch.setenv('NOTION_DATABASE_ID', 'fake')
    with patch.object(archive.httpx, 'Client', Client):
        archive.push_notion_rest(day)
    assert len(calls) > 2
    assert all(len(x['children']) <= 25 for x in calls)
    assert all(len(json.dumps(x).encode()) < 500000 for x in calls)
    text = ''.join(b['paragraph']['rich_text'][0]['text']['content'] for x in calls for b in x['children'])
    assert text == archive.render_markdown(day)
    with patch.object(archive, 'push_notion_rest', side_effect=RuntimeError('partial append failed')):
        result = archive.archive_day(day, to_notion=True)
    assert result['notion_error'] == 'partial append failed' and result['notion_url'] is None
    assert (tmp_path / 'archive' / '2026-09-20.md').read_text(encoding='utf8') == archive.render_markdown(day)


def test_synthesis_persist_and_chat(isolated, tmp_path):
    c = isolated
    c.post('/api/ingest', json={'text': 'P1 test note', 'fallback_date': '2026-09-21', 'use_llm': False})
    assert c.get('/api/days/2026-09-21/synthesis').json() == {'date': '2026-09-21', 'result': None, 'chat': [], 'updated_at': None}
    fake = {'summary': '主线', 'macro': 'm', 'industry': 'i', 'stock': 's', 'ideas': 'P1｜A｜B｜C｜D', 'thinking': 't', 'model': 'x', 'urls': [], 'note': ''}
    with patch.object(app, 'synthesize_day', return_value=dict(fake)):
        r = c.post('/api/days/2026-09-21/synthesize', json={})
    assert r.status_code == 200 and r.json()['summary'] == '主线' and r.json()['created_at']
    got = c.get('/api/days/2026-09-21/synthesis').json()['result']
    assert '主线' in got['analysis'] and 'P1' in got['priorities'] and got['plan_review'] == '' and got['ideas'].startswith('P1')
    sent = []
    with patch.object(app, '_grokbot_env', return_value={'GROKBOT_WEBHOOK_URL': 'http://x', 'GROKBOT_WEBHOOK_AUTH': 'k'}), \
         patch.object(app, '_post_grokbot', side_effect=lambda u, a, p: sent.append(p) or 200):
        r = c.post('/api/days/2026-09-21/synthesis/chat', json={'message': 'P1 还做吗'})
        assert r.status_code == 200 and r.json()['chat'][-1]['content'] == 'P1 还做吗'
        assert c.post('/api/days/2026-09-21/grokbot').json()['ok']
        assert c.post('/api/days/2026-09-21/grokbot', json={'type': 'chat'}).status_code == 422
    assert sent[0] == {'day': '2026-09-21', 'source': 'desknote', 'type': 'chat', 'message': 'P1 还做吗'}
    assert sent[1] == {'day': '2026-09-21', 'source': 'desknote', 'type': 'summary'}
    from desk import synthesis_store
    synthesis_store.append_chat('2026-09-21', 'assistant', '回复')
    with patch.object(app, '_grokbot_env', return_value={'GROKBOT_WEBHOOK_URL': 'http://x', 'GROKBOT_WEBHOOK_AUTH': 'k'}), \
         patch.object(app, '_post_grokbot', side_effect=RuntimeError('down')):
        assert c.post('/api/days/2026-09-21/synthesis/chat', json={'message': 'x'}).status_code == 502
    stored = c.get('/api/days/2026-09-21/synthesis').json()
    assert [m['role'] for m in stored['chat']] == ['user', 'assistant', 'user']
    synthesis_store.save_sections('2026-09-21', analysis='## A', priorities='1. P', plan_review='R')
    stored = c.get('/api/days/2026-09-21/synthesis').json()
    assert stored['result']['plan_review'] == 'R' and stored['result']['model'] == 'Grok Bot' and len(stored['chat']) == 3
    assert (tmp_path / 'synthesis' / '2026-09-21.json').exists()
    assert c.post('/api/days/2026-09-21/synthesis/chat', json={'message': ''}).status_code == 422
    assert c.delete('/api/days/2026-09-21/synthesis/chat').json()['chat'] == []
    assert c.get('/static/app.js').headers['cache-control'] == 'no-cache'
