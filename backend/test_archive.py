from datetime import datetime, timedelta

from fastapi.testclient import TestClient

import main
from test_memory import action, client, rows, send


def overview(client, library='life'):
    response = client.get('/api/archive', params={'library_id': library})
    assert response.status_code == 200
    return response.json()


def create(client, library='life', name='跑步', unit='公里'):
    root = f'/api/archive/{library}/trackers'
    response = client.post(root, json={'id': 'run', 'name': name, 'unit': unit, 'target': 3})
    assert response.status_code == 200
    return f'{root}/run'


def test_portrait_requires_distinct_live_evidence_and_remembers_correction(client, monkeypatch):
    calls = []

    def infer(records, facets):
        ids = [record['id'] for record in records]
        calls.append(ids)
        return [
            {'facet': 'learning', 'title': '先做小练习', 'summary': '学新东西时，你习惯先做例子再看原理。', 'evidence': ids},
            {'facet': 'habits', 'title': '重复证据', 'summary': '这项归纳使用了同一条记忆多次。', 'evidence': [ids[0], ids[0]]},
            {'facet': 'decisions', 'title': '虚构证据', 'summary': '这项归纳引用了一条不存在的记忆。', 'evidence': [ids[0], 'invented']},
        ]

    monkeypatch.setattr(main, 'judge_portrait', infer)
    first, payload = send(client, '我习惯先动手做一个小练习。')
    endpoint = '/api/archive/life/portrait/refresh'
    assert client.post(endpoint).status_code == 200 and not calls
    second, _ = send(client, '我喜欢做完例子再读原理。')
    assert overview(client)['portraitNeedsRefresh']
    assert client.post(endpoint).status_code == 200
    assert client.post(endpoint).status_code == 200 and len(calls) == 1
    leaf, = overview(client)['portrait']
    assert leaf['facet'] == 'learning'
    assert set(leaf['evidence']) == set(first.json()['created'] + second.json()['created'])
    assert overview(client, 'study')['portrait'] == []
    client.post(f"/api/conversations/{payload['conversation']}/trash")
    assert overview(client)['portrait'] == []
    client.post(f"/api/conversations/{payload['conversation']}/restore")
    assert overview(client)['portrait']
    assert client.post('/api/archive/life/portrait/learning/dismiss').status_code == 200
    assert overview(client)['portrait'] == []
    send(client, '我偏好先做一个最小可运行的样例。')
    assert client.post(endpoint).status_code == 200 and len(calls) == 2
    assert overview(client)['portrait'] == []


def test_cross_channel_duplicates_keep_both_sources_and_count_once(client, monkeypatch):
    root = create(client)
    day = main.archive.today(main.now()).isoformat()
    body = {'id': 'manual', 'day': day, 'outcome': 'done', 'amount': 3, 'note': '公园跑步'}
    assert client.post(f'{root}/entries', json=body).json() == {'status': 'active'}
    assert client.post(f'{root}/entries', json=body).json() == {'status': 'active'}
    monkeypatch.setattr(main, 'judge_archive', lambda text, trackers, views: {
        'records': [{'tracker': trackers[0]['id'], 'quote': '今天跑了3公里', 'when': '今天', 'outcome': 'done', 'amount': 3}],
        'changes': [],
    })
    response, payload = send(client, '今天跑了3公里。')
    assert response.json()['archive'] == {'recorded': 0, 'pending': 1, 'changes': 0}
    tracker, = overview(client)['trackers']
    assert tracker['segments'][0]['completed'] == 3
    pending, = [entry for entry in tracker['entries'] if entry['status'] == 'pending']
    assert client.post('/api/turn', json=payload).json() == response.json()
    assert client.patch(f"{root}/entries/{pending['id']}", json={
        'action': 'merge', 'version': pending['version'], 'into': 'manual',
    }).status_code == 200
    tracker, = overview(client)['trackers']
    entry, = tracker['entries']
    assert tracker['segments'][0]['doneCount'] == 1
    assert {source['channel'] for source in entry['sources']} == {'manual', 'chat'}
    assert overview(client, 'study')['trackers'] == []
    assert client.patch(f"/api/archive/study/trackers/run/entries/{entry['id']}", json={
        'action': 'revoke', 'version': entry['version'],
    }).status_code == 404
    client.post(f"/api/conversations/{payload['conversation']}/trash")
    assert [source['channel'] for source in overview(client)['trackers'][0]['entries'][0]['sources']] == ['manual']
    assert client.patch(f"{root}/entries/{entry['id']}", json={
        'action': 'edit', 'version': entry['version'], 'amount': 2,
    }).status_code == 200
    assert overview(client)['trackers'][0]['segments'][0]['completed'] == 2
    assert client.patch(f"{root}/entries/{entry['id']}", json={
        'action': 'revoke', 'version': entry['version'] + 1,
    }).status_code == 200
    assert overview(client)['trackers'][0]['entries'] == []


def test_goal_periods_distinguish_missing_days_and_reported_misses(client, monkeypatch):
    day = datetime(2026, 11, 8, 12)
    clock = [int(day.timestamp() * 1000)]
    monkeypatch.setattr(main, 'now', lambda: clock[0])
    root = create(client)
    first = overview(client)['trackers'][0]['goal']
    assert client.post(f'{root}/entries', json={
        'id': 'done', 'day': day.date().isoformat(), 'outcome': 'done', 'amount': 3,
    }).status_code == 200
    clock[0] = int((day + timedelta(days=2)).timestamp() * 1000)
    assert client.put(f'{root}/target', json={'goal': first['id'], 'target': 5}).status_code == 200
    assert client.post(f'{root}/entries', json={
        'id': 'missed', 'day': (day + timedelta(days=2)).date().isoformat(), 'outcome': 'missed',
    }).status_code == 200
    old, new = overview(client)['trackers'][0]['segments']
    assert (old['amount'], old['completed'], old['unrecordedDays'], old['missedCount']) == (3, 3, 1, 0)
    assert (new['amount'], new['completed'], new['unrecordedDays'], new['missedCount']) == (5, 0, 0, 1)


def test_cognitive_changes_are_automatic_scoped_and_correctable(client, monkeypatch):
    old, _ = send(client, '我计划毕业后考研。')
    memory_id = old.json()['created'][0]
    change = '我决定毕业后先工作，不考研了'
    monkeypatch.setattr(main, 'judge_archive', lambda text, trackers, views: {
        'records': [], 'changes': [{'before': memory_id, 'quote': change}],
    })
    response, payload = send(client, change + '。', mode='分析')
    assert response.json()['archive']['changes'] == 1
    assert response.json()['behavior'] == '分析'
    card, = overview(client)['changes']
    assert card['before']['text'] == '我计划毕业后考研。' and card['after']['text'] == change + '。'
    assert overview(client, 'study')['changes'] == []
    assert next(item for item in rows(client) if item['id'] == memory_id)['status'] == 'active'
    with TestClient(main.app) as other:
        other.post('/api/bootstrap')
        other.put('/api/libraries', json={'id': 'life', 'name': '生活'})
        assert overview(other)['changes'] == []
        assert other.patch(f"/api/archive/life/changes/{card['id']}", json={'action': 'dismissed'}).status_code == 404
    client.post(f"/api/conversations/{payload['conversation']}/trash")
    assert overview(client)['changes'] == []
    client.post(f"/api/conversations/{payload['conversation']}/restore")
    assert client.patch(f"/api/archive/life/changes/{card['id']}", json={'action': 'different_context'}).status_code == 200
    send(client, change + '。')
    assert overview(client)['changes'] == []


def test_archive_honors_sensitive_permission_and_memory_correction(client, monkeypatch):
    assert client.post('/api/archive/life/trackers', json={
        'id': 'health', 'name': '健康', 'unit': '次', 'target': 1,
    }).status_code == 403
    client.put('/api/permissions', json={'health': True})
    create(client, name='健康', unit='次')
    old, _ = send(client, '我计划每天记录健康状态。')
    original = next(item for item in rows(client) if item['id'] in old.json()['created'])
    monkeypatch.setattr(main, 'judge_archive', lambda text, trackers, views: {
        'records': [], 'changes': [{'before': original['id'], 'quote': '我决定不记录健康状态了'}],
    })
    # A phrasing which opts out of retaining the topic is skipped.
    send(client, '我决定不记录健康状态了。请不要保存这条。')
    assert overview(client)['changes'] == []
    monkeypatch.setattr(main, 'judge_archive', lambda text, trackers, views: {
        'records': [], 'changes': [{'before': original['id'], 'quote': '我决定停止记录健康状态'}],
    })
    assert send(client, '我决定停止记录健康状态。')[0].json()['archive']['changes'] == 1
    client.put('/api/permissions', json={'health': False})
    hidden = overview(client)
    assert hidden['trackers'] == hidden['changes'] == hidden['memories'] == []
    client.put('/api/permissions', json={'health': True})
    assert overview(client)['changes']
    assert action(client, original, 'reject').status_code == 200
    assert overview(client)['changes'] == []
