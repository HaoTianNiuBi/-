import threading
import time

import pytest
from fastapi.testclient import TestClient

import main
from test_memory import action, client, rows, send


def open_challenge(client, monkeypatch, mode='挑战', statement='我决定每天学习英语。'):
    original, _ = send(client, statement)
    memory_id = original.json()['created'][0]
    monkeypatch.setattr(main, 'nominate_challenge', lambda text, memories, mode: memory_id)
    result, payload = send(client, '我决定停止学习英语。', mode=mode)
    card = client.get('/api/challenges/pending', params={
        'conversation': payload['conversation'], 'segment': payload['segment'],
    }).json()
    return result.json(), payload, card


def decision(client, card, outcome='confirmed', **checks):
    body = {'segment': card['segment'], 'decision': outcome,
            'record_ok': True, 'situation_same': True, 'goal_same': True, **checks}
    return client.post(f"/api/challenges/{card['id']}/resolve", json=body)


@pytest.mark.parametrize('mode', ['挑战', '自动'])
def test_challenge_requires_all_three_checks_and_runs_once(client, monkeypatch, mode):
    calls = []
    monkeypatch.setattr(main, 'analyze_challenge', lambda old, current: (calls.append((old, current)) or '核对后的分析', 'test'))
    result, payload, card = open_challenge(client, monkeypatch, mode)
    assert result['provider'] == 'local-confirmation' and result['behavior'] == '挑战'
    assert result['created'] == [] and calls == []
    assert card['memory']['sourceMessage']['text'] == '我决定每天学习英语。'
    assert decision(client, card, goal_same=False).status_code == 422 and calls == []
    with TestClient(main.app) as other:
        other.post('/api/bootstrap')
        assert decision(other, card).status_code == 404
    response = decision(client, card)
    assert response.json()['text'] == '核对后的分析'
    assert decision(client, card).json() == response.json() and len(calls) == 1
    assert client.get('/api/challenges/pending', params={'conversation': payload['conversation'], 'segment': payload['segment']}).json() is None


def test_goal_change_ends_challenge_without_analysis(client, monkeypatch):
    _, _, card = open_challenge(client, monkeypatch)
    assert decision(client, card, 'goal_changed').status_code == 200
    assert rows(client)[0]['status'] == 'superseded'
    assert send(client, '我的目标是什么？')[0].json()['citations'] == []
    assert decision(client, card).status_code == 409


@pytest.mark.parametrize('change', ['scope', 'edit', 'goal', 'permission'])
def test_stale_challenge_cannot_authorize_analysis(client, monkeypatch, change):
    if change == 'permission':
        client.put('/api/permissions', json={'health': True})
    statement = '我计划每天记录健康状态。' if change == 'permission' else '我决定每天学习英语。'
    _, payload, card = open_challenge(client, monkeypatch, statement=statement)
    if change == 'scope':
        assert client.post(f"/api/conversations/{payload['conversation']}/challenge-scope", params={'segment': payload['segment']}).status_code == 200
    elif change == 'edit':
        assert action(client, card['memory'], 'edit', '我计划每周学习英语。').status_code == 200
    elif change == 'goal':
        assert send(client, '我计划每天复习数学。')[0].status_code == 200
    else:
        client.put('/api/permissions', json={'health': False})
        client.put('/api/permissions', json={'health': True})
    assert decision(client, card).status_code == 409


def test_other_library_and_manual_analysis_do_not_route_to_challenge(client, monkeypatch):
    send(client, '我计划每天学英语。')
    def unexpected(*args): raise AssertionError('No eligible challenge evidence')
    monkeypatch.setattr(main, 'nominate_challenge', unexpected)
    assert send(client, '我决定停止学习英语。', 'study', mode='自动')[0].status_code == 200
    assert send(client, '我决定停止学习英语。', mode='分析')[0].status_code == 200


def test_followup_requires_explicit_opt_in_and_valid_library_permission(client):
    client.put('/api/permissions', json={'health': True})
    send(client, '我习惯记录健康状态。')
    memory = rows(client)[0]
    path = f"/api/memories/{memory['id']}/followup"
    assert client.get('/api/followups?library_id=life').json() == []
    assert client.put(path, json={'allowed': True}).status_code == 200
    assert [m['id'] for m in client.get('/api/followups?library_id=life').json()] == [memory['id']]
    assert client.get('/api/followups?library_id=study').json() == []
    client.put('/api/permissions', json={'health': False})
    assert client.get('/api/followups?library_id=life').json() == []
    assert client.post(f"/api/followups/{memory['id']}/act", json={'action': 'start'}).status_code == 409
    client.put('/api/permissions', json={'health': True})
    assert rows(client)[0]['followup_allowed'] == 1
    assert client.post(f"/api/followups/{memory['id']}/act", json={'action': 'later'}).status_code == 200
    assert client.get('/api/followups?library_id=life').json() == []
    client.put(path, json={'allowed': False})
    assert rows(client)[0]['followup_allowed'] == 0


def wait_job(client, status):
    for _ in range(100):
        job = client.get('/api/backfills?library_id=life').json()[0]
        if job['status'] == status:
            return job
        time.sleep(.025)
    pytest.fail(f"Backfill stayed {job['status']}")


def test_backfill_respects_library_dates_and_category_and_keeps_source_time(client, monkeypatch):
    clock = [10_000]
    monkeypatch.setattr(main, 'now', lambda: clock[0])
    send(client, '我习惯记录健康状态。')
    clock[0] = 20_000
    _, source = send(client, '我计划记录健康数据。')
    send(client, '我习惯记录健康状态。', 'study')
    send(client, '我喜欢喝茶。')
    query = {'library': 'life', 'category': 'health', 'start': 15_000, 'end': 25_000}
    assert client.post('/api/backfills', json=query).status_code == 403
    client.put('/api/permissions', json={'health': True})
    assert len(rows(client)) == 1  # Enabling permission does not scan past messages.
    job = client.post('/api/backfills', json=query).json()
    assert job['total'] == 1
    assert wait_job(client, 'completed')['recorded'] == 1
    records = rows(client)
    assert len(records) == 2
    memory = next(m for m in records if m['sourceMessage']['id'] == source['id'])
    assert memory['sourceMessage']['created'] == 20_000 and rows(client, 'study') == []
    assert action(client, memory, 'reject').status_code == 200
    assert client.post('/api/backfills', json=query).status_code == 200
    assert wait_job(client, 'completed')['recorded'] == 0
    assert next(m for m in rows(client) if m['id'] == memory['id'])['status'] == 'rejected'


def test_backfill_revocation_stops_inflight_write(client, monkeypatch):
    send(client, '我习惯记录健康状态。')
    client.put('/api/permissions', json={'health': True})
    started, release = threading.Event(), threading.Event()
    def judge(text):
        started.set()
        assert release.wait(4)
        return [{'quote': '我习惯记录健康状态', 'kind': 'preference'}]
    monkeypatch.setattr(main, 'judge_memories', judge)
    job = client.post('/api/backfills', json={'library': 'life', 'category': 'health', 'start': 0, 'end': main.now() + 10000}).json()
    try:
        assert started.wait(3)
        client.put('/api/permissions', json={'health': False})
        client.put('/api/permissions', json={'health': True})
    finally:
        release.set()
    assert wait_job(client, 'cancelled')['recorded'] == 0
    for _ in range(100):
        if not any(key[1] == job['id'] for key in main.backfill_active): break
        time.sleep(.025)
    assert rows(client) == []


def test_disabled_sensitive_history_stays_out_of_model_context(client, monkeypatch):
    _, payload = send(client, '我习惯记录健康状态。')
    def reply(text, mode, memories, history):
        assert history == [] and memories == []
        return {'text': '继续说。', 'behavior': '分析', 'citations': [], 'provider': 'test'}
    monkeypatch.setattr(main, 'generate_reply', reply)
    assert send(client, '请继续。', conversation=payload['conversation'])[0].status_code == 200


def test_backfill_resumes_from_persisted_queue_on_startup(client, monkeypatch):
    send(client, '我习惯记录健康状态。')
    client.put('/api/permissions', json={'health': True})
    schedule = main.schedule_backfill
    monkeypatch.setattr(main, 'schedule_backfill', lambda who, job_id: None)
    response = client.post('/api/backfills', json={'library': 'life', 'category': 'health', 'start': 0, 'end': main.now() + 1000})
    assert response.json()['status'] == 'queued'
    monkeypatch.setattr(main, 'schedule_backfill', schedule)
    with TestClient(main.app):
        assert wait_job(client, 'completed')['recorded'] == 1
