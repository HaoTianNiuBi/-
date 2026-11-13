import json
import uuid

import httpx
import pytest
from fastapi import HTTPException

import main
import deepseek_provider as provider
from test_memory import client, send, rows, action


def test_ai_records_freshman_and_does_not_use_rules_when_accepted(client, monkeypatch):
    monkeypatch.setattr(main, 'judge_memories', lambda text: [{'quote': '我是大一学生', 'kind': 'fact'}])
    def unexpected_rules(text): raise AssertionError('AI has an accepted candidate')
    monkeypatch.setattr(main, 'extract', unexpected_rules)
    response, payload = send(client, '我是大一学生')
    assert response.status_code == 200 and response.json()['extraction'] == 'recorded'
    assert response.json()['memory_method'] == 'ai'
    m = rows(client)[0]
    assert m['content'] == '我是大一学生' and m['kind'] == 'fact'
    assert m['sourceMessage']['id'] == payload['id']
    assert client.post('/api/turn', json=payload).json() == response.json()
    assert len(rows(client)) == 1


def test_long_self_introduction_records_independent_facts_before_question(client, monkeypatch):
    facts = [
        ('我是示例学院的大二计算机学生', 'fact'),
        ('大一学了C，C++，Java，Python基础', 'fact'),
        ('拿了程序设计竞赛省三', 'fact'),
        ('暑假到现在学了数电和51单片机', 'fact'),
        ('接下来想参加STM32比赛', 'goal'),
        ('我想拿国一', 'goal'),
    ]
    question = '我需要怎么去学习'
    candidates = facts + [(question, 'goal'), ('我已经拿了国一', 'fact')]
    monkeypatch.setattr(main, 'judge_memories', lambda text: [{'quote': quote, 'kind': kind} for quote, kind in candidates])
    text = '，'.join(quote for quote, _ in facts) + '，' + question + '？'
    response, payload = send(client, text)
    assert response.status_code == 200 and len(response.json()['created']) == len(facts)
    records = rows(client)
    assert {(m['content'], m['kind']) for m in records} == set(facts)
    assert all(m['sourceMessage']['id'] == payload['id'] and m['sourceMessage']['text'] == text for m in records)


@pytest.mark.parametrize('failed', [False, True])
def test_no_ai_candidate_or_ai_failure_uses_rules(client, monkeypatch, failed):
    def judge(text):
        if failed: raise HTTPException(502, '记忆判断暂不可用')
        return []
    monkeypatch.setattr(main, 'judge_memories', judge)
    response, _ = send(client, '我是大一学生')
    assert response.status_code == 200 and response.json()['extraction'] == 'recorded'
    assert response.json()['memory_method'] == 'rules'
    assert rows(client)[0]['kind'] == 'fact'


def test_extraction_failure_preserves_chat_and_reports_unavailable(client, monkeypatch):
    def unavailable(text): raise HTTPException(502, '记忆判断暂不可用')
    monkeypatch.setattr(main, 'judge_memories', unavailable)
    response, _ = send(client, '今天的风挺舒服')
    assert response.status_code == 200 and response.json()['text']
    assert response.json()['extraction'] == 'unavailable' and rows(client) == []


def test_ai_cannot_invent_or_drop_negation(client, monkeypatch):
    monkeypatch.setattr(main, 'judge_memories', lambda text: [{'quote': '我是大一学生', 'kind': 'fact'}])
    send(client, '我不是大一学生')
    assert rows(client) == []
    send(client, '今天心情一般')
    assert rows(client) == []


@pytest.mark.parametrize('text', ['假如我是大一学生', '他说我是大一学生', '我是大一学生吗？', '我是大一学生？'])
def test_hypothetical_quotation_and_question_not_recorded(client, monkeypatch, text):
    monkeypatch.setattr(main, 'judge_memories', lambda text: [{'quote': '我是大一学生', 'kind': 'fact'}])
    send(client, text)
    assert rows(client) == []


def test_sensitive_and_unbound_do_not_invoke_memory_ai(client, monkeypatch):
    def unexpected(text): raise AssertionError('No memory permission')
    monkeypatch.setattr(main, 'judge_memories', unexpected)
    assert send(client, '我希望记录我的失眠情况')[0].json()['extraction'] == 'sensitive_skipped'
    assert send(client, '我是大一学生', None)[0].json()['extraction'] == 'unbound'
    assert rows(client) == []


@pytest.mark.parametrize('text', ['我是大一学生，但不要记住这件事', '我是大一学生，我不想让你记住'])
def test_explicit_do_not_remember_is_respected(client, monkeypatch, text):
    def unexpected(text): raise AssertionError('User declined memory')
    monkeypatch.setattr(main, 'judge_memories', unexpected)
    assert send(client, text)[0].status_code == 200
    assert rows(client) == []


def test_ai_memory_isolation_rejection_and_source(client, monkeypatch):
    monkeypatch.setattr(main, 'judge_memories', lambda text: [{'quote': text, 'kind': 'fact'}] if text == '我是大一学生' else [])
    send(client, '我是大一学生')
    assert send(client, '你记得我什么？', 'study')[0].json()['citations'] == []
    m = rows(client)[0]
    assert action(client, m, 'reject').status_code == 200
    send(client, '我是大一学生')
    assert len(rows(client)) == 1 and rows(client)[0]['status'] == 'rejected'


def test_memory_transport_only_sees_current_message_and_validates_json(client, monkeypatch, tmp_path):
    monkeypatch.setattr(provider, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-secret-not-real')
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')
    captured = []
    def respond(req):
        body = json.loads(req.content)
        captured.append(body)
        content = json.dumps({'memories': [{'quote': '我是大一学生', 'kind': 'fact'}]})
        return httpx.Response(200, json={'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(provider, 'client', lambda: httpx.Client(transport=httpx.MockTransport(respond)))
    monkeypatch.setattr(main, 'judge_memories', provider.extract_memories)
    response, _ = send(client, '我是大一学生')
    assert response.json()['memory_method'] == 'ai'
    assert len(captured) == 1 and captured[0]['response_format'] == {'type': 'json_object'}
    assert len(captured[0]['messages']) == 2
    assert captured[0]['messages'][-1]['content'] == '我是大一学生'
