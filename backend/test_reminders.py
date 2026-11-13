from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import main
from test_memory import client, rows, send


def stamp(year, month, day, hour=0):
    return int(datetime(year, month, day, hour).timestamp() * 1000)


def due(client, library='life'):
    response = client.get('/api/reminders', params={'library_id': library} if library else {})
    assert response.status_code == 200
    return response.json()


def test_reminder_for_next_home_is_idempotent_and_scoped(client, monkeypatch):
    calls = []

    def classify(text):
        calls.append(text)
        return {'intent': 'set', 'when': '下次', 'task': '整理实验笔记'}

    monkeypatch.setattr(main.deepseek, 'extract_reminder', classify)
    response, payload = send(client, '下次记得提醒我整理实验笔记')
    result = response.json()
    assert result['reminderId'] == payload['id']
    assert result['text'] == '已设置提醒 · 下次到首页'
    assert rows(client) == []
    assert [item['content'] for item in due(client)] == ['整理实验笔记']
    assert due(client, 'study') == []
    assert due(client, None) == []
    path = f"/api/reminders/{result['reminderId']}/act"
    with TestClient(main.app) as other:
        other.post('/api/bootstrap')
        other.put('/api/libraries', json={'id': 'life', 'name': '生活'})
        assert due(other) == []
        assert other.post(path, json={'action': 'cancel'}).status_code == 404
    assert client.post(path, json={'action': 'cancel'}).json() == {'status': 'cancelled'}
    assert client.post('/api/turn', json=payload).json() == result
    assert len(calls) == 1 and due(client) == []


@pytest.mark.parametrize(('phrase', 'day', 'hour'), [('下周一', 16, 0), ('下周一下午三点', 16, 15)])
def test_dated_reminder_appears_only_when_due(client, monkeypatch, phrase, day, hour):
    clock = [stamp(2026, 11, 11, 10)]
    monkeypatch.setattr(main, 'now', lambda: clock[0])
    monkeypatch.setattr(main.deepseek, 'extract_reminder',
                        lambda _: {'intent': 'set', 'when': '下周一', 'task': '交报名表'})
    response, _ = send(client, f'{phrase}提醒我交报名表', bid='study')
    assert 'reminderId' in response.json()
    assert due(client, 'study') == []
    target = stamp(2026, 11, day, hour)
    clock[0] = target - 1
    assert due(client, 'study') == []
    clock[0] = target
    item, = due(client, 'study')
    assert item['due'] == target
    path = f"/api/reminders/{item['id']}/act"
    assert client.post(path, json={'action': 'later'}).json() == {'status': 'pending'}
    assert due(client, 'study') == []
    clock[0] += 86_400_000
    assert due(client, 'study')[0]['id'] == item['id']
    assert client.post(path, json={'action': 'done'}).json() == {'status': 'done'}
    assert due(client, 'study') == []


def test_reminder_cannot_bypass_sensitive_permission_or_conversation_deletion(client, monkeypatch):
    monkeypatch.setattr(main.deepseek, 'extract_reminder',
                        lambda _: {'intent': 'set', 'when': '下次', 'task': '去医院复查'})
    assert 'reminderId' not in send(client, '下次提醒我去医院复查')[0].json()
    client.put('/api/permissions', json={'health': True})
    response, payload = send(client, '下次提醒我去医院复查')
    assert due(client)[0]['id'] == response.json()['reminderId']
    client.put('/api/permissions', json={'health': False})
    assert due(client) == []
    client.put('/api/permissions', json={'health': True})
    client.post(f"/api/conversations/{payload['conversation']}/trash")
    assert due(client) == []


def test_reported_speech_and_ambiguous_time_do_not_set_reminders(client, monkeypatch):
    monkeypatch.setattr(main.deepseek, 'extract_reminder', lambda _: {'intent': 'none'})
    assert send(client, '老师提醒我下周一交报名表')[0].json()['provider'] == 'test-double'
    monkeypatch.setattr(main.deepseek, 'extract_reminder',
                        lambda _: {'intent': 'set', 'when': '月底', 'task': '交报名表'})
    response, _ = send(client, '月底提醒我交报名表')
    assert '这个时间还不能确定' in response.json()['text']
    assert due(client) == []
