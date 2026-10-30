# -*- coding: utf-8 -*-
"""SQLite 存储：用户、简历版本库、扣分点采纳记录、用户画像。"""

import json
import os
import sqlite3
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'data')
DB_PATH = os.path.join(DATA_DIR, 'doctor.db')

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    profile TEXT
);
CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    label TEXT,
    filename TEXT,
    jd_title TEXT,
    total INTEGER,
    dims TEXT,
    deductions TEXT,
    rewrites TEXT,
    guard TEXT,
    metrics TEXT,
    jd_struct TEXT,
    resume_text TEXT,
    jd_text TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_user ON versions(user_id, seq);
CREATE TABLE IF NOT EXISTS adoptions (
    version_id INTEGER NOT NULL,
    item_key TEXT NOT NULL,
    adopted INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT,
    PRIMARY KEY (version_id, item_key)
);
CREATE TABLE IF NOT EXISTS hr_scores (
    version_id INTEGER PRIMARY KEY,
    user_id TEXT,
    total REAL NOT NULL,
    dims TEXT,
    reviewer TEXT,
    note TEXT,
    created_at TEXT NOT NULL
);
"""


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def init():
    with _conn() as c:
        c.executescript(SCHEMA)
        cols = {r['name'] for r in c.execute('PRAGMA table_info(versions)').fetchall()}
        if 'extra' not in cols:
            c.execute('ALTER TABLE versions ADD COLUMN extra TEXT')


def now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def ensure_user(user_id):
    if not user_id:
        user_id = 'u_' + uuid.uuid4().hex[:12]
    with _conn() as c:
        row = c.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone()
        if not row:
            c.execute('INSERT INTO users(id, created_at, profile) VALUES(?,?,?)',
                      (user_id, now(), json.dumps({}, ensure_ascii=False)))
    return user_id


def add_version(user_id, *, label, filename, jd_title, total, dims, deductions,
                rewrites, guard, metrics, jd_struct, resume_text, jd_text, extra=None):
    with _conn() as c:
        row = c.execute('SELECT COALESCE(MAX(seq),0) AS m FROM versions WHERE user_id=?',
                        (user_id,)).fetchone()
        seq = int(row['m']) + 1
        cur = c.execute(
            'INSERT INTO versions(user_id,seq,label,filename,jd_title,total,dims,deductions,'
            'rewrites,guard,metrics,jd_struct,resume_text,jd_text,created_at,extra) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (user_id, seq, label, filename, jd_title, total,
             json.dumps(dims, ensure_ascii=False),
             json.dumps(deductions, ensure_ascii=False),
             json.dumps(rewrites, ensure_ascii=False),
             json.dumps(guard, ensure_ascii=False),
             json.dumps(metrics, ensure_ascii=False),
             json.dumps(jd_struct, ensure_ascii=False),
             resume_text, jd_text, now(),
             json.dumps(extra or {}, ensure_ascii=False)))
        return int(cur.lastrowid), seq


def _row_to_version(r, with_text=False):
    keys = r.keys()
    extra = {}
    if 'extra' in keys and r['extra']:
        try:
            extra = json.loads(r['extra'])
        except Exception:
            extra = {}
    d = {
        'id': r['id'], 'user_id': r['user_id'], 'seq': r['seq'], 'label': r['label'],
        'filename': r['filename'],
        'jd_title': r['jd_title'], 'total': r['total'],
        'dims': json.loads(r['dims'] or '{}'),
        'deductions': json.loads(r['deductions'] or '[]'),
        'rewrites': json.loads(r['rewrites'] or '[]'),
        'guard': json.loads(r['guard'] or '{}'),
        'metrics': json.loads(r['metrics'] or '{}'),
        'jd_struct': json.loads(r['jd_struct'] or '{}'),
        'created_at': r['created_at'],
        'one_liner': extra.get('one_liner', ''),
        'summary': extra.get('summary', ''),
        'jd_fit': extra.get('jd_fit', {'level': '基本匹配', 'comment': ''}),
        'interview_risks': extra.get('interview_risks', []),
        'next_actions': extra.get('next_actions', []),
    }
    if with_text:
        d['resume_text'] = r['resume_text']
        d['jd_text'] = r['jd_text']
    return d


def get_version(vid, with_text=False):
    with _conn() as c:
        r = c.execute('SELECT * FROM versions WHERE id=?', (vid,)).fetchone()
    return _row_to_version(r, with_text) if r else None


def prev_version(user_id, before_seq):
    with _conn() as c:
        r = c.execute('SELECT * FROM versions WHERE user_id=? AND seq<? ORDER BY seq DESC LIMIT 1',
                      (user_id, before_seq)).fetchone()
    return _row_to_version(r) if r else None


def list_versions(user_id, limit=30):
    with _conn() as c:
        rows = c.execute('SELECT * FROM versions WHERE user_id=? ORDER BY seq DESC LIMIT ?',
                         (user_id, limit)).fetchall()
    return [_row_to_version(r) for r in rows]


def set_adoption(version_id, item_key, adopted):
    with _conn() as c:
        c.execute('INSERT INTO adoptions(version_id,item_key,adopted,updated_at) VALUES(?,?,?,?) '
                  'ON CONFLICT(version_id,item_key) DO UPDATE SET adopted=excluded.adopted, '
                  'updated_at=excluded.updated_at',
                  (version_id, item_key, 1 if adopted else 0, now()))


def adoptions(version_id):
    with _conn() as c:
        rows = c.execute('SELECT item_key, adopted FROM adoptions WHERE version_id=?',
                         (version_id,)).fetchall()
    return {r['item_key']: bool(r['adopted']) for r in rows}


def adoption_rate(user_id):
    with _conn() as c:
        r = c.execute(
            'SELECT COUNT(*) AS total, SUM(a.adopted) AS yes FROM adoptions a '
            'JOIN versions v ON v.id=a.version_id WHERE v.user_id=?', (user_id,)).fetchone()
    total = int(r['total'] or 0)
    yes = int(r['yes'] or 0)
    return {'total': total, 'adopted': yes,
            'rate': round(yes / total, 3) if total else None}


def set_hr_score(version_id, total, dims=None, reviewer='', note=''):
    """录入/覆盖人工评分（老师或 HR 打的分数）。"""
    v = get_version(version_id)
    if not v:
        raise ValueError('版本不存在')
    with _conn() as c:
        c.execute(
            'INSERT INTO hr_scores(version_id,user_id,total,dims,reviewer,note,created_at) '
            'VALUES(?,?,?,?,?,?,?) ON CONFLICT(version_id) DO UPDATE SET '
            'total=excluded.total, dims=excluded.dims, reviewer=excluded.reviewer, '
            'note=excluded.note, created_at=excluded.created_at',
            (version_id, v['user_id'], float(total),
             json.dumps(dims or {}, ensure_ascii=False),
             reviewer or '', note or '', now()))
    return get_hr_score(version_id)


def get_hr_score(version_id):
    with _conn() as c:
        r = c.execute('SELECT * FROM hr_scores WHERE version_id=?', (version_id,)).fetchone()
    if not r:
        return None
    try:
        dims = json.loads(r['dims'] or '{}')
    except Exception:
        dims = {}
    return {'version_id': r['version_id'], 'user_id': r['user_id'], 'total': r['total'],
            'dims': dims, 'reviewer': r['reviewer'], 'note': r['note'],
            'created_at': r['created_at']}


def del_hr_score(version_id):
    with _conn() as c:
        c.execute('DELETE FROM hr_scores WHERE version_id=?', (version_id,))


def get_profile(user_id):
    with _conn() as c:
        r = c.execute('SELECT profile FROM users WHERE id=?', (user_id,)).fetchone()
    if not r:
        return {}
    try:
        return json.loads(r['profile'] or '{}')
    except Exception:
        return {}


def save_profile(user_id, profile):
    with _conn() as c:
        c.execute('UPDATE users SET profile=? WHERE id=?',
                  (json.dumps(profile, ensure_ascii=False), user_id))
