"""Local Qiyu workspace API."""
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import time
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import deepseek_provider as deepseek
import archive
import data_control
import planning
import web_search
from reminders import request_candidate, resolve_time

generate_reply = deepseek.generate
judge_memories = deepseek.extract_memories
nominate_challenge = deepseek.nominate_challenge
analyze_challenge = deepseek.analyze_challenge
judge_archive = deepseek.extract_archive
judge_portrait = deepseek.summarize_portrait
_turn_locks = {}
_turn_locks_guard = threading.Lock()


@contextmanager
def turn_lock(who, turn_id):
    key = (who, turn_id)
    with _turn_locks_guard:
        item = _turn_locks.setdefault(key, [threading.Lock(), 0])
        item[1] += 1
    try:
        with item[0]: yield
    finally:
        with _turn_locks_guard:
            item[1] -= 1
            if item[1] == 0: del _turn_locks[key]

DB = Path(os.environ.get('QIYU_DB', Path(__file__).parent / 'data' / 'qiyu.sqlite3'))


@asynccontextmanager
async def lifespan(_app):
    with db() as c:
        jobs = c.execute("SELECT owner,id FROM backfill_jobs WHERE status IN ('queued','running')").fetchall()
    for job in jobs:
        schedule_backfill(job['owner'], job['id'])
    yield


app = FastAPI(title='Qiyu local memory demo', version='0.6.0', lifespan=lifespan)


def uid(): return str(uuid.uuid4())
def now(): return int(time.time() * 1000)


@contextmanager
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
    PRAGMA foreign_keys=ON;
    PRAGMA secure_delete=ON;
    CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,owner TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS libraries(owner TEXT,id TEXT,name TEXT,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS contexts(owner TEXT,id TEXT,cid TEXT,bid TEXT,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS messages(owner TEXT,id TEXT,cid TEXT,sid TEXT,bid TEXT,text TEXT,created INTEGER,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS memories(owner TEXT,id TEXT,bid TEXT,source TEXT,content TEXT,kind TEXT,slot TEXT,status TEXT,created INTEGER,updated INTEGER,expires INTEGER,version INTEGER DEFAULT 1,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS events(owner TEXT,id TEXT,memory TEXT,action TEXT,before TEXT,after TEXT,created INTEGER);
    CREATE TABLE IF NOT EXISTS turns(owner TEXT,id TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,id));
    CREATE TABLE IF NOT EXISTS settings(owner TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS deleted_conversations(owner TEXT,cid TEXT,PRIMARY KEY(owner,cid));
    CREATE TABLE IF NOT EXISTS challenges(
        owner TEXT,id TEXT,cid TEXT,sid TEXT,bid TEXT,source TEXT,memory TEXT,version INTEGER,
        goals TEXT,status TEXT,decision TEXT,result TEXT,created INTEGER,PRIMARY KEY(owner,id)
    );
    CREATE INDEX IF NOT EXISTS challenge_conversation ON challenges(owner,cid,status);
    CREATE TABLE IF NOT EXISTS closed_challenge_scopes(owner TEXT,cid TEXT,sid TEXT,PRIMARY KEY(owner,cid,sid));
    CREATE TABLE IF NOT EXISTS followups(owner TEXT,memory TEXT,allowed INTEGER,due INTEGER,PRIMARY KEY(owner,memory));
    CREATE TABLE IF NOT EXISTS reminders(
        owner TEXT,id TEXT,content TEXT,due INTEGER,status TEXT,PRIMARY KEY(owner,id)
    );
    CREATE INDEX IF NOT EXISTS reminder_due ON reminders(owner,status,due);
    CREATE TABLE IF NOT EXISTS backfill_jobs(
        owner TEXT,id TEXT,bid TEXT,category TEXT,start INTEGER,end INTEGER,status TEXT,
        total INTEGER,processed INTEGER,recorded INTEGER,failed INTEGER,created INTEGER,PRIMARY KEY(owner,id)
    );
    CREATE TABLE IF NOT EXISTS backfill_items(owner TEXT,job TEXT,source TEXT,PRIMARY KEY(owner,job,source));
    CREATE INDEX IF NOT EXISTS message_scope_date ON messages(owner,bid,created);
    ''' + archive.SCHEMA + planning.SCHEMA + data_control.SCHEMA)
    try:
        conn.execute('BEGIN IMMEDIATE')
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally: conn.close()


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.method not in ('GET', 'HEAD'):
        origin = request.headers.get('origin')
        if origin and origin not in {'http://127.0.0.1:5173', 'http://localhost:5173', 'http://127.0.0.1:8765',
                                     os.environ.get('QIYU_ORIGIN')}:
            return Response(status_code=403)
    return await call_next(request)


def owner(request, *, check_workspace=True):
    token = request.cookies.get('qiyu_session')
    with db() as c:
        row = c.execute('SELECT owner FROM sessions WHERE token=?', (token,)).fetchone()
    if not row: raise HTTPException(401, '请刷新页面以初始化本地空间')
    expected = request.headers.get('X-Qiyu-Workspace')
    if check_workspace and expected and expected != row['owner']:
        raise HTTPException(409, '数据空间已重置，请刷新页面')
    return row['owner']


def permissions(c, who):
    row = c.execute('SELECT value FROM settings WHERE owner=?', (who,)).fetchone()
    return json.loads(row['value']) if row else {'health': False, 'finance': False, 'relationship': False}


def categories(text):
    groups = {
        'health': r'健康|医院|疾病|生病|药|抑郁|焦虑|病史|诊断|失眠|血压|身体|疼痛|自残|自杀',
        'finance': r'财务|收入|工资|存款|负债|贷款|银行卡|余额|投资|欠钱|月薪|理财|资产|债务',
        'relationship': r'感情|恋爱|女友|男友|女朋友|男朋友|伴侣|分手|离婚|性生活|性取向|前任|结婚|暗恋',
        'private': r'身份证|手机号|电话号码|家庭住址|密码|密钥|验证码|精确位置|银行卡号|(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{15,19}[Xx]?(?!\d)',
    }
    return [k for k, pattern in groups.items() if re.search(pattern, text)]


def allowed(c, who, text):
    p = permissions(c, who)
    return all(p.get(k, False) for k in categories(text))


def library(c, who, bid):
    if not c.execute('SELECT id FROM libraries WHERE owner=? AND id=?', (who, bid)).fetchone():
        raise HTTPException(404, '记忆库不存在或不属于当前空间')


def visible(c, who, bid):
    rows = c.execute('''SELECT m.*, COALESCE(f.allowed,0) AS followup_allowed FROM memories m
        LEFT JOIN followups f ON f.owner=m.owner AND f.memory=m.id
        WHERE m.owner=? AND m.bid=? ORDER BY m.created DESC,m.rowid DESC''', (who, bid)).fetchall()
    result = []
    for row in rows:
        m = dict(row)
        source = c.execute('SELECT * FROM messages WHERE owner=? AND id=? AND bid=?', (who, m['source'], bid)).fetchone()
        if not source: continue
        m['sourceMessage'] = dict(source)
        if c.execute('SELECT 1 FROM deleted_conversations WHERE owner=? AND cid=?', (who, source['cid'])).fetchone():
            continue
        if m['status'] == 'active' and m['expires'] < now(): m['status'] = 'expired'
        if m['status'] == 'active' and not allowed(c, who, source['text'] + m['content']): m['status'] = 'paused'
        m['confidence'] = '用户明确表达' if m['version'] == 1 else '用户已纠正'
        result.append(m)
    return result


def extract(text):
    if re.search(r'假设|假如|如果|比如|例如|他说|她说|你说|引用|扮演|小说|故事|[“”「」"]', text): return []
    result = []
    for part in re.split(r'[。！？\n；;]', text):
        part = part.strip()
        if not 4 <= len(part) <= 220: continue
        if re.match(r'^(我(的目标是|计划|打算|想在|准备|决定)|今年我要)', part):
            kind, slot = 'goal', 'goal:' + part[:10]
        elif re.match(r'^(我(喜欢|不喜欢|希望|习惯|偏好|更喜欢|讨厌)|请(先|不要|别)|以后(请|别|不要)|记住[，,:：]?我)', part):
            kind, slot = 'preference', ('communication' if re.search('回复|回答|建议|分析|共情|安慰|倾听', part) else 'preference:' + part[:12])
        elif re.match(r'^(我(是|目前是|现在是|今年读|就读于|在读)|本人是)', part) and not re.search(r'[?？]|[吗呢]$', part):
            kind, slot = 'fact', 'fact:' + part[:12]
        else: continue
        result.append((part, kind, slot))
    return result


def choose_memories(text):
    # Preserve the complete source clause, including negation and qualifications.
    if re.search(r'不要记|别记|不想.*记|不要保存|不保存|不记录|不要记录', text):
        return [], 'none', False
    if re.search(r'假设|假如|如果|比如|例如|他说|她说|你说|引用|扮演|小说|故事|[“”「」"]', text):
        return [], 'none', False
    # A source span may contain commas, e.g. a list of languages or awards.
    boundary = r'[。！？!?；;，,\n]'
    failed = False
    try:
        proposed = judge_memories(text)
    except HTTPException:
        proposed, failed = [], True
    accepted = []
    for item in proposed[:deepseek.MAX_MEMORY_CANDIDATES]:
        if not isinstance(item, dict): continue
        quote, kind = item.get('quote'), item.get('kind')
        if not isinstance(quote, str) or kind not in ('fact', 'preference', 'goal'): continue
        quote = quote.strip('。！？\n；; ')
        if not 4 <= len(quote) <= 220 or re.search(r'[?？]|[吗呢]$', quote): continue
        if re.match(r'^(你|他|她|(?:我(?:该|要|需要|可以|应该)?)?(?:怎么|如何|为什么|能不能|要不要))', quote): continue
        # Bound both ends so a positive fragment cannot be cut out of a negation.
        span = rf'(?:^|{boundary})\s*{re.escape(quote)}\s*(?=$|[。！!；;，,\n])'
        if not re.search(span, text): continue
        accepted.append((quote, kind, kind + ':' + quote[:12]))
    if accepted: return list(dict.fromkeys(accepted)), 'ai', False
    fallback = [] if re.search(r'[?？]|[吗呢][。！!]*$', text) else extract(text)
    return fallback, 'rules' if fallback else 'none', failed


class LibraryInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=24)


class Turn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    conversation: str = Field(min_length=1, max_length=80)
    segment: str = Field(min_length=1, max_length=80)
    library: str | None = Field(default=None, max_length=80)
    text: str = Field(min_length=1, max_length=4000)
    mode: str
    webSearch: bool = False


class MemoryChange(BaseModel):
    action: str
    content: str | None = Field(default=None, max_length=400)
    version: int


class EraseMemory(BaseModel):
    version: int = Field(ge=1)
    scope: Literal['memory', 'source']
    blockSource: bool = True


class PermissionInput(BaseModel):
    health: bool = False
    finance: bool = False
    relationship: bool = False


class ChallengeDecision(BaseModel):
    segment: str
    decision: Literal['confirmed', 'record_wrong', 'situation_changed', 'goal_changed', 'skip']
    record_ok: bool = False
    situation_same: bool = False
    goal_same: bool = False


class FollowupPermission(BaseModel):
    allowed: bool


class FollowupAction(BaseModel):
    action: Literal['start', 'later', 'ignore', 'stop']


class ReminderAction(BaseModel):
    action: Literal['done', 'later', 'cancel']


class BackfillInput(BaseModel):
    library: str = Field(min_length=1, max_length=80)
    category: Literal['health', 'finance', 'relationship']
    start: int = Field(ge=0)
    end: int = Field(gt=0)


@app.get('/api/health')
def health(): return {'version': app.version, 'provider': 'deepseek'}


@app.get('/api/model')
def model_status(request: Request):
    owner(request)
    return {**deepseek.status(), 'keyEditable': os.environ.get('QIYU_KEY_MODE') != 'environment'}


@app.post('/api/model/test')
def test_model(request: Request):
    owner(request)
    _, model = deepseek.completion([{'role': 'user', 'content': '请只回复 OK。'}], test=True)
    return {'ok': True, 'provider': 'deepseek', 'model': model}


@app.put('/api/model/key')
async def save_model_key(request: Request):
    owner(request)
    if os.environ.get('QIYU_KEY_MODE') == 'environment':
        raise HTTPException(403, '模型密钥由部署端配置')
    raw = await request.body()
    if len(raw) > 4096:
        raise HTTPException(422, 'API Key 格式不正确，请检查后重试')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise HTTPException(422, '配置格式不正确，请重试') from None
    # Validate without echoing credential input in standard validation errors.
    if not isinstance(body, dict):
        raise HTTPException(422, '配置格式不正确，请重试')
    return deepseek.save_key(body.get('apiKey'))


@app.post('/api/model/search/test')
def test_search(request: Request):
    owner(request)
    web_search.search('Python programming language official documentation')
    return {'ok': True}


@app.post('/api/bootstrap')
def bootstrap(request: Request, response: Response):
    token = request.cookies.get('qiyu_session')
    with db() as c:
        row = c.execute('SELECT owner FROM sessions WHERE token=?', (token,)).fetchone()
        if not row:
            token, who = uid(), uid()
            c.execute('INSERT INTO sessions VALUES(?,?)', (token, who))
        else: who = row['owner']
        c.execute('''INSERT OR IGNORE INTO workspace_started
            SELECT ?,COALESCE(MIN(created),?) FROM messages WHERE owner=?''', (who, now(), who))
        response.set_cookie('qiyu_session', token, httponly=True, secure=request.url.scheme == 'https',
                            samesite='strict', max_age=31536000, path='/api')
        response.headers['Cache-Control'] = 'no-store'
        return {'provider': 'deepseek', 'permissions': permissions(c, who), **data_control.state(c, who)}


@app.put('/api/libraries')
def put_library(body: LibraryInput, request: Request):
    who = owner(request)
    if not body.name.strip(): raise HTTPException(422, '名称不能为空')
    with db() as c:
        data_control.require_workspace(c, who)
        c.execute('INSERT INTO libraries VALUES(?,?,?) ON CONFLICT(owner,id) DO UPDATE SET name=excluded.name', (who, body.id, body.name.strip()))
    return {'ok': True}


@app.get('/api/permissions')
def get_permissions(request: Request):
    who = owner(request)
    with db() as c: return permissions(c, who)


@app.put('/api/permissions')
def set_permissions(body: PermissionInput, request: Request):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        values = body.model_dump()
        c.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(owner) DO UPDATE SET value=excluded.value', (who, json.dumps(values)))
        for category, enabled in values.items():
            if not enabled:
                c.execute("UPDATE backfill_jobs SET status='cancelled' WHERE owner=? AND category=? AND status IN ('queued','running')", (who, category))
        # A permission change invalidates pending authorizations, even if later reversed.
        invalidate_challenges(c, who, invalid_only=True)
    return body.model_dump()


@app.get('/api/memories')
def memories(library_id: str, request: Request):
    who = owner(request)
    with db() as c:
        library(c, who, library_id)
        return visible(c, who, library_id)


@app.get('/api/memories/{memory_id}/history')
def history(memory_id: str, request: Request):
    who = owner(request)
    with db() as c:
        if not c.execute('SELECT id FROM memories WHERE owner=? AND id=?', (who, memory_id)).fetchone(): raise HTTPException(404)
        return [dict(r) for r in c.execute('SELECT action,before,after,created FROM events WHERE owner=? AND memory=? ORDER BY created', (who, memory_id))]


@app.patch('/api/memories/{memory_id}')
def change_memory(memory_id: str, body: MemoryChange, request: Request):
    who = owner(request)
    with db() as c:
        row = c.execute('SELECT * FROM memories WHERE owner=? AND id=?', (who, memory_id)).fetchone()
        if not row: raise HTTPException(404)
        if row['version'] != body.version: raise HTTPException(409, '条目已更新，请刷新后重试')
        status, content = row['status'], row['content']
        if body.action == 'reject': status = 'rejected'
        elif body.action == 'change_goal': status = 'superseded'
        elif body.action == 'edit':
            if row['status'] != 'active' or row['expires'] < now(): raise HTTPException(409, '仅可修改仍有效的条目')
            content = (body.content or '').strip()
            if not content: raise HTTPException(422, '内容不能为空')
            source = c.execute('SELECT text FROM messages WHERE owner=? AND id=?', (who, row['source'])).fetchone()
            if not allowed(c, who, source['text'] + content): raise HTTPException(403, '相关敏感类别未获授权')
        else: raise HTTPException(422, '不支持此操作')
        c.execute('UPDATE memories SET content=?,status=?,updated=?,version=version+1 WHERE owner=? AND id=?', (content, status, now(), who, memory_id))
        c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)', (who, uid(), memory_id, body.action, json.dumps(dict(row), ensure_ascii=False), json.dumps({'content': content, 'status': status}, ensure_ascii=False), now()))
        invalidate_challenges(c, who, bid=row['bid'], invalid_only=True)
    return {'ok': True}


@app.post('/api/conversations/{cid}/trash')
def trash_conversation(cid: str, request: Request):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        if data_control.erased(c, who, 'conversation', cid):
            raise HTTPException(410, '此对话已永久删除')
        c.execute('INSERT OR IGNORE INTO deleted_conversations VALUES(?,?)', (who, cid))
        invalidate_challenges(c, who, invalid_only=True)
    return {'ok': True}


@app.post('/api/conversations/{cid}/restore')
def restore_conversation(cid: str, request: Request):
    who = owner(request)
    with db() as c:
        if data_control.erased(c, who, 'conversation', cid):
            raise HTTPException(410, '此对话已永久删除')
        c.execute('DELETE FROM deleted_conversations WHERE owner=? AND cid=?', (who, cid))
    return {'ok': True}


def evidence_valid(result, live):
    for mid in result.get('citations', []):
        if mid not in live: return False
        versions = result.get('evidence_versions')
        if versions is not None:
            if versions.get(mid) != live[mid]['version']: return False
        elif live[mid]['content'] not in result['text']: return False
    return True


def allowed_capsule_versions(c, who, bid):
    return {row['id']: row['version'] for row in planning.current_capsules(c, who, bid)
            if allowed(c, who, row['title'] + '\n' + row['body'])} if bid else {}


def capsule_evidence_valid(result, live):
    return all(live.get(identifier) == version for identifier, version in result.get('capsule_versions', {}).items())


def snapshot(c, who, body):
    data_control.require_workspace(c, who)
    if (data_control.erased(c, who, 'conversation', body.conversation)
            or data_control.erased(c, who, 'message', body.id)):
        raise HTTPException(410, '此消息或对话已永久删除')
    if c.execute('SELECT 1 FROM deleted_conversations WHERE owner=? AND cid=?', (who, body.conversation)).fetchone():
        raise HTTPException(409, '此对话已删除，请恢复后继续')
    if body.library: library(c, who, body.library)
    context = c.execute('SELECT * FROM contexts WHERE owner=? AND id=?', (who, body.segment)).fetchone()
    if context and (context['bid'] != body.library or context['cid'] != body.conversation):
        raise HTTPException(409, '上下文段归属不一致')
    records = visible(c, who, body.library) if body.library else []
    live = {m['id']: m for m in records if m['status'] == 'active'}
    versions = allowed_capsule_versions(c, who, body.library)
    capsules = planning.capsule_evidence(planning.current_capsules(c, who, body.library),
                                        lambda text: allowed(c, who, text), body.text) if body.library and not body.webSearch else []
    history, history_messages, history_replies = [], [], []
    prior = c.execute('''SELECT m.*,t.result FROM messages m JOIN turns t ON t.owner=m.owner AND t.id=m.id
        WHERE m.owner=? AND m.cid=? AND m.sid=? AND m.bid IS ?
        AND json_extract(t.result, '$.extraction') IS NOT 'search_only'
        ORDER BY m.created DESC,m.rowid DESC LIMIT 12''',
        (who, body.conversation, body.segment, body.library)).fetchall()
    for row in reversed(prior):
        source_records = [m for m in records if m['source'] == row['id']]
        # Corrected/denied facts must not re-enter through their original chat text.
        if (data_control.blocked(c, who, row['id']) or not allowed(c, who, row['text'])
                or any(m['status'] != 'active' or m['version'] > 1 for m in source_records)):
            continue
        result = json.loads(row['result'])
        history.append({'role': 'user', 'content': row['text']})
        history_messages.append(row['id'])
        if not result.get('redacted') and evidence_valid(result, live) and capsule_evidence_valid(result, versions):
            history.append({'role': 'assistant', 'content': result['text']})
            history_replies.append(row['id'])
    return {'memories': list(live.values())[:12], 'history': history,
            'capsules': capsules, 'data_revision': data_control.revision(c, who),
            'history_messages': history_messages, 'history_replies': history_replies,
            'permissions': permissions(c, who), 'challenge_scope_closed': bool(c.execute(
                'SELECT 1 FROM closed_challenge_scopes WHERE owner=? AND cid=? AND sid=?',
                (who, body.conversation, body.segment)).fetchone())}


def cached_turn(c, who, body, payload_hash):
    old = c.execute('SELECT * FROM turns WHERE owner=? AND id=?', (who, body.id)).fetchone()
    if not old: return None
    if old['hash'] != payload_hash: raise HTTPException(409, '请求标识已经用于另一条消息')
    result = json.loads(old['result'])
    if result.get('redacted'):
        raise HTTPException(410, '这条回复已随关联数据清除，请发送新消息')
    result.pop('input_sources', None)
    result['created'] = [mid for mid in result['created'] if not data_control.erased(c, who, 'memory', mid)]
    live = {m['id']: m for m in visible(c, who, body.library) if m['status'] == 'active'} if body.library else {}
    if not evidence_valid(result, live) or not capsule_evidence_valid(result, allowed_capsule_versions(c, who, body.library)):
        result['text'] = '引用记录已经更新，请重新提问。'
        result['citations'] = []
        result.pop('capsuleRefs', None)
    return result


def goal_versions(records):
    return {m['id']: m['version'] for m in records if m['status'] == 'active' and m['kind'] == 'goal'}


def challenge_evidence(c, row):
    who = row['owner']
    if c.execute('SELECT 1 FROM deleted_conversations WHERE owner=? AND cid=?', (who, row['cid'])).fetchone():
        return None
    if c.execute('SELECT 1 FROM closed_challenge_scopes WHERE owner=? AND cid=? AND sid=?', (who, row['cid'], row['sid'])).fetchone():
        return None
    source = c.execute('SELECT * FROM messages WHERE owner=? AND id=? AND cid=? AND sid=? AND bid=?',
                       (who, row['source'], row['cid'], row['sid'], row['bid'])).fetchone()
    if not source or not allowed(c, who, source['text']):
        return None
    records = visible(c, who, row['bid'])
    if goal_versions(records) != json.loads(row['goals']):
        return None
    memory = next((m for m in records if m['id'] == row['memory'] and m['version'] == row['version'] and m['status'] == 'active'), None)
    return (memory, source) if memory else None


def invalidate_challenges(c, who, *, bid=None, invalid_only=False):
    pending = c.execute("SELECT * FROM challenges WHERE owner=? AND status='pending' AND (? IS NULL OR bid=?)",
                        (who, bid, bid)).fetchall()
    for row in pending:
        if not invalid_only or not challenge_evidence(c, row):
            c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND id=?", (who, row['id']))


@app.get('/api/challenges/pending')
def pending_challenge(conversation: str, segment: str, request: Request):
    who = owner(request)
    with db() as c:
        row = c.execute("SELECT * FROM challenges WHERE owner=? AND cid=? AND sid=? AND status='pending' ORDER BY created DESC LIMIT 1",
                        (who, conversation, segment)).fetchone()
        evidence = challenge_evidence(c, row) if row else None
        if not evidence:
            if row:
                c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND id=?", (who, row['id']))
            return None
        memory, source = evidence
        return {'id': row['id'], 'library': row['bid'], 'segment': row['sid'], 'memory': memory,
                'text': source['text']}


@app.post('/api/conversations/{cid}/challenge-scope')
def leave_challenge_scope(cid: str, segment: str, request: Request):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        if data_control.erased(c, who, 'conversation', cid):
            raise HTTPException(410, '此对话已永久删除')
        c.execute('INSERT OR IGNORE INTO closed_challenge_scopes VALUES(?,?,?)', (who, cid, segment))
        c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND cid=? AND sid=? AND status='pending'",
                  (who, cid, segment))
    return {'ok': True}


@app.post('/api/challenges/{challenge_id}/resolve')
def resolve_challenge(challenge_id: str, body: ChallengeDecision, request: Request):
    who = owner(request)
    with turn_lock(who, 'challenge:' + challenge_id):
        return apply_challenge_decision(who, challenge_id, body)


def apply_challenge_decision(who, challenge_id, body):
    if body.decision == 'confirmed' and not (body.record_ok and body.situation_same and body.goal_same):
        raise HTTPException(422, '请先确认记录、情况和目标')
    with db() as c:
        row = c.execute('SELECT * FROM challenges WHERE owner=? AND id=? AND sid=?',
                        (who, challenge_id, body.segment)).fetchone()
        if not row:
            raise HTTPException(404, '找不到这次待确认事项')
        evidence = challenge_evidence(c, row)
        if row['status'] == 'resolved' and row['decision'] == body.decision and (body.decision != 'confirmed' or evidence):
            return json.loads(row['result'])
        if row['status'] != 'pending' or not evidence:
            raise HTTPException(409, '记录、范围或权限已变化，请重新核对')
        memory, source = evidence
        if body.decision != 'confirmed':
            new_status = {'record_wrong': 'rejected', 'goal_changed': 'superseded'}.get(body.decision)
            if body.decision == 'goal_changed' and memory['kind'] != 'goal':
                new_status = None
            if new_status:
                c.execute('UPDATE memories SET status=?,updated=?,version=version+1 WHERE owner=? AND id=?',
                          (new_status, now(), who, memory['id']))
                c.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?)', (who, uid(), memory['id'], body.decision,
                          json.dumps({'status': memory['status'], 'content': memory['content'], 'version': memory['version']}, ensure_ascii=False),
                          json.dumps({'status': new_status}), now()))
            result = {'id': challenge_id, 'decision': body.decision, 'timestamp': now()}
            finish_challenge(c, who, challenge_id, result)
            invalidate_challenges(c, who, bid=row['bid'], invalid_only=True)
            return result
    text, model = analyze_challenge(memory['content'], source['text'])
    with db() as c:
        row = c.execute('SELECT * FROM challenges WHERE owner=? AND id=?', (who, challenge_id)).fetchone()
        if not row or row['status'] != 'pending' or not challenge_evidence(c, row):
            raise HTTPException(409, '分析期间记录已变化，请重新核对')
        result = {'id': challenge_id, 'decision': 'confirmed', 'text': text, 'model': model,
                  'library': row['bid'], 'citations': [memory['id']], 'timestamp': now()}
        finish_challenge(c, who, challenge_id, result)
        return result


def finish_challenge(c, who, challenge_id, result):
    c.execute("UPDATE challenges SET status='resolved',decision=?,result=? WHERE owner=? AND id=?",
              (result['decision'], json.dumps(result, ensure_ascii=False), who, challenge_id))


def record_candidates(c, who, bid, source, candidates, stamp):
    if data_control.blocked(c, who, source):
        return []
    created = []
    for content, kind, slot in candidates:
        if c.execute('SELECT 1 FROM memories WHERE owner=? AND bid=? AND content=?', (who, bid, content)).fetchone():
            continue
        mid = uid()
        c.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (who, mid, bid, source, content, kind, slot, 'active', stamp, stamp, stamp + 90*86400000, 1))
        created.append(mid)
    return created


@app.put('/api/memories/{memory_id}/followup')
def set_followup(memory_id: str, body: FollowupPermission, request: Request):
    who = owner(request)
    with db() as c:
        row = c.execute('SELECT bid FROM memories WHERE owner=? AND id=?', (who, memory_id)).fetchone()
        if not row:
            raise HTTPException(404, '记忆不存在')
        if body.allowed and not any(m['id'] == memory_id and m['status'] == 'active' for m in visible(c, who, row['bid'])):
            raise HTTPException(409, '只能对有效记忆开启首页提醒')
        c.execute('''INSERT INTO followups VALUES(?,?,?,?) ON CONFLICT(owner,memory) DO UPDATE SET
            due=CASE WHEN followups.allowed=excluded.allowed THEN followups.due ELSE excluded.due END,
            allowed=excluded.allowed''', (who, memory_id, body.allowed, now()))
    return {'allowed': body.allowed}


@app.get('/api/followups')
def followups(library_id: str, request: Request):
    who = owner(request)
    with db() as c:
        library(c, who, library_id)
        active = {m['id']: m for m in visible(c, who, library_id) if m['status'] == 'active'}
        due = c.execute('SELECT memory FROM followups WHERE owner=? AND allowed=1 AND due<=? ORDER BY due, memory',
                        (who, now())).fetchall()
        return [active[row['memory']] for row in due if row['memory'] in active][:3]


@app.post('/api/followups/{memory_id}/act')
def act_on_followup(memory_id: str, body: FollowupAction, request: Request):
    who = owner(request)
    with db() as c:
        row = c.execute('''SELECT m.bid,f.due FROM memories m JOIN followups f ON f.owner=m.owner AND f.memory=m.id
            WHERE m.owner=? AND m.id=? AND f.allowed=1''', (who, memory_id)).fetchone()
        if not row:
            raise HTTPException(404, '提醒已关闭')
        memory = next((m for m in visible(c, who, row['bid']) if m['id'] == memory_id and m['status'] == 'active'), None)
        if body.action == 'stop':
            c.execute('UPDATE followups SET allowed=0 WHERE owner=? AND memory=?', (who, memory_id))
        elif not memory or row['due'] > now():
            raise HTTPException(409, '提醒已更新，请刷新')
        else:
            days = {'start': 7, 'later': 1, 'ignore': 30}[body.action]
            c.execute('UPDATE followups SET due=? WHERE owner=? AND memory=?', (now() + days * 86400000, who, memory_id))
        return {'ok': True, 'memory': memory if body.action == 'start' else None}


def reminder_is_visible(c, who, row):
    deleted = c.execute('SELECT 1 FROM deleted_conversations WHERE owner=? AND cid=?', (who, row['cid'])).fetchone()
    return not deleted and allowed(c, who, row['text'] + '\n' + row['content'])


@app.get('/api/reminders')
def reminders(request: Request, library_id: str | None = None):
    who = owner(request)
    with db() as c:
        if library_id is not None:
            library(c, who, library_id)
        rows = c.execute('''SELECT r.id,r.content,r.due,m.text,m.cid FROM reminders r
            JOIN messages m ON m.owner=r.owner AND m.id=r.id
            WHERE r.owner=? AND m.bid IS ? AND r.status='pending' AND r.due<=? ORDER BY r.due,r.id''',
            (who, library_id, now()))
        return [{key: row[key] for key in ('id', 'content', 'due')}
                for row in rows if reminder_is_visible(c, who, row)][:3]


@app.post('/api/reminders/{reminder_id}/act')
def act_on_reminder(reminder_id: str, body: ReminderAction, request: Request):
    who = owner(request)
    with db() as c:
        row = c.execute('''SELECT r.*,m.text,m.cid FROM reminders r JOIN messages m ON m.owner=r.owner AND m.id=r.id
            WHERE r.owner=? AND r.id=?''', (who, reminder_id)).fetchone()
        if not row:
            raise HTTPException(404, '提醒不存在')
        status = row['status']
        if status != 'pending':
            return {'status': status}
        if body.action == 'cancel':
            status = 'cancelled'
        elif row['due'] > now() or not reminder_is_visible(c, who, row):
            raise HTTPException(409, '提醒已更新，请刷新')
        elif body.action == 'done':
            status = 'done'
        else:
            c.execute('UPDATE reminders SET due=? WHERE owner=? AND id=?', (now() + 86_400_000, who, reminder_id))
        c.execute('UPDATE reminders SET status=? WHERE owner=? AND id=?', (status, who, reminder_id))
        return {'status': status}


def interpret_reminder(text, grants):
    match = request_candidate(text)
    if not match:
        return None
    result = deepseek.extract_reminder(text)
    if result['intent'] == 'none':
        return None
    if any(not grants.get(kind, False) for kind in categories(text)):
        return {'text': '这条提醒涉及当前未开启的敏感记忆，未设置提醒。'}
    if result['intent'] == 'clarify':
        return {'text': '可以一次告诉我一件事，比如「下周一提醒我交报名表」。'}
    task, when = result.get('task'), result.get('when')
    if not isinstance(task, str) or not task.strip() or task.strip() not in match['task'] or len(task.strip()) > 400:
        return {'text': '请再说一下要提醒的事。'}
    if not isinstance(when, str) or (when and when not in match['prefix'] and not match['task'].startswith(when)):
        return {'text': '请再说一下提醒时间，比如「下次」或「下周一」。'}
    prefix = re.sub(r'麻烦(?:你)?|可以|能不能|记得|别忘了|到时候|请|帮我|给我|能|[，,\s]', '', match['prefix'])
    if prefix and not when:
        return {'text': '请再说一下提醒时间，比如「下次」或「下周一」。'}
    if prefix and when in match['prefix']:
        when = prefix
    schedule = resolve_time(when, now())
    if schedule is None:
        return {'text': '这个时间还不能确定。可以说「下次」、「明天」或「下周一下午3点」。'}
    due, date_only = schedule
    if not when or when.startswith('下次'):
        label = '下次到首页'
    else:
        local = datetime.fromtimestamp(due / 1000)
        label = local.strftime('%Y年%m月%d日' if date_only else '%Y年%m月%d日 %H:%M')
    return {'text': f'已设置提醒 · {label}', 'content': task.strip(), 'due': due}

backfill_executor = ThreadPoolExecutor(max_workers=1)
backfill_active = set()
backfill_lock = threading.Lock()


def schedule_backfill(who, job_id):
    key = (who, job_id)
    with backfill_lock:
        if key in backfill_active:
            return
        backfill_active.add(key)
    backfill_executor.submit(run_backfill, who, job_id)


def backfill_source(c, job, source_id):
    source = c.execute('''SELECT m.* FROM messages m WHERE m.owner=? AND m.bid=? AND m.id=? AND m.created>=? AND m.created<?
        AND NOT EXISTS(SELECT 1 FROM deleted_conversations d WHERE d.owner=m.owner AND d.cid=m.cid)
        AND NOT EXISTS(SELECT 1 FROM reminders r WHERE r.owner=m.owner AND r.id=m.id)
        AND NOT EXISTS(SELECT 1 FROM blocked_memory_sources b WHERE b.owner=m.owner AND b.source=m.id)
        AND NOT EXISTS(SELECT 1 FROM turns t WHERE t.owner=m.owner AND t.id=m.id
            AND json_extract(t.result, '$.extraction')='search_only')''',
                       (job['owner'], job['bid'], source_id, job['start'], job['end'])).fetchone()
    if not source or job['category'] not in categories(source['text']) or not allowed(c, job['owner'], source['text']):
        return None
    if c.execute("SELECT 1 FROM memories WHERE owner=? AND bid=? AND source=? AND (status<>'active' OR version>1)",
                 (job['owner'], job['bid'], source_id)).fetchone():
        return None
    return source


def run_backfill(who, job_id):
    try:
        while True:
            with db() as c:
                job = c.execute('SELECT * FROM backfill_jobs WHERE owner=? AND id=?', (who, job_id)).fetchone()
                if not job or job['status'] not in ('queued', 'running'):
                    return
                if not permissions(c, who).get(job['category']):
                    c.execute("UPDATE backfill_jobs SET status='cancelled' WHERE owner=? AND id=?", (who, job_id))
                    return
                item = c.execute('SELECT source FROM backfill_items WHERE owner=? AND job=? ORDER BY rowid LIMIT 1', (who, job_id)).fetchone()
                if not item:
                    c.execute("UPDATE backfill_jobs SET status='completed' WHERE owner=? AND id=?", (who, job_id))
                    return
                c.execute("UPDATE backfill_jobs SET status='running' WHERE owner=? AND id=?", (who, job_id))
                source = backfill_source(c, job, item['source'])
            candidates, _, failed = choose_memories(source['text']) if source else ([], 'none', False)
            with db() as c:
                current = c.execute('SELECT status FROM backfill_jobs WHERE owner=? AND id=?', (who, job_id)).fetchone()
                if not current or current['status'] != 'running':
                    return
                if not c.execute('SELECT 1 FROM backfill_items WHERE owner=? AND job=? AND source=?',
                                 (who, job_id, item['source'])).fetchone():
                    continue
                added = []
                if source and backfill_source(c, job, item['source']):
                    scoped = [item for item in candidates if job['category'] in categories(item[0])]
                    added = record_candidates(c, who, job['bid'], source['id'], scoped, now())
                    invalidate_challenges(c, who, bid=job['bid'], invalid_only=True)
                c.execute('DELETE FROM backfill_items WHERE owner=? AND job=? AND source=?', (who, job_id, item['source']))
                c.execute('UPDATE backfill_jobs SET processed=processed+1,recorded=recorded+?,failed=failed+? WHERE owner=? AND id=?',
                          (len(added), int(failed and not added), who, job_id))
    except Exception:
        deepseek.logger.exception('Memory backfill failed')
        with db() as c:
            c.execute("UPDATE backfill_jobs SET status='failed' WHERE owner=? AND id=? AND status IN ('queued','running')", (who, job_id))
    finally:
        with backfill_lock:
            backfill_active.discard((who, job_id))


@app.post('/api/backfills')
def start_backfill(body: BackfillInput, request: Request):
    who = owner(request)
    if body.end <= body.start:
        raise HTTPException(422, '结束时间必须晚于开始时间')
    with db() as c:
        library(c, who, body.library)
        if not permissions(c, who).get(body.category):
            raise HTTPException(403, '请先开启对应的敏感记忆类别')
        existing = c.execute('''SELECT * FROM backfill_jobs WHERE owner=? AND bid=? AND category=? AND start=? AND end=?
            AND status IN ('queued','running')''', (who, body.library, body.category, body.start, body.end)).fetchone()
        if existing:
            return dict(existing)
        messages = c.execute('''SELECT m.id,m.text FROM messages m WHERE m.owner=? AND m.bid=? AND m.created>=? AND m.created<?
            AND NOT EXISTS(SELECT 1 FROM deleted_conversations d WHERE d.owner=m.owner AND d.cid=m.cid)
            AND NOT EXISTS(SELECT 1 FROM blocked_memory_sources b WHERE b.owner=m.owner AND b.source=m.id)
            AND NOT EXISTS(SELECT 1 FROM turns t WHERE t.owner=m.owner AND t.id=m.id
                AND json_extract(t.result, '$.extraction')='search_only') ORDER BY m.created''',
                             (who, body.library, body.start, body.end)).fetchall()
        sources = [m['id'] for m in messages if body.category in categories(m['text']) and allowed(c, who, m['text'])]
        job_id = uid()
        status = 'queued' if sources else 'completed'
        c.execute('INSERT INTO backfill_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (who, job_id, body.library, body.category, body.start, body.end, status, len(sources), 0, 0, 0, now()))
        c.executemany('INSERT INTO backfill_items VALUES(?,?,?)', ((who, job_id, source) for source in sources))
        job = dict(c.execute('SELECT * FROM backfill_jobs WHERE owner=? AND id=?', (who, job_id)).fetchone())
    if sources:
        schedule_backfill(who, job_id)
    return job


@app.get('/api/backfills')
def backfills(library_id: str, request: Request):
    who = owner(request)
    with db() as c:
        library(c, who, library_id)
        return [dict(row) for row in c.execute('SELECT * FROM backfill_jobs WHERE owner=? AND bid=? ORDER BY created DESC,rowid DESC LIMIT 5',
                                              (who, library_id))]


@app.post('/api/turn')
def turn(body: Turn, request: Request):
    who = owner(request)
    with turn_lock(who, body.id):
        return process_turn(body, who)


@app.post('/api/turn/stream')
def turn_stream(body: Turn, request: Request):
    who = owner(request)

    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue()
        closed = threading.Event()

        def emit(event):
            if not closed.is_set():
                loop.call_soon_threadsafe(queue.put_nowait, event)

        def run():
            try:
                with turn_lock(who, body.id):
                    result = process_turn(body, who, on_text=lambda text: emit({'type': 'text', 'text': text}))
                emit({'type': 'done', 'result': result})
            except HTTPException as error:
                emit({'type': 'error', 'detail': error.detail})
            except Exception:
                deepseek.logger.exception('Chat stream failed')
                emit({'type': 'error', 'detail': '本地服务暂不可用，请重试'})

        loop.run_in_executor(None, run)
        try:
            while True:
                event = await queue.get()
                yield json.dumps(event, ensure_ascii=False) + '\n'
                if event['type'] != 'text':
                    break
        finally:
            closed.set()

    return StreamingResponse(events(), media_type='application/x-ndjson', headers={'Cache-Control': 'no-cache'})


def archive_context(c, who, body):
    if not body.library or not allowed(c, who, body.text):
        return [], []
    if re.search(r'(?:^|[，。；\n])\s*(?:请)?(?:不要|别|不用|不必)(?:记|保存)|不想让你[^。；\n]*记', body.text):
        return [], []

    def permitted(text):
        return allowed(c, who, text)
    trackers = archive.trackers(c, who, body.library, permitted)
    evidence = []
    if re.search(r'我|目标|原本|以前|现在|改主意|不再', body.text):
        evidence = archive.change_candidates(visible(c, who, body.library), permitted)
    return trackers, evidence


def process_turn(body, who, on_text=None):
    if body.mode not in ['共情', '分析', '挑战', '自动']: raise HTTPException(422, '模式不正确')
    if not body.text.strip(): raise HTTPException(422, '消息不能为空')
    # Preserve hashes written by previous versions for ordinary chat retries.
    payload = body.model_dump_json(exclude=None if body.webSearch else {'webSearch'})
    payload_hash = hashlib.sha256(payload.encode()).hexdigest()
    with db() as c:
        before = snapshot(c, who, body)
        cached = cached_turn(c, who, body, payload_hash)
        if cached is not None: return cached
        turn_started = now()
        trackers, previous_views = ([], []) if body.webSearch else archive_context(c, who, body)
        open_challenge = c.execute("SELECT * FROM challenges WHERE owner=? AND cid=? AND sid=? AND status='pending'",
                                   (who, body.conversation, body.segment)).fetchone()
        if open_challenge and not challenge_evidence(c, open_challenge):
            c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND id=?", (who, open_challenge['id']))
            open_challenge = None
    # Network I/O never holds the SQLite write lock. Failures leave no partial turn.
    reminder = None if body.webSearch else interpret_reminder(body.text, before['permissions'])
    candidate = None
    proposal, archive_failed = None, False
    candidates, memory_method, memory_failed = [], 'none', False
    if body.webSearch:
        sources = web_search.search(body.text)
        generated = deepseek.generate(body.text, body.mode, [], [], on_text=on_text, web_results=sources)
    elif reminder:
        generated = {'text': reminder['text'], 'behavior': '提醒', 'citations': [], 'provider': 'local-reminder'}
    else:
        can_extract = body.library and all(before['permissions'].get(k, False) for k in categories(body.text))
        # Memory classification cannot turn a valid conversational reply into an error.
        with ThreadPoolExecutor(max_workers=2) as pool:
            memory_job = pool.submit(choose_memories, body.text) if can_extract else None
            archive_job = None
            if trackers or previous_views:
                archive_job = pool.submit(
                    judge_archive, body.text,
                    [{key: t[key] for key in ('id', 'name', 'unit')} for t in trackers],
                    [{key: m[key] for key in ('id', 'content', 'kind')} for m in previous_views],
                )
            args = (body.text, body.mode, before['memories'], before['history'])
            candidate_id = None
            if can_extract and body.mode in ('挑战', '自动') and before['memories'] and not before['challenge_scope_closed'] and not open_challenge:
                try:
                    candidate_id = nominate_challenge(body.text, before['memories'], body.mode)
                except HTTPException:
                    pass
            candidate = next((m for m in before['memories'] if m['id'] == candidate_id), None)
            if candidate:
                generated = {'text': '这次的话和一条旧记录可能有关。我们先核对，再决定要不要继续分析。',
                             'behavior': '挑战', 'citations': [candidate['id']], 'provider': 'local-confirmation'}
            elif before['capsules']:
                # Capsule-derived text is sent only after the permission and version check below.
                generated = deepseek.generate(*args, capsules=before['capsules'])
                generated['capsule_versions'] = {row['id']: row['version'] for row in before['capsules']}
            else:
                generated = deepseek.generate(*args, on_text=on_text) if on_text else generate_reply(*args)
            if memory_job:
                candidates, memory_method, memory_failed = memory_job.result()
            if archive_job:
                try:
                    proposal = archive_job.result()
                except HTTPException:
                    archive_failed = True
    with db() as c:
        after = snapshot(c, who, body)
        cached = cached_turn(c, who, body, payload_hash)
        if cached is not None: return cached
        if before != after:
            raise HTTPException(409, '对话或记忆在回复期间发生变化，请重试')
        if not c.execute('SELECT 1 FROM contexts WHERE owner=? AND id=?', (who, body.segment)).fetchone():
            c.execute('INSERT INTO contexts VALUES(?,?,?,?)', (who, body.segment, body.conversation, body.library))
        stamp = now()
        c.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?)', (who, body.id, body.conversation, body.segment, body.library, body.text, stamp))
        c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND cid=? AND sid<>? AND status='pending'",
                  (who, body.conversation, body.segment))
        extraction, created = 'no_match', []
        if body.webSearch:
            extraction = 'search_only'
        elif reminder:
            extraction = 'reminder'
            if 'content' in reminder:
                c.execute("INSERT INTO reminders VALUES(?,?,?,?,'pending')", (who, body.id, reminder['content'], reminder['due']))
                generated['reminderId'] = body.id
        elif not body.library: extraction = 'unbound'
        elif not allowed(c, who, body.text): extraction = 'sensitive_skipped'
        elif candidate:
            extraction, memory_method = 'deferred', 'none'
            goals = goal_versions(visible(c, who, body.library))
            c.execute("UPDATE challenges SET status='invalidated' WHERE owner=? AND cid=? AND status='pending'",
                      (who, body.conversation))
            c.execute('INSERT INTO challenges VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (who, uid(), body.conversation, body.segment, body.library, body.id, candidate['id'],
                       candidate['version'], json.dumps(goals), 'pending', None, None, stamp))
        else:
            created = record_candidates(c, who, body.library, body.id, candidates, stamp)
            extraction = 'recorded' if created else ('duplicate' if candidates else ('unavailable' if memory_failed else 'no_match'))
            if created:
                invalidate_challenges(c, who, bid=body.library, invalid_only=True)
        if proposal and any(proposal.values()):
            live_trackers, live_views = archive_context(c, who, body)
            def permitted(text):
                return allowed(c, who, text)
            counts = archive.record_proposals(c, who, body.library, body.id, body.text, turn_started, proposal,
                                              [t for t in trackers if t in live_trackers],
                                              [m for m in previous_views if m in live_views], permitted)
            if any(counts.values()):
                generated['archive'] = counts
        if archive_failed:
            generated['archive'] = {'recorded': 0, 'pending': 0, 'changes': 0, 'unavailable': True}
        if body.library and not body.webSearch and not reminder and allowed(c, who, body.text):
            quote = planning.draft_from_chat(body.text)
            if quote and not re.search(r'(?:不要|别|不用|不必)(?:记|保存)', body.text):
                generated['decisionDraftId'] = planning.save_draft(c, who, body.library, body.id, quote, stamp)
        result = {**generated, 'extraction': extraction, 'created': created, 'timestamp': stamp,
                  'memory_method': memory_method,
                  'evidence_versions': {m['id']: m['version'] for m in before['memories'] if m['id'] in generated['citations']}}
        inputs = {'messages': before['history_messages'], 'replies': before['history_replies'],
                  'memories': [memory['id'] for memory in before['memories']]}
        c.execute('INSERT INTO turns VALUES(?,?,?,?)', (who, body.id, payload_hash,
                  json.dumps({**result, 'input_sources': inputs}, ensure_ascii=False)))
        return result


def archive_tracker(c, who, bid, tracker_id, permitted):
    tracker = next((item for item in archive.trackers(c, who, bid, permitted) if item['id'] == tracker_id), None)
    if not tracker:
        raise HTTPException(404, '找不到这个跟踪项')
    return tracker


@app.get('/api/archive')
def read_archive(library_id: str, request: Request):
    who = owner(request)
    with db() as c:
        library(c, who, library_id)
        def permitted(text):
            return allowed(c, who, text)
        stamp = now()
        memories = [m for m in visible(c, who, library_id)
                    if permitted(m['content'] + '\n' + m['sourceMessage']['text'])]
        timeline = [m for m in memories if m['status'] in ('active', 'superseded', 'expired')]
        all_trackers = [archive.tracker_view(c, who, t, stamp, permitted)
                        for t in archive.trackers(c, who, library_id, permitted)]
        first = c.execute('SELECT created FROM workspace_started WHERE owner=?', (who,)).fetchone()
        days = max(1, (archive.today(stamp) - archive.today(first['created'] if first else stamp)).days + 1)
        relevant = {m['id']: m['content'] if m['status'] != 'rejected' else None for m in memories}
        corrections = [
            {'id': row['id'], 'action': row['action'], 'created': row['created'], 'content': relevant[row['memory']]}
            for row in c.execute('''SELECT e.id,e.action,e.created,e.memory FROM events e
                JOIN memories m ON m.owner=e.owner AND m.id=e.memory
                WHERE e.owner=? AND m.bid=? AND e.action IN ('edit','change_goal','goal_changed','reject','record_wrong')
                ORDER BY e.created DESC,e.rowid DESC''', (who, library_id))
            if row['memory'] in relevant
        ]
        evidence = archive.portrait_evidence(memories)
        return {'memories': timeline, 'trackers': all_trackers,
                'decisions': planning.decisions(c, who, library_id),
                'changes': archive.changes(c, who, library_id, memories, permitted),
                'portrait': archive.read_portrait(c, who, library_id, evidence, permitted),
                'portraitNeedsRefresh': archive.portrait_needs_refresh(c, who, library_id, evidence),
                'relation': {'days': days, 'corrections': corrections[:10]}}


def portrait_snapshot(c, who, bid):
    library(c, who, bid)
    return archive.portrait_evidence([
        m for m in visible(c, who, bid) if allowed(c, who, m['content'] + '\n' + m['sourceMessage']['text'])
    ])


@app.post('/api/archive/{bid}/portrait/refresh')
def refresh_portrait(bid: str, request: Request):
    who = owner(request)
    with turn_lock(who, ('portrait', bid)):
        with db() as c:
            evidence = portrait_snapshot(c, who, bid)
            if not archive.portrait_needs_refresh(c, who, bid, evidence):
                return {'ok': True}
        compact = [{'id': m['id'], 'kind': m['kind'], 'content': m['content'],
                    'message': m['sourceMessage']['id']} for m in evidence]
        proposed = judge_portrait(compact, archive.PORTRAIT_FACETS)
        with db() as c:
            current = portrait_snapshot(c, who, bid)
            fingerprint = archive.portrait_fingerprint(current)
            if fingerprint != archive.portrait_fingerprint(evidence):
                raise HTTPException(409, '记忆已变化，请重新读取画像')
            leaves = archive.clean_portrait(proposed, current, lambda text: allowed(c, who, text))
            c.execute('''INSERT INTO archive_portraits VALUES(?,?,?,?) ON CONFLICT(owner,bid) DO UPDATE SET
                fingerprint=excluded.fingerprint,leaves=excluded.leaves''',
                (who, bid, fingerprint, json.dumps(leaves, ensure_ascii=False)))
    return {'ok': True}


@app.post('/api/archive/{bid}/portrait/{facet}/dismiss')
def dismiss_portrait(bid: str, facet: str, request: Request):
    who = owner(request)
    if facet not in archive.PORTRAIT_FACETS:
        raise HTTPException(404, '找不到这片叶子')
    with db() as c:
        evidence = portrait_snapshot(c, who, bid)
        leaves = archive.read_portrait(c, who, bid, evidence, lambda text: allowed(c, who, text))
        leaf = next((item for item in leaves if item['facet'] == facet), None)
        if leaf:
            c.execute('INSERT OR IGNORE INTO archive_portrait_corrections VALUES(?,?,?,?,?,?)',
                      (who, bid, facet, json.dumps(sorted(leaf['evidence'])), leaf['title'], leaf['summary']))
    return {'ok': True}


@app.post('/api/archive/{bid}/trackers')
def new_tracker(bid: str, body: archive.TrackerInput, request: Request):
    who = owner(request)
    with db() as c:
        library(c, who, bid)
        archive.create_tracker(c, who, bid, body, now(), lambda text: allowed(c, who, text))
    return {'ok': True}


@app.put('/api/archive/{bid}/trackers/{tracker_id}/target')
def change_tracker_target(bid: str, tracker_id: str, body: archive.TargetInput, request: Request):
    who = owner(request)
    with db() as c:
        tracker = archive_tracker(c, who, bid, tracker_id, lambda text: allowed(c, who, text))
        archive.set_target(c, who, tracker, body, now())
    return {'ok': True}


@app.post('/api/archive/{bid}/trackers/{tracker_id}/entries')
def add_checkin(bid: str, tracker_id: str, body: archive.CheckIn, request: Request):
    who = owner(request)
    with db() as c:
        def permitted(text):
            return allowed(c, who, text)
        tracker = archive_tracker(c, who, bid, tracker_id, permitted)
        status = archive.write_checkin(c, who, tracker, body, now(), permitted)
    return {'status': status}


@app.patch('/api/archive/{bid}/trackers/{tracker_id}/entries/{entry_id}')
def change_checkin(bid: str, tracker_id: str, entry_id: str, body: archive.CheckInAction, request: Request):
    who = owner(request)
    with db() as c:
        def permitted(text):
            return allowed(c, who, text)
        tracker = archive_tracker(c, who, bid, tracker_id, permitted)
        archive.checkin_action(c, who, tracker, entry_id, body, permitted)
    return {'ok': True}


@app.patch('/api/archive/{bid}/changes/{change_id}')
def correct_change(bid: str, change_id: str, body: archive.ChangeAction, request: Request):
    who = owner(request)
    with db() as c:
        updated = c.execute('UPDATE cognitive_changes SET status=? WHERE owner=? AND bid=? AND id=?',
                            (body.action, who, bid, change_id))
        if not updated.rowcount:
            raise HTTPException(404, '找不到这条变化')
    return {'ok': True}


@contextmanager
def planning_scope(request, bid):
    who = owner(request)
    with db() as c:
        library(c, who, bid)
        yield c, who


@app.get('/api/planning/{bid}/home')
def planning_home(bid: str, request: Request):
    with planning_scope(request, bid) as (c, who):
        day = archive.today(now()).isoformat()
        due_capsules = [row for row in planning.current_capsules(c, who, bid) if planning.capsule_due(row, day)]
        due_decisions = [row for row in planning.decisions(c, who, bid)
                         if not row['draft'] and not row['result'] and row['due'] and row['due'] <= day]
        return {'capsules': [{key: row[key] for key in ('id', 'kind', 'due', 'version')}
                             for row in sorted(due_capsules, key=lambda row: row['due'])[:3]],
                'decisions': [{'id': row['id'], 'title': row['title'] if allowed(c, who, row['title']) else '一项决定'}
                              for row in sorted(due_decisions, key=lambda row: row['due'])[:3]]}


@app.get('/api/planning/{bid}/decisions')
def read_decisions(bid: str, request: Request):
    with planning_scope(request, bid) as (c, who):
        return planning.decisions(c, who, bid)


@app.put('/api/planning/{bid}/decisions')
def put_decision(bid: str, body: planning.DecisionInput, request: Request):
    with planning_scope(request, bid) as (c, who):
        planning.save_decision(c, who, bid, body, now())
        return planning.decision(c, who, bid, body.id)


@app.delete('/api/planning/{bid}/decisions/{identifier}')
def delete_decision(bid: str, identifier: str, version: int, request: Request):
    with planning_scope(request, bid) as (c, who):
        planning.check_version(planning.decision(c, who, bid, identifier), version)
        c.execute('DELETE FROM decisions WHERE owner=? AND bid=? AND id=?', (who, bid, identifier))
        return {'ok': True}


@app.post('/api/planning/{bid}/decisions/{identifier}/review')
def review_decision(bid: str, identifier: str, body: planning.VersionInput, request: Request):
    who = owner(request)
    with turn_lock(who, ('decision-review', bid, identifier)):
        def read(c):
            library(c, who, bid)
            row = planning.decision(c, who, bid, identifier)
            planning.check_version(row, body.version)
            if row['draft'] or not row['result']:
                raise HTTPException(422, '请先保存决定和实际结果')
            evidence = {key: row[key] for key in ('title', 'choice', 'options', 'reason', 'expectation', 'result')}
            if not allowed(c, who, '\n'.join(evidence.values()) + '\n' + (row['original'] or '')):
                raise HTTPException(403, '相关敏感类别尚未授权给 AI')
            return row, evidence

        with db() as c:
            row, evidence = read(c)
            if row['review']:
                return {'review': row['review']}
        result = deepseek.review_decision(evidence)
        with db() as c:
            read(c)
            c.execute('UPDATE decisions SET review=? WHERE owner=? AND bid=? AND id=?', (result, who, bid, identifier))
        return {'review': result}


@app.get('/api/planning/{bid}/capsules')
def read_capsules(bid: str, request: Request, preview_day: date | None = None):
    with planning_scope(request, bid) as (c, who):
        day = (preview_day or archive.today(now())).isoformat()
        return {'preview': preview_day is not None, 'day': day,
                'items': [{**planning.capsule_metadata(row), 'dueNow': bool(planning.capsule_due(row, day))}
                          for row in planning.current_capsules(c, who, bid)]}


@app.get('/api/planning/{bid}/capsules/{identifier}')
def read_capsule(bid: str, identifier: str, request: Request):
    with planning_scope(request, bid) as (c, who):
        row = planning.capsule(c, who, bid, identifier)
        return {**row, 'dueNow': bool(planning.capsule_due(row, archive.today(now()).isoformat())),
                'versions': planning.capsule_versions(c, who, bid, identifier)}


@app.get('/api/planning/{bid}/capsules/{identifier}/images/{image_id}')
def read_capsule_image(bid: str, identifier: str, image_id: str, request: Request):
    with planning_scope(request, bid) as (c, who):
        image = c.execute('''SELECT mime,data FROM capsule_images WHERE owner=? AND bid=? AND id=? AND digest=?''',
                          (who, bid, identifier, image_id)).fetchone()
        if not image:
            raise HTTPException(404, '找不到这张胶囊图片')
        return Response(image['data'], media_type=image['mime'], headers={
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
        })


@app.put('/api/planning/{bid}/capsules')
def put_capsule(bid: str, body: planning.CapsuleInput, request: Request):
    with planning_scope(request, bid) as (c, who):
        planning.save_capsule(c, who, bid, body, now())
        return {'id': body.id}


@app.post('/api/planning/{bid}/capsules/{identifier}/act')
def capsule_action(bid: str, identifier: str, body: planning.CapsuleAction, request: Request):
    with planning_scope(request, bid) as (c, who):
        planning.act_on_capsule(c, who, bid, identifier, body, now())
        return {'ok': True}


@app.delete('/api/planning/{bid}/capsules/{identifier}')
def delete_capsule(bid: str, identifier: str, version: int, request: Request):
    with planning_scope(request, bid) as (c, who):
        planning.check_version(planning.capsule(c, who, bid, identifier), version)
        c.execute('DELETE FROM capsules WHERE owner=? AND bid=? AND id=?', (who, bid, identifier))
        return {'ok': True}


@app.get('/api/context-summary')
def context_summary(library_id: str, request: Request):
    """Recomputed from usable records, so corrections cannot leave stale summaries."""
    who = owner(request)
    with db() as c:
        library(c, who, library_id)
        rows = [m for m in visible(c, who, library_id) if m['status'] == 'active']
        return {'provider': 'local-rules', 'summary': '\n'.join(m['content'] for m in rows[:12]), 'sources': [m['source'] for m in rows[:12]]}


@app.get('/api/data/state')
def workspace_data_state(request: Request, response: Response):
    who = owner(request, check_workspace=False)
    with db() as c:
        data_control.require_workspace(c, who)
        response.headers['Cache-Control'] = 'no-store'
        return data_control.state(c, who)


@app.get('/api/data/export')
def export_workspace_data(request: Request, response: Response, library_id: str | None = None):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        timeline, portrait = [], []
        if library_id is not None:
            library(c, who, library_id)
            records = visible(c, who, library_id)
            timeline = [
                {key: memory[key] for key in ('id', 'source', 'content', 'kind', 'status', 'created', 'updated')}
                for memory in records if memory['status'] in ('active', 'superseded', 'expired')
            ]
            portrait = archive.read_portrait(c, who, library_id, portrait_snapshot(c, who, library_id),
                                             lambda text: allowed(c, who, text))
        response.headers['Cache-Control'] = 'private, no-store'
        return {'workspace': who, 'revision': data_control.revision(c, who),
                **data_control.export_library(c, who, library_id, now(), timeline, portrait)}


@app.delete('/api/data/memories/{memory_id}')
def permanently_delete_memory(memory_id: str, body: EraseMemory, request: Request):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        data_control.erase_memory(c, who, memory_id, body.version, body.scope == 'source', body.blockSource)
        return data_control.state(c, who)


@app.delete('/api/data/conversations/{conversation_id}')
def permanently_delete_conversation(conversation_id: str, request: Request):
    who = owner(request)
    with db() as c:
        data_control.require_workspace(c, who)
        data_control.erase_conversation(c, who, conversation_id)
        return data_control.state(c, who)


@app.delete('/api/data/workspace')
def erase_workspace_data(request: Request):
    who = owner(request)
    with db() as c:
        new_owner = uid()
        data_control.reset_workspace(c, who, new_owner, now())
        return data_control.state(c, new_owner)


if static_dir := os.environ.get('QIYU_STATIC_DIR'):
    app.mount('/', StaticFiles(directory=static_dir, html=True), name='frontend')
