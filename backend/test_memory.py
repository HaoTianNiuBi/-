import uuid
import pytest
from fastapi.testclient import TestClient
import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DB', tmp_path / 'test.sqlite3')
    monkeypatch.setattr(main, 'judge_memories', lambda text: [])
    monkeypatch.setattr(main, 'judge_archive', lambda *args: {'records': [], 'changes': []})
    monkeypatch.setattr(main, 'nominate_challenge', lambda text, memories, mode: None)
    # Deterministic double for memory tests; production never falls back to rules.
    def fake_reply(text, mode, memories, history):
        recalled = memories if any(word in text for word in ('记得', '目标是什么', '偏好')) else []
        return {'text': '\n'.join(m['content'] for m in recalled) or '请继续说。',
                'behavior': '分析' if mode == '自动' else mode,
                'citations': [m['id'] for m in recalled], 'provider': 'test-double'}
    monkeypatch.setattr(main, 'generate_reply', fake_reply)
    with TestClient(main.app) as c:
        assert c.post('/api/bootstrap').status_code == 200
        for bid in ['life', 'study']:
            assert c.put('/api/libraries', json={'id': bid, 'name': bid}).status_code == 200
        yield c


def send(c, text, bid='life', **kwargs):
    cid = kwargs.pop('conversation', str(uuid.uuid4()))
    payload = {'id': str(uuid.uuid4()), 'conversation': cid, 'segment': cid, 'library': bid, 'text': text, 'mode': '分析', **kwargs}
    return c.post('/api/turn', json=payload), payload


def rows(c, bid='life'):
    r = c.get('/api/memories', params={'library_id': bid})
    assert r.status_code == 200
    return r.json()


def test_conversation_trash_restore_and_recall(client):
    response, payload = send(client, '我喜欢喝绿茶。')
    mid = response.json()['created'][0]
    cid = payload['conversation']
    assert client.post(f'/api/conversations/{cid}/trash').status_code == 200
    assert rows(client) == []
    assert send(client, '你记得我什么？')[0].json()['citations'] == []
    assert client.post('/api/turn', json=payload).status_code == 409
    assert client.post(f'/api/conversations/{cid}/restore').status_code == 200
    assert rows(client)[0]['id'] == mid
    assert mid in send(client, '你记得我什么？')[0].json()['citations']


def test_trash_is_scoped_to_current_owner(client):
    _, payload = send(client, '我喜欢喝绿茶。')
    with TestClient(main.app) as other:
        other.post('/api/bootstrap')
        other.post(f"/api/conversations/{payload['conversation']}/trash")
    assert len(rows(client)) == 1


def test_restore_does_not_reactivate_rejected_memories(client):
    _, payload = send(client, '我喜欢喝绿茶。')
    action(client, rows(client)[0], 'reject')
    cid = payload['conversation']
    client.post(f'/api/conversations/{cid}/trash')
    client.post(f'/api/conversations/{cid}/restore')
    assert rows(client)[0]['status'] == 'rejected'
    assert send(client, '你记得我什么？')[0].json()['citations'] == []


def action(c, m, name, content=None):
    return c.patch('/api/memories/' + m['id'], json={'action': name, 'version': m['version'], 'content': content})


def test_source_and_cross_conversation_recall(client):
    r, p = send(client, '我希望回复时先共情，不要急着给建议。')
    assert r.status_code == 200 and r.json()['extraction'] == 'recorded'
    m = rows(client)[0]
    assert m['sourceMessage']['text'] == p['text']
    assert m['sourceMessage']['id'] == p['id']
    assert m['sourceMessage']['created'] > 0
    recall, _ = send(client, '你记得我什么？')
    assert m['id'] in recall.json()['citations'] and m['content'] in recall.json()['text']
    other, _ = send(client, '你记得我什么？', 'study')
    assert other.json()['citations'] == []
    unbound, _ = send(client, '我喜欢喝绿茶。', None)
    assert unbound.json()['extraction'] == 'unbound'
    assert len(rows(client)) == 1


def test_corrections_propagate_and_version_conflict(client):
    send(client, '我喜欢喝绿茶。')
    m = rows(client)[0]
    assert action(client, m, 'edit', '我喜欢喝乌龙茶').status_code == 200
    assert action(client, m, 'edit', '冲突修改').status_code == 409
    result, _ = send(client, '你记得我什么？')
    assert '乌龙茶' in result.json()['text'] and '绿茶' not in result.json()['text']
    summary = client.get('/api/context-summary', params={'library_id': 'life'}).json()
    assert '绿茶' not in summary['summary']
    m = rows(client)[0]
    assert action(client, m, 'reject').status_code == 200
    assert send(client, '你记得我什么？')[0].json()['citations'] == []
    assert len(client.get('/api/memories/' + m['id'] + '/history').json()) == 2


def test_changed_goal_is_not_a_current_constraint(client):
    send(client, '我计划每天学习英语。')
    m = rows(client)[0]
    assert m['kind'] == 'goal'
    assert action(client, m, 'change_goal').status_code == 200
    assert rows(client)[0]['status'] == 'superseded'
    assert send(client, '我的目标是什么？')[0].json()['citations'] == []


def test_retry_and_dedup_never_reactivate_rejected(client):
    r, payload = send(client, '我喜欢喝绿茶。')
    assert client.post('/api/turn', json=payload).json() == r.json()
    assert len(rows(client)) == 1
    changed = dict(payload, text='另一条消息')
    assert client.post('/api/turn', json=changed).status_code == 409
    m = rows(client)[0]
    action(client, m, 'reject')
    send(client, payload['text'])
    assert len(rows(client)) == 1 and rows(client)[0]['status'] == 'rejected'


def test_sensitive_permissions_pause_without_resurrection(client):
    text = '我希望记录我的失眠情况。'
    assert send(client, text)[0].json()['extraction'] == 'sensitive_skipped'
    assert rows(client) == []
    client.put('/api/permissions', json={'health': True})
    assert rows(client) == []  # No retroactive extraction.
    send(client, text)
    assert len(rows(client)) == 1
    client.put('/api/permissions', json={'health': False})
    assert rows(client)[0]['status'] == 'paused'
    assert send(client, '你记得我什么？')[0].json()['citations'] == []
    client.put('/api/permissions', json={'health': True})
    m = rows(client)[0]
    assert m['status'] == 'active'
    action(client, m, 'reject')
    client.put('/api/permissions', json={'health': False})
    client.put('/api/permissions', json={'health': True})
    assert rows(client)[0]['status'] == 'rejected'


def test_multi_category_and_edit_bypass_blocked(client):
    send(client, '我喜欢喝茶。')
    m = rows(client)[0]
    assert action(client, m, 'edit', '我的工资是一万元').status_code == 403
    client.put('/api/permissions', json={'health': True})
    assert send(client, '我希望记录吃药和财务情况。')[0].json()['extraction'] == 'sensitive_skipped'
    assert send(client, '我希望记住银行卡号。')[0].json()['extraction'] == 'sensitive_skipped'
    assert send(client, f'我希望联系{138:03d}{0:08d}。')[0].json()['extraction'] == 'sensitive_skipped'


def test_quoted_and_hypothetical_are_not_facts(client):
    for text in ['如果我喜欢喝绿茶怎么办？', '他说我喜欢喝绿茶。', '例如：我喜欢喝绿茶。']:
        assert send(client, text)[0].json()['created'] == []
    assert rows(client) == []


def test_owner_isolation_and_context_binding(client):
    r, payload = send(client, '我喜欢喝绿茶。')
    mid = rows(client)[0]['id']
    assert client.post('/api/turn', json=dict(payload, id=str(uuid.uuid4()), library='study')).status_code == 409
    with TestClient(main.app) as other:
        assert other.get('/api/memories', params={'library_id': 'life'}).status_code == 401
        other.post('/api/bootstrap')
        assert other.get('/api/memories', params={'library_id': 'life'}).status_code == 404
        other.put('/api/libraries', json={'id': 'life', 'name': '生活'})
        assert rows(other) == []
        assert other.patch('/api/memories/' + mid, json={'action': 'reject', 'version': 1}).status_code == 404


def test_cross_origin_blocked(client):
    assert client.post('/api/bootstrap', headers={'origin': 'https://attacker.example'}).status_code == 403


def test_retry_does_not_leak_invalidated_evidence(client):
    send(client, '我喜欢喝绿茶。')
    old, payload = send(client, '你记得我什么？')
    assert '绿茶' in old.json()['text']
    action(client, rows(client)[0], 'reject')
    retry = client.post('/api/turn', json=payload).json()
    assert retry['citations'] == [] and '绿茶' not in retry['text']


def test_expiry_and_durable_session(client):
    send(client, '我喜欢喝绿茶。')
    token = client.cookies.get('qiyu_session')
    with TestClient(main.app) as restarted:
        restarted.cookies.set('qiyu_session', token, path='/api')
        assert len(rows(restarted)) == 1
    with main.db() as c:
        c.execute('UPDATE memories SET expires=0')
    assert rows(client)[0]['status'] == 'expired'
    assert send(client, '你记得我什么？')[0].json()['citations'] == []
