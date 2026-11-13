"""Single-query public web retrieval for explicitly enabled chat turns."""
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from deepseek_provider import credentials

URL = 'https://api.deepseek.com/v1/tools/search'
MAX_QUERY_LENGTH = 128
MAX_RESULTS = 5


def client():
    return httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False)


def is_web_url(value):
    if not isinstance(value, str) or len(value) > 2048 or '\\' in value or any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        url = urlsplit(value)
        return url.scheme in ('http', 'https') and bool(url.hostname) and url.username is None
    except ValueError:
        return False


def search(query):
    query = ' '.join(query.split())
    if not query or len(query) > MAX_QUERY_LENGTH:
        raise HTTPException(422, f'联网查询最多支持 {MAX_QUERY_LENGTH} 个字符，请缩短问题后再发送')
    key, _ = credentials()
    try:
        with client() as transport:
            response = transport.post(URL, headers={'Authorization': f'Bearer {key}'}, json={
                'query': query, 'count': MAX_RESULTS,
            })
        if response.status_code in (401, 403):
            raise HTTPException(502, 'DeepSeek API Key 无效或无搜索权限，请在设置中检查')
        if response.status_code == 402:
            raise HTTPException(502, 'DeepSeek 账户余额不足，暂时无法联网搜索')
        if response.status_code == 429:
            raise HTTPException(502, 'DeepSeek 搜索频率超过限额，请稍后重试')
        response.raise_for_status()
        items = response.json()['results']
        if not isinstance(items, list):
            raise ValueError('Invalid results')
    except httpx.TimeoutException:
        raise HTTPException(504, '联网搜索超时，请重试') from None
    except (httpx.HTTPError, ValueError, TypeError, KeyError):
        raise HTTPException(502, '联网搜索暂不可用，请检查网络后重试') from None

    results = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url, title = item.get('url'), item.get('title')
        summary = next((value.strip() for value in (item.get('summary'), item.get('snippet'))
                        if isinstance(value, str) and value.strip()), '')
        if not is_web_url(url) or url in seen or not summary:
            continue
        seen.add(url)
        results.append({'number': len(results) + 1, 'url': url,
                        'title': (title.strip() if isinstance(title, str) else '')[:180] or urlsplit(url).hostname,
                        'content': summary[:1200]})
        if len(results) == MAX_RESULTS:
            break
    if not results:
        raise HTTPException(404, '没有找到可用的网页资料，可以换个关键词再搜索')
    return results
