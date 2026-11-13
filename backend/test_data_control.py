import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from fastapi.testclient import TestClient

import main
from test_archive import create, overview
from test_memory import client, rows, send


def export(client, library='life'):
    response = client.get('/api/data/export', params={'library_id': library} if library else {})
    response.raise_for_status()
    assert 'no-store' in response.headers['cache-control']
    return response.json()


def erase(client, memory, scope='memory', block=True):
    return client.request('DELETE', f"/api/data/memories/{memory['id']}", json={
        'version': memory['version'], 'scope': scope, 'blockSource': block,
    })


def test_export_keeps_switched_conversation_in_its_message_scope(client):
    first, first_request = send(client, '我喜欢清晨读自然笔记。')
    assert first.status_code == 200
    cid = first_request['conversation']
    send(client, '我决定参加机器人研习。', 'study', conversation=cid, segment='study-segment')[0].raise_for_status()
    send(client, '这是未绑定内容。', None, conversation=cid, segment='unbound-segment')[0].raise_for_status()
    png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNwTTEDAAHRAODfYd9eAAAAAElFTkSuQmCC'
    day = main.archive.today(main.now()).isoformat()
    client.put('/api/planning/life/capsules', json={
        'id': 'album', 'kind': 'snapshot', 'title': '晨读照片', 'due': day,
        'images': [{'name': '树叶.png', 'data': png}],
    }).raise_for_status()

    life, study, unbound = export(client), export(client, 'study'), export(client, None)
    assert [item['cid'] for item in life['messages']] == [cid]
    assert life['messages'][0]['reply']['text']
    assert life['timeline'][0]['content'] == '我喜欢清晨读自然笔记'
    assert life['capsules'][0]['attachments'][0]['base64'] == png
    assert life['capsules'][0]['versions'][0]['images'][0]['name'] == '树叶.png'
    assert '机器人研习' not in json.dumps(life, ensure_ascii=False)
    assert '未绑定内容' not in json.dumps(life, ensure_ascii=False)
    assert study['decisions'][0]['title'] == '我决定参加机器人研习'
    assert not study['capsules'] and '自然笔记' not in json.dumps(study, ensure_ascii=False)
    assert [message['text'] for message in unbound['messages']] == ['这是未绑定内容。']
    assert not unbound['memories'] and not unbound['decisions']
    assert client.get('/api/data/export?library_id=missing').status_code == 404
    with TestClient(main.app) as other:
        assert other.get('/api/data/export?library_id=life').status_code == 401
        other.post('/api/bootstrap').raise_for_status()
        other.put('/api/libraries', json={'id': 'life', 'name': '另一个空间'}).raise_for_status()
        assert not export(other)['messages'] and not export(other)['capsules']


def test_erased_memory_stays_out_of_a_running_backfill_and_cached_reply(client, monkeypatch):
    client.put('/api/permissions', json={'health': True}).raise_for_status()
    original = '我习惯每天记录健康状态。'
    first, source = send(client, original)
    assert first.json()['extraction'] == 'recorded'
    memory, = rows(client)
    client.put(f"/api/memories/{memory['id']}/followup", json={'allowed': True}).raise_for_status()
    recall, replay = send(client, '你还记得什么？')
    assert memory['content'] in recall.json()['text']
    monkeypatch.setattr(main, 'schedule_backfill', lambda *_: None)
    job = client.post('/api/backfills', json={
        'library': 'life', 'category': 'health', 'start': 0, 'end': main.now() + 10000,
    }).json()
    who = client.get('/api/data/state').json()['workspace']
    entered, proceed = Event(), Event()

    def delayed_classifier(text):
        if text == original:
            entered.set()
            assert proceed.wait(5)
        return []

    monkeypatch.setattr(main, 'judge_memories', delayed_classifier)
    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(main.run_backfill, who, job['id'])
        try:
            assert entered.wait(5)
            deleted = erase(client, memory)
            deleted.raise_for_status()
            assert deleted.json()['updatedSources'] == [source['id']]
        finally:
            proceed.set()
        worker.result(timeout=5)

    assert not rows(client)
    assert not client.get('/api/followups?library_id=life').json()
    assert client.get('/api/context-summary?library_id=life').json()['summary'] == ''
    assert client.get('/api/backfills?library_id=life').json()[0]['recorded'] == 0
    assert client.post('/api/turn', json=replay).status_code == 410
    assert client.post('/api/turn', json=source).json()['created'] == []
    assert original in [item['text'] for item in export(client)['messages']]
    assert erase(client, memory).json() == deleted.json()
    history = []

    def next_reply(text, mode, memories, messages):
        history.extend(item['content'] for item in messages)
        return {'text': '好的。', 'behavior': mode, 'provider': 'test-double', 'citations': []}

    monkeypatch.setattr(main, 'generate_reply', next_reply)
    send(client, '继续吧。', conversation=source['conversation'])[0].raise_for_status()
    assert original not in history


def test_source_erasure_cleans_same_source_and_keeps_manual_checkin(client, monkeypatch):
    tracker = create(client)
    day = main.archive.today(main.now()).isoformat()
    client.post(tracker + '/entries', json={'id': 'manual', 'day': day, 'outcome': 'done',
                                          'amount': 3, 'note': '手动记录'}).raise_for_status()
    monkeypatch.setattr(main, 'judge_archive', lambda text, trackers, evidence: {
        'records': [{'tracker': trackers[0]['id'], 'quote': '今天跑了3公里', 'when': '今天', 'outcome': 'done', 'amount': 3}],
        'changes': [],
    })
    text = '我喜欢跑步。今天跑了3公里。我决定参加秋季跑步。'
    first, source = send(client, text)
    assert len(first.json()['created']) == 2 and first.json()['decisionDraftId']
    pending, = [entry for entry in overview(client)['trackers'][0]['entries'] if entry['status'] == 'pending']
    client.patch(f"{tracker}/entries/{pending['id']}", json={
        'action': 'merge', 'version': pending['version'], 'into': 'manual',
    }).raise_for_status()
    memory = rows(client)[0]
    client.patch(f"/api/memories/{memory['id']}", json={
        'action': 'edit', 'version': memory['version'], 'content': memory['content'] + '。'
    }).raise_for_status()
    assert erase(client, memory, 'source').status_code == 409
    updated = next(item for item in rows(client) if item['id'] == memory['id'])
    deleted = erase(client, updated, 'source')
    deleted.raise_for_status()
    assert deleted.json()['messages'] == [source['id']]
    assert set(deleted.json()['memories']) == set(first.json()['created'])
    entry, = overview(client)['trackers'][0]['entries']
    assert entry['amount'] == 3 and [item['channel'] for item in entry['sources']] == ['manual']
    data = export(client)
    assert not data['messages'] and not data['memories'] and not data['decisions']
    assert '参加秋季跑步' not in json.dumps(data, ensure_ascii=False)
    assert client.post('/api/turn', json=source).status_code == 410
    with main.db() as c:
        assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0
        assert c.execute('SELECT COUNT(*) FROM tracker_sources').fetchone()[0] == 1


def test_purged_conversation_stays_deleted_across_reopen_and_legacy_cache(client):
    _, source = send(client, '我喜欢冷杉色。')
    _, reply = send(client, '你记得我什么？')
    _, other_library = send(client, '我喜欢机械设计。', 'study')
    with main.db() as c:
        c.execute("UPDATE turns SET result=json_remove(result,'$.input_sources') WHERE id=?", (reply['id'],))
    path = f"/api/conversations/{source['conversation']}"
    erase_path = f"/api/data/conversations/{source['conversation']}"
    assert client.delete(erase_path).status_code == 409
    client.post(path + '/trash').raise_for_status()
    deleted = client.delete(erase_path).json()
    assert reply['id'] in deleted['replies']
    assert other_library['id'] not in deleted['replies']
    with TestClient(main.app, cookies=client.cookies) as reopened:
        assert reopened.get('/api/data/state').json() == deleted
        assert reopened.post(path + '/restore').status_code == 410
        assert reopened.post('/api/turn', json=source).status_code == 410
        assert reopened.post('/api/turn', json=reply).status_code == 410
        assert reopened.post('/api/turn', json=other_library).status_code == 200


def test_workspace_reset_rejects_stale_client_and_inflight_turn(client, monkeypatch):
    old = client.get('/api/data/state').json()
    headers = {'X-Qiyu-Workspace': old['workspace']}
    send(client, '我喜欢松木香。')[0].raise_for_status()
    with TestClient(main.app) as other:
        other.post('/api/bootstrap').raise_for_status()
        other.put('/api/libraries', json={'id': 'life', 'name': '另一空间'}).raise_for_status()
        send(other, '我喜欢初雪。')[0].raise_for_status()

        entered, proceed = Event(), Event()

        def delayed_reply(*args):
            entered.set()
            assert proceed.wait(5)
            return {'text': '继续说吧。', 'behavior': '分析', 'provider': 'test-double', 'citations': []}

        monkeypatch.setattr(main, 'generate_reply', delayed_reply)
        request = {'id': 'running', 'conversation': 'new', 'segment': 'new', 'library': 'life',
                   'mode': '分析', 'text': '我偏好晚饭后读书。'}
        with ThreadPoolExecutor(max_workers=1) as executor:
            turn = executor.submit(client.post, '/api/turn', json=request, headers=headers)
            try:
                assert entered.wait(5)
                reset = client.delete('/api/data/workspace', headers=headers)
                reset.raise_for_status()
            finally:
                proceed.set()
            assert turn.result(timeout=5).status_code == 409
        current = reset.json()
        assert current['workspace'] != old['workspace'] and current['reset'] == old['reset'] + 1
        assert current['lineage'] == old['lineage']
        assert client.get('/api/data/state', headers=headers).json() == current
        assert client.put('/api/libraries', json={'id': 'life', 'name': '旧库'}, headers=headers).status_code == 409
        client.put('/api/libraries', json={'id': 'life', 'name': '生活'}).raise_for_status()
        assert not export(client)['messages']
        assert rows(other)[0]['content'] == '我喜欢初雪'
    with main.db() as c:
        for table in main.data_control.WORKSPACE_TABLES:
            assert c.execute(f'SELECT 1 FROM {table} WHERE owner=?', (old['workspace'],)).fetchone() is None
