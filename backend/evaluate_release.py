"""Small real-provider smoke evaluation using fictional input in a fresh session."""

import argparse
import json
import statistics
import sys
import time
import uuid
from datetime import datetime

import httpx


CASES = [
    ('身份', '我目前是植物标本社的记录员。', '我目前是植物标本社的记录员'),
    ('偏好', '我喜欢桂花乌龙茶。', '我喜欢桂花乌龙茶'),
    ('目标', '我计划每周完成两张叶脉观察图。', '我计划每周完成两张叶脉观察图'),
    ('假设', '假设我喜欢柠檬苏打，你会怎么回答？', None),
    ('敏感类别默认关闭', '我习惯每天记录健康状态。', None),
]


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f'{path} HTTP {response.status_code}')
    return response.json()


def run_turn(client, library, text):
    identifier = str(uuid.uuid4())
    payload = {'id': identifier, 'conversation': identifier, 'segment': identifier, 'library': library,
               'text': text, 'mode': '分析'}
    first_text = None
    started = time.perf_counter()
    with client.stream('POST', '/api/turn/stream', json=payload) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            event = json.loads(line)
            if event['type'] == 'text' and first_text is None:
                first_text = round((time.perf_counter() - started) * 1000)
            elif event['type'] == 'error':
                raise RuntimeError(event['detail'])
            elif event['type'] == 'done':
                return payload, event['result'], first_text, round((time.perf_counter() - started) * 1000)
    raise RuntimeError('流式回复未收到完成事件')


def evaluate(origin):
    report = {'timestamp': datetime.now().astimezone().isoformat(timespec='seconds'),
              'source': '虚构输入 · 本地 HTTP · 真实模型', 'cases': []}
    with httpx.Client(base_url=origin, timeout=120, trust_env=False) as client:
        workspace = request(client, 'POST', '/api/bootstrap')
        client.headers['X-Qiyu-Workspace'] = workspace['workspace']
        try:
            model = request(client, 'GET', '/api/model')
            report['model'] = model['model']
            if not model['configured']:
                raise RuntimeError('当前服务尚未配置模型')
            request(client, 'POST', '/api/model/test')
            for library, name in (('life', '生活样例'), ('study', '学习样例')):
                request(client, 'PUT', '/api/libraries', json={'id': library, 'name': name})
            tea = None
            correct, total, covered = 0, 0, 0
            expected_count, matched = 0, 0
            for label, text, expected in CASES:
                payload, result, first, duration = run_turn(client, 'life', text)
                memories = request(client, 'GET', '/api/memories?library_id=life')
                created = [item for item in memories if item['id'] in result['created']]
                original = [item['sourceMessage']['text'] for item in created]
                observed = [item['content'] for item in created]
                if expected:
                    expected_count += 1
                    matched += int(expected in observed)
                total += len(observed)
                correct += sum(item == expected for item in observed)
                covered += sum(item['sourceMessage']['id'] == payload['id'] and item['content'] in text
                               for item in created)
                if label == '偏好':
                    tea = next((item for item in created if item['content'] == expected), None)
                report['cases'].append({
                    'case': label, 'library': 'life', 'input': text, 'expected': expected,
                    'memories': observed, 'sources': original, 'extraction': result['extraction'],
                    'provider': result['provider'], 'firstTextMs': first, 'durationMs': duration,
                    'passed': observed == ([expected] if expected else []),
                })
            prompt = '你记得我偏好哪一种茶吗？只回答有依据的名称。'
            for label, library, expected in (('同库跨会话召回', 'life', True), ('不同库隔离', 'study', False)):
                payload, result, first, duration = run_turn(client, library, prompt)
                cited = bool(tea and tea['id'] in result['citations'])
                mentioned = '桂花乌龙茶' in result['text']
                passed = cited and mentioned if expected else not cited and not mentioned
                cached = request(client, 'POST', '/api/turn', json=payload)
                report['cases'].append({
                    'case': label, 'library': library, 'input': prompt, 'reply': result['text'],
                    'expectedRecall': expected, 'citedTeaMemory': cited, 'retryMatched': cached == result,
                    'provider': result['provider'], 'firstTextMs': first, 'durationMs': duration, 'passed': passed,
                })
            if tea:
                request(client, 'DELETE', f"/api/data/memories/{tea['id']}",
                        json={'version': tea['version'], 'scope': 'source'})
                _, result, first, duration = run_turn(client, 'life', prompt)
                report['cases'].append({
                    'case': '删除后召回', 'library': 'life', 'input': prompt, 'reply': result['text'],
                    'expectedRecall': False, 'citedTeaMemory': tea['id'] in result['citations'],
                    'provider': result['provider'], 'firstTextMs': first, 'durationMs': duration,
                    'passed': '桂花乌龙茶' not in result['text'] and tea['id'] not in result['citations'],
                })
            first_times = [case['firstTextMs'] for case in report['cases'] if case['firstTextMs'] is not None]
            report['metrics'] = {
                'memoryCorrect': correct, 'memoryCount': total, 'sourceCovered': covered,
                'expectedCount': expected_count, 'expectedMatched': matched,
                'firstTextMedianMs': statistics.median(first_times) if first_times else None,
                'firstTextMinMs': min(first_times, default=None), 'firstTextMaxMs': max(first_times, default=None),
                'firstTextSamples': len(first_times),
            }
        except (RuntimeError, httpx.HTTPError) as error:
            report['error'] = str(error)
        finally:
            request(client, 'DELETE', '/api/data/workspace')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8778)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(evaluate(f'http://127.0.0.1:{args.port}'), ensure_ascii=False, indent=2))
