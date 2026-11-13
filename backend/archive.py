"""Scoped archive, tracker records and their source evidence."""

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field


SCHEMA = '''
CREATE TABLE IF NOT EXISTS workspace_started(owner TEXT PRIMARY KEY,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS trackers(
    owner TEXT,id TEXT,bid TEXT,name TEXT,unit TEXT,created INTEGER,PRIMARY KEY(owner,id),UNIQUE(owner,bid,name)
);
CREATE TABLE IF NOT EXISTS tracker_goals(
    owner TEXT,tracker TEXT,id TEXT,amount REAL,starts INTEGER,PRIMARY KEY(owner,id)
);
CREATE TABLE IF NOT EXISTS tracker_entries(
    owner TEXT,id TEXT,tracker TEXT,goal TEXT,day TEXT,amount REAL,outcome TEXT,status TEXT,
    created INTEGER,version INTEGER DEFAULT 1,PRIMARY KEY(owner,id)
);
CREATE TABLE IF NOT EXISTS tracker_sources(
    owner TEXT,id TEXT,entry TEXT,message TEXT,quote TEXT,created INTEGER,PRIMARY KEY(owner,id)
);
CREATE INDEX IF NOT EXISTS tracker_entry_day ON tracker_entries(owner,tracker,day,status);
CREATE INDEX IF NOT EXISTS tracker_source_entry ON tracker_sources(owner,entry);
CREATE TABLE IF NOT EXISTS cognitive_changes(
    owner TEXT,id TEXT,bid TEXT,old_memory TEXT,old_content TEXT,old_version INTEGER,
    new_message TEXT,quote TEXT,status TEXT,created INTEGER,PRIMARY KEY(owner,id),
    UNIQUE(owner,bid,old_memory,old_version,quote)
);
CREATE TABLE IF NOT EXISTS archive_portraits(
    owner TEXT,bid TEXT,fingerprint TEXT,leaves TEXT,PRIMARY KEY(owner,bid)
);
CREATE TABLE IF NOT EXISTS archive_portrait_corrections(
    owner TEXT,bid TEXT,facet TEXT,proofs TEXT,title TEXT,summary TEXT,PRIMARY KEY(owner,bid,facet,proofs)
);
'''

PORTRAIT_FACETS = {
    'learning': '学习方式', 'challenges': '面对难题', 'communication': '沟通方式',
    'decisions': '做决定', 'habits': '生活习惯', 'planning': '计划习惯',
    'interests': '兴趣偏好', 'collaboration': '与人合作',
}


def portrait_evidence(memories):
    return [m for m in memories if m['status'] == 'active'][:32]


def portrait_fingerprint(memories):
    versions = [(m['id'], m['version']) for m in memories]
    return hashlib.sha256(json.dumps(versions).encode()).hexdigest()


def portrait_proofs(ids, memories):
    sources = {re.sub(r'[\W_]+', '', m['sourceMessage']['text']) for m in memories if m['id'] in ids}
    messages = {m['sourceMessage']['id'] for m in memories if m['id'] in ids}
    return len(sources) >= 2 and len(messages) >= 2


def portrait_needs_refresh(c, who, bid, memories):
    if not portrait_proofs({m['id'] for m in memories}, memories):
        return False
    cached = c.execute('SELECT fingerprint FROM archive_portraits WHERE owner=? AND bid=?', (who, bid)).fetchone()
    return not cached or cached['fingerprint'] != portrait_fingerprint(memories)


def clean_portrait(proposed, memories, permitted):
    current = {m['id']: m for m in memories}
    leaves = {}
    for item in proposed:
        if not isinstance(item, dict):
            continue
        facet, title, summary, ids = (item.get(k) for k in ('facet', 'title', 'summary', 'evidence'))
        if not isinstance(facet, str) or facet not in PORTRAIT_FACETS or facet in leaves:
            continue
        if not isinstance(title, str) or not isinstance(summary, str):
            continue
        title, summary = title.strip(), summary.strip()
        if not 2 <= len(title) <= 14 or not 8 <= len(summary) <= 100 or not permitted(title + '\n' + summary):
            continue
        if not isinstance(ids, list) or not all(isinstance(mid, str) and mid in current for mid in ids):
            continue
        ids = list(dict.fromkeys(ids))[:5]
        if portrait_proofs(ids, memories):
            leaves[facet] = {'facet': facet, 'title': title, 'summary': summary,
                             'versions': {mid: current[mid]['version'] for mid in ids}}
    return list(leaves.values())


def read_portrait(c, who, bid, memories, permitted):
    row = c.execute('SELECT fingerprint,leaves FROM archive_portraits WHERE owner=? AND bid=?', (who, bid)).fetchone()
    if not row:
        return []
    live = {m['id']: m for m in memories}
    corrections = c.execute(
        'SELECT facet,proofs,title,summary FROM archive_portrait_corrections WHERE owner=? AND bid=?', (who, bid)
    ).fetchall()
    result = []
    for leaf in json.loads(row['leaves']):
        evidence = leaf['versions']
        if not permitted(leaf['title'] + '\n' + leaf['summary']):
            continue
        if not all(mid in live and live[mid]['version'] == version for mid, version in evidence.items()):
            continue
        if any(row['facet'] == leaf['facet'] and (
            set(evidence) <= set(json.loads(row['proofs'])) or
            (row['title'], row['summary']) == (leaf['title'], leaf['summary'])
        ) for row in corrections):
            continue
        result.append({'facet': leaf['facet'], 'title': leaf['title'], 'summary': leaf['summary'],
                       'evidence': list(evidence)})
    return result


class TrackerInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=40)
    unit: str = Field(min_length=1, max_length=10)
    target: float = Field(gt=0, le=100000)


class TargetInput(BaseModel):
    target: float = Field(gt=0, le=100000)
    goal: str


class CheckIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    day: date
    outcome: Literal['done', 'missed']
    amount: float | None = Field(default=None, gt=0, le=100000)
    note: str = Field(default='', max_length=280)


class CheckInAction(BaseModel):
    action: Literal['merge', 'separate', 'edit', 'revoke']
    version: int = Field(ge=1)
    into: str | None = None
    amount: float | None = Field(default=None, gt=0, le=100000)


class ChangeAction(BaseModel):
    action: Literal['dismissed', 'different_context']


def today(stamp):
    return datetime.fromtimestamp(stamp / 1000).date()


def trackers(c, who, bid, permitted):
    rows = c.execute('SELECT * FROM trackers WHERE owner=? AND bid=? ORDER BY created,id', (who, bid))
    return [dict(row) for row in rows if permitted(row['name'] + '\n' + row['unit'])]


def goal_history(c, who, tracker_id):
    return [dict(row) for row in c.execute(
        'SELECT id,amount,starts FROM tracker_goals WHERE owner=? AND tracker=? ORDER BY starts,rowid', (who, tracker_id))]


def create_tracker(c, who, bid, body, stamp, permitted):
    name, unit = body.name.strip(), body.unit.strip()
    if not name or not unit:
        raise HTTPException(422, '请输入跟踪项名称和单位')
    if not permitted(name + '\n' + unit):
        raise HTTPException(403, '请先在记忆台账开启相关敏感类别')
    prior = c.execute('SELECT * FROM trackers WHERE owner=? AND id=?', (who, body.id)).fetchone()
    if prior:
        if (prior['bid'], prior['name'], prior['unit']) == (bid, name, unit) and goal_history(c, who, body.id)[0]['amount'] == body.target:
            return
        raise HTTPException(409, '此请求已经用于另一项跟踪')
    if c.execute('SELECT 1 FROM trackers WHERE owner=? AND bid=? AND name=?', (who, bid, name)).fetchone():
        raise HTTPException(409, '这个记忆库已有同名跟踪项')
    c.execute('INSERT INTO trackers VALUES(?,?,?,?,?,?)', (who, body.id, bid, name, unit, stamp))
    c.execute('INSERT INTO tracker_goals VALUES(?,?,?,?,?)', (who, body.id, str(uuid.uuid4()), body.target, stamp))


def set_target(c, who, tracker, body, stamp):
    current = goal_history(c, who, tracker['id'])[-1]
    if current['amount'] == body.target:
        return
    if current['id'] != body.goal:
        raise HTTPException(409, '目标已更新，请重新打开后修改')
    c.execute('INSERT INTO tracker_goals VALUES(?,?,?,?,?)', (who, tracker['id'], str(uuid.uuid4()), body.target, stamp))


def source_rows(c, who, entry_id, bid, permitted):
    sources = c.execute('''SELECT s.id,s.message,s.quote,s.created,m.text,m.created AS message_time
        FROM tracker_sources s
        LEFT JOIN messages m ON s.owner=m.owner AND s.message=m.id AND m.bid=?
        LEFT JOIN deleted_conversations d ON d.owner=m.owner AND d.cid=m.cid
        WHERE s.owner=? AND s.entry=? AND d.cid IS NULL ORDER BY s.created,s.rowid''', (bid, who, entry_id))
    return [{'id': row['id'], 'channel': 'chat' if row['message'] else 'manual',
             'text': row['text'] if row['message'] else row['quote'], 'quote': row['quote'],
             'created': row['message_time'] if row['message'] else row['created']}
            for row in sources if (row['message'] is None or row['text'] is not None)
            and permitted((row['text'] or '') + '\n' + row['quote'])]


def records(c, who, tracker, permitted, day=None):
    rows = c.execute('''SELECT id,goal,day,amount,outcome,status,created,version FROM tracker_entries
        WHERE owner=? AND tracker=? AND status IN ('pending','active') AND (? IS NULL OR day=?)
        ORDER BY day DESC,created DESC,rowid DESC''', (who, tracker['id'], day, day))
    result = []
    for row in rows:
        sources = source_rows(c, who, row['id'], tracker['bid'], permitted)
        if sources:
            result.append({**dict(row), 'sources': sources})
    return result


def write_checkin(c, who, tracker, body, stamp, permitted, message=None):
    if body.outcome == 'done' and body.amount is None:
        raise HTTPException(422, '请输入完成数量')
    note = body.note.strip()
    if not permitted(note):
        raise HTTPException(403, '请先在记忆台账开启相关敏感类别')
    if body.day > today(stamp):
        raise HTTPException(422, '请选择今天或更早的日期')
    goals = [goal for goal in goal_history(c, who, tracker['id'])
             if goal['starts'] <= stamp and today(goal['starts']) <= body.day]
    if not goals:
        raise HTTPException(422, '请选择创建跟踪项当天或之后的日期')
    day = body.day.isoformat()
    amount = body.amount if body.outcome == 'done' else None
    existing = c.execute('''SELECT e.tracker,e.day,e.amount,e.outcome,e.status,s.quote,s.message
        FROM tracker_entries e LEFT JOIN tracker_sources s ON s.owner=e.owner AND s.id=e.id
        WHERE e.owner=? AND e.id=?''', (who, body.id)).fetchone()
    if existing:
        if (existing['tracker'], existing['day'], existing['amount'], existing['outcome'], existing['quote'], existing['message']) == (
                tracker['id'], day, amount, body.outcome, note, message):
            return existing['status']
        raise HTTPException(409, '此打卡标识已使用')
    matches = records(c, who, tracker, permitted, day)
    status = 'pending' if any(r['status'] == 'active' and r['outcome'] == body.outcome for r in matches) else 'active'
    c.execute('''INSERT INTO tracker_entries(owner,id,tracker,goal,day,amount,outcome,status,created)
        VALUES(?,?,?,?,?,?,?,?,?)''', (who, body.id, tracker['id'], goals[-1]['id'], day, amount, body.outcome, status, stamp))
    c.execute('INSERT INTO tracker_sources VALUES(?,?,?,?,?,?)', (who, body.id, body.id, message, note, stamp))
    return status


def checkin_action(c, who, tracker, entry_id, body, permitted):
    entry = c.execute('SELECT * FROM tracker_entries WHERE owner=? AND tracker=? AND id=?',
                      (who, tracker['id'], entry_id)).fetchone()
    if not entry:
        raise HTTPException(404, '找不到这条记录')
    if (body.action, entry['status']) in (('revoke', 'revoked'), ('merge', 'merged'), ('separate', 'active')):
        return
    if entry['version'] != body.version or entry['status'] not in ('active', 'pending'):
        raise HTTPException(409, '记录已更新，请刷新')
    if not source_rows(c, who, entry_id, tracker['bid'], permitted):
        raise HTTPException(404, '找不到这条记录')
    status, amount = entry['status'], entry['amount']
    if body.action == 'revoke':
        status = 'revoked'
    elif body.action == 'edit':
        if entry['outcome'] != 'done' or body.amount is None:
            raise HTTPException(422, '请输入完成数量')
        amount = body.amount
    elif entry['status'] == 'pending':
        if body.action == 'merge':
            targets = records(c, who, tracker, permitted, entry['day'])
            target = next((r for r in targets if r['id'] == body.into and r['status'] == 'active'
                           and r['outcome'] == entry['outcome']), None)
            if not target:
                raise HTTPException(409, '这条已有记录已变更，请重新选择')
            if target['amount'] != amount:
                raise HTTPException(409, '两条记录的数量不同，请先修改数量再合并')
            c.execute('UPDATE tracker_sources SET entry=? WHERE owner=? AND entry=?', (body.into, who, entry_id))
            c.execute('UPDATE tracker_entries SET version=version+1 WHERE owner=? AND id=?', (who, body.into))
            status = 'merged'
        else:
            status = 'active'
    else:
        raise HTTPException(409, '这条记录已处理')
    c.execute('UPDATE tracker_entries SET status=?,amount=?,version=version+1 WHERE owner=? AND id=?',
              (status, amount, who, entry_id))


def tracker_view(c, who, tracker, stamp, permitted):
    goals = goal_history(c, who, tracker['id'])
    entries = records(c, who, tracker, permitted)
    end = today(stamp)
    start = end - timedelta(days=6)
    segments = []
    for index, goal in enumerate(goals):
        first = max(start, today(goal['starts']))
        last = end if index == len(goals) - 1 else today(goals[index + 1]['starts'])
        if first > last:
            continue
        selected = [entry for entry in entries if entry['goal'] == goal['id'] and entry['status'] == 'active'
                    and first.isoformat() <= entry['day'] <= last.isoformat()]
        # The day a goal changes may contain records for both versions.
        # Count its expected days only under the last version effective that day.
        expected_last = last if index == len(goals) - 1 else last - timedelta(days=1)
        expected = max(0, (expected_last - first).days + 1)
        known = {entry['day'] for entry in selected if entry['day'] <= expected_last.isoformat()}
        segments.append({**goal, 'fromDay': first.isoformat(), 'toDay': last.isoformat(),
                         'plannedDays': expected, 'unrecordedDays': expected - len(known),
                         'completed': sum(entry['amount'] for entry in selected if entry['outcome'] == 'done'),
                         'doneCount': sum(entry['outcome'] == 'done' for entry in selected),
                         'missedCount': sum(entry['outcome'] == 'missed' for entry in selected)})
    return {key: tracker[key] for key in ('id', 'name', 'unit', 'created')} | {
        'goal': goals[-1], 'segments': segments,
        'entries': [entry for entry in entries if entry['status'] == 'pending' or entry in entries[:30]],
    }


def change_candidates(memories, permitted):
    return [m for m in memories if m['status'] in ('active', 'superseded')
            and m['kind'] in ('goal', 'preference')
            and m['content'] in m['sourceMessage']['text']
            and permitted(m['content'] + '\n' + m['sourceMessage']['text'])][:16]


def record_day(when, stamp):
    current = today(stamp)
    if when in ('', '今天', '刚才', '刚刚'):
        return current
    if when in ('昨天', '前天'):
        return current - timedelta(days=1 if when == '昨天' else 2)
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', when):
            return date.fromisoformat(when)
        match = re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]', when)
        if match:
            year, month, day = match.groups()
            return date(int(year or current.year), int(month), int(day))
    except ValueError:
        pass
    return None


def exact_quote(value, text):
    if not isinstance(value, str):
        return None
    quote = value.strip('。！？；;\n ')
    if 3 <= len(quote) <= 280:
        boundary = r'[。！？!?；;，,\n]'
        span = rf'(?:^|{boundary})\s*{re.escape(quote)}\s*(?=$|[。！!；;，,\n])'
        if re.search(span, text):
            return quote
    return None


def record_proposals(c, who, bid, message, text, stamp, proposal, trackers, evidence, permitted):
    available = {item['id']: item for item in trackers}
    old = {item['id']: item for item in evidence}
    counts = {'recorded': 0, 'pending': 0, 'changes': 0}
    seen = set()
    for item in proposal['records'][:6]:
        if not isinstance(item, dict):
            continue
        tracker_id = item.get('tracker')
        tracker = available.get(tracker_id) if isinstance(tracker_id, str) else None
        quote = exact_quote(item.get('quote'), text)
        when = item.get('when')
        if not tracker or not quote or not isinstance(when, str) or (when and when not in text):
            continue
        if (tracker_id, quote) in seen:
            continue
        seen.add((tracker_id, quote))
        day = record_day(when, stamp)
        if day is None:
            continue
        amount = item.get('amount')
        outcome = item.get('outcome')
        if outcome not in ('done', 'missed') or (outcome == 'done' and type(amount) not in (int, float)):
            continue
        # An exact source span can support different trackers, but only once per tracker.
        try:
            checkin_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'{message}:{tracker_id}:{quote}'))
            body = CheckIn(id=checkin_id, day=day, outcome=outcome, amount=amount if outcome == 'done' else None, note=quote)
            status = write_checkin(c, who, tracker, body, stamp, permitted, message)
        except (ValueError, HTTPException):
            continue
        counts['pending' if status == 'pending' else 'recorded'] += 1
    for item in proposal['changes'][:4]:
        if not isinstance(item, dict):
            continue
        memory_id = item.get('before')
        previous = old.get(memory_id) if isinstance(memory_id, str) else None
        quote = exact_quote(item.get('quote'), text)
        if not previous or not quote or previous['content'].strip('。！？ ') == quote or not permitted(quote):
            continue
        inserted = c.execute('''INSERT OR IGNORE INTO cognitive_changes VALUES(?,?,?,?,?,?,?,?,'shown',?)''',
                             (who, str(uuid.uuid4()), bid, memory_id, previous['content'],
                              previous['version'], message, quote, stamp))
        counts['changes'] += inserted.rowcount
    return counts


def changes(c, who, bid, memories, permitted):
    old = {m['id']: m for m in memories if m['status'] in ('active', 'superseded', 'expired')}
    rows = c.execute('''SELECT a.*,m.text,m.created AS message_time FROM cognitive_changes a
        JOIN messages m ON m.owner=a.owner AND m.id=a.new_message AND m.bid=a.bid
        LEFT JOIN deleted_conversations d ON d.owner=m.owner AND d.cid=m.cid
        WHERE a.owner=? AND a.bid=? AND a.status='shown' AND d.cid IS NULL ORDER BY a.created DESC,a.rowid DESC''',
        (who, bid))
    result = []
    for row in rows:
        memory = old.get(row['old_memory'])
        if not memory or memory['content'] != row['old_content']:
            continue
        if memory['version'] != row['old_version'] and memory['status'] != 'superseded':
            continue
        original = memory['sourceMessage']
        if not permitted(original['text'] + '\n' + memory['content'] + '\n' + row['text']):
            continue
        if any(item['source'] == row['new_message'] and
               (item['status'] == 'rejected' or (item['version'] > 1 and item['status'] != 'superseded'))
               for item in memories):
            continue
        result.append({'id': row['id'],
                       'before': {'quote': memory['content'], 'text': original['text'], 'created': original['created']},
                       'after': {'quote': row['quote'], 'text': row['text'], 'created': row['message_time']}})
    return result
