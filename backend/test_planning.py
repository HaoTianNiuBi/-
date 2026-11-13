from base64 import b64decode
from datetime import datetime

from fastapi.testclient import TestClient

import main
from test_memory import client, send


def stamp(day):
    return int(datetime.fromisoformat(day + 'T12:00:00').timestamp() * 1000)


def test_decision_draft_result_and_review_use_the_same_library(client, monkeypatch):
    home = '/api/planning/life'
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-12'))
    assert send(client, '我决定参加读书会？')[0].json().get('decisionDraftId') is None
    reply, body = send(client, '我决定参加读书会。')
    identifier = reply.json()['decisionDraftId']
    path = f'{home}/decisions/{identifier}'
    draft, = client.get(home + '/decisions').json()
    assert draft['draft'] and draft['original'] == body['text'] and not draft['expectation']
    assert client.get('/api/planning/study/decisions').json() == []
    assert client.post(path + '/review', json={'version': 1}).status_code == 422
    decision = {'id': identifier, 'version': 1, 'title': '参加读书会', 'choice': draft['choice'],
                'expectation': '认识两位同学', 'due': '2026-11-12'}
    confirmed = client.put(home + '/decisions', json=decision).json()
    assert not confirmed['draft'] and confirmed['version'] == 2
    assert client.get(home + '/home').json()['decisions'][0]['id'] == identifier
    saved = client.put(home + '/decisions', json={**decision, 'version': 2, 'result': '认识了一位同学'}).json()
    assert saved['result_at'] == stamp('2026-11-12')
    monkeypatch.setattr(main.deepseek, 'review_decision', lambda data: data['expectation'] + ' / ' + data['result'])
    assert client.post(path + '/review', json={'version': 3}).json()['review'] == '认识两位同学 / 认识了一位同学'
    assert client.get(home + '/home').json()['decisions'] == []
    timeline = client.get('/api/archive', params={'library_id': 'life'}).json()['decisions']
    assert timeline[0]['result'] == '认识了一位同学'
    assert client.post(f"/api/conversations/{body['conversation']}/trash").status_code == 200
    assert client.get(home + '/decisions').json() == []


def test_capsule_versions_preview_reminder_and_delete(client, monkeypatch):
    home = '/api/planning/life'
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-12'))
    body = {'id': 'letter', 'kind': 'letter', 'title': '给下个月的自己', 'body': '当时的想法', 'due': '2026-11-13'}
    assert client.put(home + '/capsules', json=body).status_code == 200
    assert client.put(home + '/capsules', json={**body, 'id': 'snapshot', 'kind': 'snapshot', 'due': '2026-11-20'}).status_code == 200
    item = client.get(home + '/capsules/letter').json()
    assert item['body'] == '当时的想法' and not item['dueNow']
    assert client.put(home + '/capsules', json={**body, 'version': 1, 'body': '改过的想法'}).status_code == 200
    item = client.get(home + '/capsules/letter').json()
    assert [row['body'] for row in item['versions']] == ['改过的想法', '当时的想法']
    assert client.put(home + '/capsules', json={**body, 'version': 1}).status_code == 409
    preview = client.get(home + '/capsules?preview_day=2026-11-13').json()
    assert preview['preview'] and all('body' not in row for row in preview['items'])
    assert [row['id'] for row in preview['items'] if row['dueNow']] == ['letter']
    assert client.get(home + '/home').json()['capsules'] == []
    assert client.get('/api/planning/study/capsules/letter').status_code == 404
    assert client.post(home + '/capsules/letter/act', json={'version': 2, 'action': 'seen'}).status_code == 409
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-13'))
    assert client.get(home + '/home').json()['capsules'][0]['id'] == 'letter'
    assert client.post(home + '/capsules/letter/act', json={'version': 2, 'action': 'snooze'}).status_code == 200
    assert client.get(home + '/home').json()['capsules'] == []
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-14'))
    assert client.post(home + '/capsules/letter/act', json={'version': 2, 'action': 'seen'}).status_code == 200
    assert client.get(home + '/home').json()['capsules'] == []
    assert client.delete(home + '/capsules/letter?version=2').status_code == 200
    with main.db() as database:
        assert not database.execute("SELECT 1 FROM capsule_versions WHERE id='letter'").fetchone()


def test_capsule_ai_permission_and_version_are_rechecked(client, monkeypatch):
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-12'))
    body = {'id': 'letter', 'kind': 'letter', 'title': '我的未来信', 'body': '想改善睡眠和健康', 'due': '2026-11-20'}
    assert client.put('/api/planning/life/capsules', json=body).status_code == 200
    calls = []

    def generate(*args, capsules):
        calls.append(capsules)
        return {'text': capsules[0]['body'], 'provider': 'test', 'behavior': '分析', 'citations': [],
                'capsuleRefs': [{'id': 'letter', 'version': capsules[0]['version']}]}

    monkeypatch.setattr(main.deepseek, 'generate', generate)
    assert send(client, '回看我的未来信。')[0].status_code == 200 and not calls
    client.put('/api/permissions', json={'health': True}).raise_for_status()
    assert send(client, '回看我的未来信。', 'study')[0].status_code == 200 and not calls
    result, payload = send(client, '回看我的未来信。')
    assert result.json()['text'] == body['body'] and calls[0][0]['version'] == 1
    client.put('/api/permissions', json={'health': False}).raise_for_status()
    cached = client.post('/api/turn', json=payload).json()
    assert body['body'] not in cached['text'] and 'capsuleRefs' not in cached
    assert client.get('/api/planning/life/capsules/letter').json()['body'] == body['body']


def test_capsule_images_follow_versions_and_library_scope(client, monkeypatch):
    home = '/api/planning/life/capsules'
    path = home + '/album'
    monkeypatch.setattr(main, 'now', lambda: stamp('2026-11-12'))
    leaf = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNwTTEDAAHRAODfYd9eAAAAAElFTkSuQmCC'
    bark = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPYNacIAAPcAcngDzS/AAAAAElFTkSuQmCC'
    body = {'id': 'album', 'kind': 'snapshot', 'title': '散步照片', 'due': '2026-11-13'}
    album = {**body, 'images': [{'name': '叶.png', 'data': leaf}, {'name': '树皮.png', 'data': bark}]}
    client.put(home, json=album).raise_for_status()
    client.put(home, json=album).raise_for_status()
    first, = client.get(path).json()['versions']
    assert first['version'] == 1 and first['body'] == ''
    assert [image['name'] for image in first['images']] == ['叶.png', '树皮.png']
    first_image, second_image = first['images']
    image_path = f"{path}/images/{first_image['id']}"
    response = client.get(image_path)
    assert response.status_code == 200 and response.content == b64decode(leaf)
    assert response.headers['content-type'] == 'image/png'
    assert response.headers['cache-control'] == 'private, no-store'
    assert 'images' not in client.get(home).json()['items'][0]
    assert client.get(image_path.replace('/life/', '/study/')).status_code == 404
    with TestClient(main.app) as other:
        assert other.get(image_path).status_code == 401
        other.post('/api/bootstrap').raise_for_status()
        other.put('/api/libraries', json={'id': 'life', 'name': '生活'}).raise_for_status()
        assert other.get(image_path).status_code == 404

    edit = {**body, 'version': 1, 'body': '那天拍了树叶和树皮。'}
    client.put(home, json=edit).raise_for_status()
    assert client.get(path).json()['versions'][0]['images'] == first['images']
    invalid = {**edit, 'version': 2, 'images': [{'name': '坏图.png', 'data': 'bm90IGFuIGltYWdl'}]}
    assert client.put(home, json=invalid).status_code == 422
    client.put(home, json={**edit, 'version': 2, 'images': [second_image['id']]}).raise_for_status()
    latest, legacy_edit, original = client.get(path).json()['versions']
    assert [latest['version'], legacy_edit['version'], original['version']] == [3, 2, 1]
    assert latest['images'] == [second_image] and legacy_edit['images'] == original['images'] == first['images']
    assert client.get(image_path).content == b64decode(leaf)
    client.delete(path + '?version=3').raise_for_status()
    assert client.get(image_path).status_code == 404
    with main.db() as database:
        assert database.execute('SELECT COUNT(*) FROM capsule_images').fetchone()[0] == 0
        assert database.execute('SELECT COUNT(*) FROM capsule_version_images').fetchone()[0] == 0


def test_retired_template_does_not_affect_existing_tracking(client):
    base = '/api/planning/life'
    tracker = {'id': 'existing', 'name': '复习时长', 'unit': '分钟', 'target': 60}
    client.post('/api/archive/life/trackers', json=tracker).raise_for_status()
    client.post('/api/archive/life/trackers/existing/entries', json={
        'id': 'check-in', 'day': main.archive.today(main.now()).isoformat(), 'outcome': 'done', 'amount': 30,
    }).raise_for_status()
    with main.db() as database:
        who = database.execute('SELECT owner FROM sessions').fetchone()['owner']
        database.execute('''CREATE TABLE library_templates(owner TEXT,bid TEXT,template TEXT,configuration TEXT,
            custom_configuration TEXT,PRIMARY KEY(owner,bid))''')
        database.execute('INSERT INTO library_templates VALUES(?,?,?,?,NULL)',
                         (who, 'life', 'exam', '{"cards":["tracking"],"prompt":"复习"}'))
    assert client.get(base + '/home').json() == {'capsules': [], 'decisions': []}
    assert client.get(base + '/templates').status_code == 404
    assert client.post(base + '/templates/apply', json={'template': 'exam'}).status_code == 404
    existing, = client.get('/api/archive', params={'library_id': 'life'}).json()['trackers']
    assert existing['goal']['amount'] == 60 and existing['entries'][0]['amount'] == 30
