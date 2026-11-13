"""Exports and permanent erasure for one local workspace."""

import base64
import hashlib
import json
from collections import defaultdict

from fastapi import HTTPException

import planning


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_data_state(
    owner TEXT PRIMARY KEY,revision INTEGER NOT NULL,reset_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS erased_data(
    owner TEXT NOT NULL,kind TEXT NOT NULL,id TEXT NOT NULL,PRIMARY KEY(owner,kind,id)
);
CREATE TABLE IF NOT EXISTS blocked_memory_sources(
    owner TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(owner,source)
);
"""

WORKSPACE_TABLES = (
    'backfill_items', 'backfill_jobs', 'challenges', 'closed_challenge_scopes',
    'tracker_sources', 'tracker_entries', 'tracker_goals', 'trackers',
    'cognitive_changes', 'archive_portraits', 'archive_portrait_corrections',
    'followups', 'events', 'memories', 'reminders', 'decisions', 'capsules',
    'turns', 'messages', 'contexts', 'deleted_conversations', 'libraries', 'settings',
    'blocked_memory_sources', 'erased_data', 'workspace_data_state', 'workspace_started',
)


def revision(c, who):
    row = c.execute('SELECT revision FROM workspace_data_state WHERE owner=?', (who,)).fetchone()
    return row['revision'] if row else 0


def state(c, who):
    row = c.execute('SELECT revision,reset_count FROM workspace_data_state WHERE owner=?', (who,)).fetchone()
    session = c.execute('SELECT token FROM sessions WHERE owner=? ORDER BY token LIMIT 1', (who,)).fetchone()
    removed = defaultdict(list)
    for item in c.execute('SELECT kind,id FROM erased_data WHERE owner=? ORDER BY kind,id', (who,)):
        removed[item['kind']].append(item['id'])
    return {
        'workspace': who,
        'lineage': hashlib.sha256(session['token'].encode()).hexdigest(),
        'revision': row['revision'] if row else 0,
        'reset': row['reset_count'] if row else 0,
        'conversations': removed['conversation'],
        'messages': removed['message'],
        'replies': removed['reply'],
        'challenges': removed['challenge'],
        'memories': removed['memory'],
        'updatedSources': removed['updated_source'],
    }


def erased(c, who, kind, identifier):
    return c.execute('SELECT 1 FROM erased_data WHERE owner=? AND kind=? AND id=?',
                     (who, kind, identifier)).fetchone() is not None


def blocked(c, who, source):
    return c.execute('SELECT 1 FROM blocked_memory_sources WHERE owner=? AND source=?',
                     (who, source)).fetchone() is not None


def mark_erased(c, who, kind, identifiers):
    c.executemany('INSERT OR IGNORE INTO erased_data VALUES(?,?,?)', ((who, kind, item) for item in identifiers))


def advance(c, who):
    c.execute('''INSERT INTO workspace_data_state VALUES(?,1,0)
        ON CONFLICT(owner) DO UPDATE SET revision=revision+1''', (who,))


def require_workspace(c, who):
    if not c.execute('SELECT 1 FROM workspace_started WHERE owner=?', (who,)).fetchone():
        raise HTTPException(409, '数据空间已重置，请刷新页面')


def remove_by_ids(c, table, column, who, ids):
    if ids:
        c.execute(f'DELETE FROM {table} WHERE owner=? AND {column} IN (SELECT value FROM json_each(?))',
                  (who, json.dumps(sorted(ids))))


def remove_tracker_sources(c, who, sources):
    if not sources:
        return
    linked = c.execute('''SELECT id,entry FROM tracker_sources WHERE owner=?
        AND message IN (SELECT value FROM json_each(?))''', (who, json.dumps(sorted(sources)))).fetchall()
    remove_by_ids(c, 'tracker_sources', 'id', who, {row['id'] for row in linked})
    entries = {row[key] for row in linked for key in ('id', 'entry')}
    for entry in entries:
        if c.execute('SELECT 1 FROM tracker_sources WHERE owner=? AND entry=?', (who, entry)).fetchone():
            c.execute('UPDATE tracker_entries SET version=version+1 WHERE owner=? AND id=?', (who, entry))
        else:
            c.execute('DELETE FROM tracker_entries WHERE owner=? AND id=?', (who, entry))


def remove_backfill_sources(c, who, sources):
    if not sources:
        return
    identifiers = json.dumps(sorted(sources))
    counts = c.execute('''SELECT job,COUNT(*) AS count FROM backfill_items WHERE owner=?
        AND source IN (SELECT value FROM json_each(?)) GROUP BY job''', (who, identifiers)).fetchall()
    remove_by_ids(c, 'backfill_items', 'source', who, sources)
    c.executemany('''UPDATE backfill_jobs SET total=total-? WHERE owner=? AND id=?
        AND status IN ('queued','running')''', ((row['count'], who, row['job']) for row in counts))


def redact_replies(c, who, sources, blocked_sources, memories, cutoffs):
    # Prior versions stored citations, while the model also saw uncited context.
    # For old replies, use the earliest affected timestamp in the same library.
    if not cutoffs:
        return
    removed_history = sources | blocked_sources
    redacted = set(sources)
    retained = set()
    for row in c.execute('''SELECT t.id,t.result,m.bid,m.created FROM turns t
        JOIN messages m ON m.owner=t.owner AND m.id=t.id WHERE t.owner=?
        ORDER BY m.rowid''', (who,)).fetchall():
        if row['id'] in sources or row['bid'] not in cutoffs:
            continue
        result = json.loads(row['result'])
        if result.get('redacted') or result.get('extraction') in ('reminder', 'search_only'):
            continue
        inputs = result.get('input_sources')
        if inputs is None:
            affected = row['created'] >= cutoffs[row['bid']]
        else:
            affected = (
                bool(removed_history & set(inputs['messages']))
                or bool(redacted & set(inputs['replies']))
                or bool(memories & set(inputs['memories']))
            )
        if affected:
            redacted.add(row['id'])
            retained.add(row['id'])
            stub = {'redacted': True, 'extraction': result['extraction'], 'timestamp': result['timestamp']}
            c.execute('UPDATE turns SET result=? WHERE owner=? AND id=?',
                      (json.dumps(stub), who, row['id']))
    mark_erased(c, who, 'reply', retained)


def erase_records(c, who, source_rows, memory_rows, blocked_sources=(), conversation=None):
    sources = {row['id'] for row in source_rows}
    memories = {row['id'] for row in memory_rows}
    blocked_sources = set(blocked_sources)
    cutoffs = {}
    for row in [*source_rows, *memory_rows]:
        cutoffs[row['bid']] = min(cutoffs.get(row['bid'], row['created']), row['created'])
    if blocked_sources:
        c.executemany('INSERT OR IGNORE INTO blocked_memory_sources VALUES(?,?)',
                      ((who, source) for source in blocked_sources))
        for row in c.execute('''SELECT bid,created FROM messages WHERE owner=?
            AND id IN (SELECT value FROM json_each(?))''', (who, json.dumps(sorted(blocked_sources)))):
            cutoffs[row['bid']] = min(cutoffs.get(row['bid'], row['created']), row['created'])

    challenge_ids = {row['id'] for row in c.execute('''SELECT id FROM challenges WHERE owner=? AND (
        cid=? OR source IN (SELECT value FROM json_each(?)) OR memory IN (SELECT value FROM json_each(?))
        OR EXISTS (SELECT 1 FROM json_each(challenges.goals) WHERE key IN (SELECT value FROM json_each(?))))''',
        (who, conversation, json.dumps(sorted(sources)), json.dumps(sorted(memories)), json.dumps(sorted(memories))))}
    remove_by_ids(c, 'challenges', 'id', who, challenge_ids)
    mark_erased(c, who, 'challenge', challenge_ids)
    remove_by_ids(c, 'followups', 'memory', who, memories)
    remove_by_ids(c, 'events', 'memory', who, memories)
    remove_by_ids(c, 'cognitive_changes', 'old_memory', who, memories)
    remove_by_ids(c, 'cognitive_changes', 'new_message', who, sources)
    remove_by_ids(c, 'reminders', 'id', who, sources)
    remove_by_ids(c, 'decisions', 'source', who, sources)
    remove_tracker_sources(c, who, sources)
    remove_backfill_sources(c, who, sources | blocked_sources)
    for bid in cutoffs:
        c.execute('DELETE FROM archive_portraits WHERE owner=? AND bid=?', (who, bid))
    c.execute('''DELETE FROM archive_portrait_corrections WHERE owner=? AND EXISTS (
        SELECT 1 FROM json_each(proofs) WHERE value IN (SELECT value FROM json_each(?)))''',
              (who, json.dumps(sorted(memories))))
    redact_replies(c, who, sources, blocked_sources, memories, cutoffs)
    remove_by_ids(c, 'memories', 'id', who, memories)
    remove_by_ids(c, 'blocked_memory_sources', 'source', who, sources)
    remove_by_ids(c, 'turns', 'id', who, sources)
    remove_by_ids(c, 'messages', 'id', who, sources)
    mark_erased(c, who, 'memory', memories)
    mark_erased(c, who, 'message', sources)
    mark_erased(c, who, 'updated_source', {row['source'] for row in memory_rows} - sources)
    if conversation:
        for table in ('contexts', 'closed_challenge_scopes', 'deleted_conversations'):
            c.execute(f'DELETE FROM {table} WHERE owner=? AND cid=?', (who, conversation))
        mark_erased(c, who, 'conversation', [conversation])
    advance(c, who)


def erase_memory(c, who, identifier, version, with_source, block_source):
    row = c.execute('SELECT * FROM memories WHERE owner=? AND id=?', (who, identifier)).fetchone()
    if not row:
        if erased(c, who, 'memory', identifier):
            return
        raise HTTPException(404, '找不到这条记忆')
    planning.check_version(row, version)
    if with_source:
        sources = c.execute('SELECT * FROM messages WHERE owner=? AND id=? AND bid=?',
                            (who, row['source'], row['bid'])).fetchall()
        related = c.execute('SELECT * FROM memories WHERE owner=? AND source=? AND bid=?',
                            (who, row['source'], row['bid'])).fetchall()
        erase_records(c, who, sources, related)
    else:
        erase_records(c, who, [], [row], [row['source']] if block_source else [])


def erase_conversation(c, who, identifier):
    if erased(c, who, 'conversation', identifier):
        return
    if not c.execute('SELECT 1 FROM deleted_conversations WHERE owner=? AND cid=?', (who, identifier)).fetchone():
        raise HTTPException(409, '请先将对话移至最近删除')
    sources = c.execute('SELECT * FROM messages WHERE owner=? AND cid=?', (who, identifier)).fetchall()
    memories = c.execute('''SELECT r.* FROM memories r JOIN messages m ON m.owner=r.owner AND m.id=r.source
        WHERE r.owner=? AND m.cid=?''', (who, identifier)).fetchall()
    erase_records(c, who, sources, memories, conversation=identifier)


def reset_workspace(c, who, new_owner, stamp):
    require_workspace(c, who)
    reset_count = state(c, who)['reset'] + 1
    for table in WORKSPACE_TABLES:
        c.execute(f'DELETE FROM {table} WHERE owner=?', (who,))
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_templates'").fetchone():
        c.execute('DELETE FROM library_templates WHERE owner=?', (who,))
    c.execute('UPDATE sessions SET owner=? WHERE owner=?', (new_owner, who))
    c.execute('INSERT INTO workspace_started VALUES(?,?)', (new_owner, stamp))
    c.execute('INSERT INTO workspace_data_state VALUES(?,0,?)', (new_owner, reset_count))


def rows(c, query, params):
    return [{key: row[key] for key in row.keys() if key != 'owner'} for row in c.execute(query, params)]


def export_library(c, who, bid, stamp, timeline, portrait):
    scope = (who, bid)
    library = c.execute('SELECT id,name FROM libraries WHERE owner=? AND id=?', scope).fetchone()
    messages = rows(c, '''SELECT m.id,m.cid,m.sid,m.text,m.created,d.cid IS NOT NULL AS trashed,t.result
        FROM messages m LEFT JOIN turns t ON t.owner=m.owner AND t.id=m.id
        LEFT JOIN deleted_conversations d ON d.owner=m.owner AND d.cid=m.cid
        WHERE m.owner=? AND m.bid IS ? ORDER BY m.created,m.rowid''', scope)
    for message in messages:
        result = json.loads(message.pop('result') or '{}')
        if result.get('text'):
            message['reply'] = {key: result[key] for key in (
                'text', 'behavior', 'provider', 'model', 'citations', 'timestamp', 'webSources', 'capsuleRefs'
            ) if key in result}
    trackers = rows(c, 'SELECT * FROM trackers WHERE owner=? AND bid=? ORDER BY created,id', scope)
    for tracker in trackers:
        key = (who, tracker['id'])
        tracker['goals'] = rows(c, 'SELECT * FROM tracker_goals WHERE owner=? AND tracker=? ORDER BY starts,id', key)
        tracker['entries'] = rows(c, 'SELECT * FROM tracker_entries WHERE owner=? AND tracker=? ORDER BY day,created,id', key)
        for entry in tracker['entries']:
            entry['sources'] = rows(c, 'SELECT * FROM tracker_sources WHERE owner=? AND entry=? ORDER BY created,id',
                                    (who, entry['id']))
    capsules = planning.current_capsules(c, who, bid) if bid else []
    for capsule in capsules:
        capsule['versions'] = planning.capsule_versions(c, who, bid, capsule['id'])
        capsule['attachments'] = [
            {'id': row['digest'], 'name': row['name'], 'mime': row['mime'], 'base64': base64.b64encode(row['data']).decode('ascii')}
            for row in c.execute('SELECT digest,name,mime,data FROM capsule_images WHERE owner=? AND bid=? AND id=? ORDER BY digest',
                                 (who, bid, capsule['id']))
        ]
    memories = rows(c, 'SELECT * FROM memories WHERE owner=? AND bid=? ORDER BY created,id', scope)
    for memory in memories:
        memory['history'] = rows(c, 'SELECT action,before,after,created FROM events WHERE owner=? AND memory=? ORDER BY created,rowid',
                                 (who, memory['id']))
        for event in memory['history']:
            for key in ('before', 'after'):
                if event[key]:
                    event[key] = json.loads(event[key])
                    event[key].pop('owner', None)
        reminder = c.execute('SELECT allowed,due FROM followups WHERE owner=? AND memory=?', (who, memory['id'])).fetchone()
        if reminder:
            memory['followup'] = dict(reminder)
    return {
        'format': 'qiyu-export-v1', 'exportedAt': stamp,
        'library': dict(library) if library else {'id': None, 'name': '未绑定对话'},
        'messages': messages, 'memories': memories, 'timeline': timeline, 'portrait': portrait,
        'trackers': trackers, 'capsules': capsules,
        'decisions': rows(c, 'SELECT * FROM decisions WHERE owner=? AND bid=? ORDER BY created,id', scope),
        'changes': rows(c, 'SELECT * FROM cognitive_changes WHERE owner=? AND bid=? ORDER BY created,id', scope),
        'reminders': rows(c, '''SELECT r.id,r.content,r.due,r.status FROM reminders r
            JOIN messages m ON m.owner=r.owner AND m.id=r.id WHERE r.owner=? AND m.bid IS ? ORDER BY r.due,r.id''', scope),
    }
