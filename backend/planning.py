"""Library-scoped decisions and capsule versions."""

import base64
import binascii
import hashlib
import re
import uuid
from datetime import date, timedelta
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

import archive


SCHEMA = '''
CREATE TABLE IF NOT EXISTS decisions(
    owner TEXT NOT NULL,bid TEXT NOT NULL,id TEXT NOT NULL,source TEXT,
    title TEXT NOT NULL,choice TEXT NOT NULL,options TEXT NOT NULL,reason TEXT NOT NULL,
    expectation TEXT NOT NULL,due TEXT,result TEXT NOT NULL,result_at INTEGER,review TEXT,
    draft INTEGER NOT NULL,created INTEGER NOT NULL,version INTEGER NOT NULL,
    PRIMARY KEY(owner,bid,id),UNIQUE(owner,bid,source)
);
CREATE TABLE IF NOT EXISTS capsules(
    owner TEXT NOT NULL,bid TEXT NOT NULL,id TEXT NOT NULL,kind TEXT NOT NULL,created INTEGER NOT NULL,
    version INTEGER NOT NULL,seen INTEGER NOT NULL DEFAULT 0,snooze TEXT,
    PRIMARY KEY(owner,bid,id)
);
CREATE TABLE IF NOT EXISTS capsule_versions(
    owner TEXT NOT NULL,bid TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
    title TEXT NOT NULL,body TEXT NOT NULL,due TEXT NOT NULL,sealed INTEGER NOT NULL,
    reminder INTEGER NOT NULL,saved INTEGER NOT NULL,PRIMARY KEY(owner,bid,id,version),
    FOREIGN KEY(owner,bid,id) REFERENCES capsules(owner,bid,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS capsule_images(
    owner TEXT NOT NULL,bid TEXT NOT NULL,id TEXT NOT NULL,digest TEXT NOT NULL,
    name TEXT NOT NULL,mime TEXT NOT NULL,data BLOB NOT NULL,PRIMARY KEY(owner,bid,id,digest),
    FOREIGN KEY(owner,bid,id) REFERENCES capsules(owner,bid,id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS capsule_version_images(
    owner TEXT NOT NULL,bid TEXT NOT NULL,id TEXT NOT NULL,version INTEGER NOT NULL,
    position INTEGER NOT NULL,digest TEXT NOT NULL,PRIMARY KEY(owner,bid,id,version,position),
    FOREIGN KEY(owner,bid,id,version) REFERENCES capsule_versions(owner,bid,id,version) ON DELETE CASCADE,
    FOREIGN KEY(owner,bid,id,digest) REFERENCES capsule_images(owner,bid,id,digest) ON DELETE CASCADE
);
'''

MAX_CAPSULE_IMAGE_BYTES = 3 * 1024 * 1024
MAX_CAPSULE_IMAGES = 4


class DecisionInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    version: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=100)
    choice: str = Field(min_length=1, max_length=500)
    options: str = Field(default='', max_length=1000)
    reason: str = Field(default='', max_length=1000)
    expectation: str = Field(default='', max_length=1000)
    due: date | None = None
    result: str = Field(default='', max_length=1500)


class VersionInput(BaseModel):
    version: int = Field(ge=1)


class CapsuleImageInput(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    data: str


class CapsuleInput(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    version: int = Field(default=0, ge=0)
    kind: Literal['letter', 'snapshot']
    title: str = Field(min_length=1, max_length=100)
    body: str = Field(default='', max_length=4000)
    due: date
    sealed: bool = True
    reminder: bool = True
    images: list[str | CapsuleImageInput] | None = Field(default=None, max_length=MAX_CAPSULE_IMAGES)


class CapsuleAction(VersionInput):
    action: Literal['seen', 'snooze', 'disable']


def clean_required(value):
    if not value.strip():
        raise HTTPException(422, '请填写必填内容')
    return value.strip()


def check_version(row, version):
    if (row['version'] if row else 0) != version:
        raise HTTPException(409, '记录已更新，请刷新后重试')


def decisions(c, who, bid):
    return [dict(row) for row in c.execute('''SELECT d.id,d.title,d.choice,d.options,d.reason,d.expectation,d.due,
        d.result,d.result_at,d.review,d.draft,d.created,d.version,m.text AS original,m.cid AS conversation
        FROM decisions d LEFT JOIN messages m ON m.owner=d.owner AND m.bid=d.bid AND m.id=d.source
        WHERE d.owner=? AND d.bid=? AND (d.source IS NULL OR (m.id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM deleted_conversations x WHERE x.owner=d.owner AND x.cid=m.cid)))
        ORDER BY d.created DESC,d.rowid DESC''', (who, bid))]


def decision(c, who, bid, identifier):
    row = next((item for item in decisions(c, who, bid) if item['id'] == identifier), None)
    if not row:
        raise HTTPException(404, '没有找到这条决定')
    return row


def save_decision(c, who, bid, body, stamp):
    prior = c.execute('SELECT * FROM decisions WHERE owner=? AND bid=? AND id=?', (who, bid, body.id)).fetchone()
    if prior:
        decision(c, who, bid, body.id)
    values = (clean_required(body.title), clean_required(body.choice), body.options.strip(), body.reason.strip(),
              body.expectation.strip(), body.due.isoformat() if body.due else None, body.result.strip())
    fields = ('title', 'choice', 'options', 'reason', 'expectation', 'due', 'result')
    if prior and not prior['draft'] and values == tuple(prior[field] for field in fields):
        return
    check_version(prior, body.version)
    result_at = (prior['result_at'] if prior and prior['result'] == values[-1] else stamp) if values[-1] else None
    if prior:
        c.execute('''UPDATE decisions SET title=?,choice=?,options=?,reason=?,expectation=?,due=?,result=?,result_at=?,
            review=NULL,draft=0,version=version+1 WHERE owner=? AND bid=? AND id=?''', (*values, result_at, who, bid, body.id))
    else:
        c.execute('INSERT INTO decisions VALUES(?,?,?,NULL,?,?,?,?,?,?,?,?,NULL,0,?,1)',
                  (who, bid, body.id, *values, result_at, stamp))


def draft_from_chat(text):
    for match in re.finditer(r'([^。！？!?；;\n]+)([。！？!?；;\n]?)', text):
        sentence = match[1].strip()
        if match[2] in ('？', '?'):
            continue
        if re.match(r'^我(?:已经|最终|刚刚)?决定(?:了)?[：:，, ]*\S', sentence) and 6 <= len(sentence) <= 500:
            if not re.search(r'假如|如果|假设|比如|举例|要不要|是否|可能|不确定|还没|怎么选|吗$|么$', sentence):
                return sentence
    return None


def save_draft(c, who, bid, source, quote, stamp):
    identifier = str(uuid.uuid4())
    c.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?,'','','',NULL,'',NULL,NULL,1,?,1)",
              (who, bid, identifier, source, quote[:100], quote, stamp))
    return identifier


def current_capsules(c, who, bid):
    return [dict(row) for row in c.execute('''SELECT v.id,v.title,v.body,v.due,v.sealed,v.reminder,v.saved,v.version,
        s.kind,s.created,s.seen,s.snooze FROM capsules s JOIN capsule_versions v
        ON v.owner=s.owner AND v.bid=s.bid AND v.id=s.id AND v.version=s.version
        WHERE s.owner=? AND s.bid=? ORDER BY v.saved DESC,v.rowid DESC''', (who, bid))]


def capsule(c, who, bid, identifier):
    row = next((item for item in current_capsules(c, who, bid) if item['id'] == identifier), None)
    if not row:
        raise HTTPException(404, '没有找到这个时间胶囊')
    return row


def capsule_versions(c, who, bid, identifier):
    images = {}
    for row in c.execute('''SELECT v.version,i.digest,i.name FROM capsule_version_images v JOIN capsule_images i
        ON i.owner=v.owner AND i.bid=v.bid AND i.id=v.id AND i.digest=v.digest
        WHERE v.owner=? AND v.bid=? AND v.id=? ORDER BY v.version,v.position''', (who, bid, identifier)):
        images.setdefault(row['version'], []).append({'id': row['digest'], 'name': row['name']})
    return [{**dict(row), 'images': images.get(row['version'], [])} for row in c.execute('''
        SELECT version,title,body,due,sealed,reminder,saved FROM capsule_versions
        WHERE owner=? AND bid=? AND id=? ORDER BY version DESC''', (who, bid, identifier))]


def decode_capsule_image(image):
    if len(image.data) > 4 * ((MAX_CAPSULE_IMAGE_BYTES + 2) // 3):
        raise HTTPException(422, '单张图片不能超过 3 MB')
    try:
        data = base64.b64decode(image.data, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, '图片数据无法读取') from None
    if len(data) > MAX_CAPSULE_IMAGE_BYTES:
        raise HTTPException(422, '单张图片不能超过 3 MB')
    if len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR'):
        mime = 'image/png'
    elif data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):
        mime = 'image/jpeg'
    elif len(data) >= 20 and data[:4] == b'RIFF' and data[8:16] in (b'WEBPVP8 ', b'WEBPVP8L', b'WEBPVP8X'):
        mime = 'image/webp'
    else:
        raise HTTPException(422, '请选择 JPG、PNG 或 WebP 图片')
    name = re.split(r'[/\\]', image.name)[-1].strip() or '图片'
    return hashlib.sha256(data).hexdigest(), name, mime, data


def capsule_image_selection(c, scope, requested, previous):
    if requested is None:
        return previous, []
    existing = {row['digest'] for row in c.execute('SELECT digest FROM capsule_images WHERE owner=? AND bid=? AND id=?', scope)}
    selected, uploads = [], []
    for item in requested:
        if isinstance(item, str):
            if item not in existing:
                raise HTTPException(422, '找不到这张胶囊图片')
            digest = item
        else:
            upload = decode_capsule_image(item)
            digest = upload[0]
            if digest not in existing:
                uploads.append(upload)
                existing.add(digest)
        if digest not in selected:
            selected.append(digest)
    return selected, uploads


def capsule_metadata(row):
    return {k: v for k, v in row.items() if k != 'body' or not row['sealed']}


def capsule_due(row, day):
    return row['reminder'] and not row['seen'] and max(row['due'], row['snooze'] or '') <= day


def save_capsule(c, who, bid, body, stamp):
    prior = next((item for item in current_capsules(c, who, bid) if item['id'] == body.id), None)
    scope = (who, bid, body.id)
    previous_images = [row['digest'] for row in c.execute('''SELECT digest FROM capsule_version_images
        WHERE owner=? AND bid=? AND id=? AND version=? ORDER BY position''', (*scope, prior['version']))] if prior else []
    images, uploads = capsule_image_selection(c, scope, body.images, previous_images)
    values = (clean_required(body.title), body.body.strip(), body.due.isoformat(), int(body.sealed), int(body.reminder))
    if not values[1] and not images:
        raise HTTPException(422, '请写点内容或添加一张图片')
    fields = ('title', 'body', 'due', 'sealed', 'reminder')
    if prior and prior['kind'] != body.kind:
        raise HTTPException(422, '已保存的胶囊类型不能更改')
    if prior and values == tuple(prior[field] for field in fields) and images == previous_images:
        return
    check_version(prior, body.version)
    day_changed = not prior or values[2] != prior['due']
    if day_changed and body.due < archive.today(stamp):
        raise HTTPException(422, '请选择今天或之后的日期')
    version = body.version + 1
    if prior:
        restart = day_changed or (body.reminder and not prior['reminder'])
        c.execute('UPDATE capsules SET version=?,seen=?,snooze=? WHERE owner=? AND bid=? AND id=?',
                  (version, 0 if restart else prior['seen'], None if restart else prior['snooze'], who, bid, body.id))
    else:
        c.execute('INSERT INTO capsules(owner,bid,id,kind,created,version) VALUES(?,?,?,?,?,?)',
                  (who, bid, body.id, body.kind, stamp, version))
    c.execute('INSERT INTO capsule_versions VALUES(?,?,?,?,?,?,?,?,?,?)', (*scope, version, *values, stamp))
    c.executemany('INSERT INTO capsule_images VALUES(?,?,?,?,?,?,?)', ((*scope, *upload) for upload in uploads))
    c.executemany('INSERT INTO capsule_version_images VALUES(?,?,?,?,?,?)',
                  ((*scope, version, index, digest) for index, digest in enumerate(images)))


def act_on_capsule(c, who, bid, identifier, body, stamp):
    row = capsule(c, who, bid, identifier)
    check_version(row, body.version)
    if body.action == 'disable':
        save_capsule(c, who, bid, CapsuleInput(**{**row, 'reminder': False}), stamp)
        return
    if row['seen'] and body.action == 'seen':
        return
    day = archive.today(stamp)
    if not capsule_due(row, day.isoformat()):
        raise HTTPException(409, '这封胶囊当前没有到期提醒')
    c.execute('UPDATE capsules SET seen=?,snooze=? WHERE owner=? AND bid=? AND id=?',
              (int(body.action == 'seen'), (day + timedelta(days=1)).isoformat() if body.action == 'snooze' else None,
               who, bid, identifier))


def capsule_evidence(rows, permitted, query):
    candidates = [row for row in rows if row['body'] and permitted(row['title'] + '\n' + row['body'])]
    named = [row for row in candidates if len(row['title']) >= 2 and row['title'] in query]
    if not named and not re.search(r'胶囊|未来信|当下快照', query):
        return []
    return [{key: row[key] for key in ('id', 'kind', 'title', 'body', 'version', 'saved')}
            for row in (named or candidates)[:4]]


