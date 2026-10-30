# -*- coding: utf-8 -*-
"""短期记忆（进程内，按 session 隔离，2 小时过期）。"""

import time

_TTL = 2 * 3600
_store = {}


def get(sid):
    item = _store.get(sid)
    if not item:
        return {'goal': None, 'facts': [], 'last': None, 'updated': None}
    if time.time() - item['ts'] > _TTL:
        _store.pop(sid, None)
        return {'goal': None, 'facts': [], 'last': None, 'updated': None}
    return item['data']


def update(sid, goal=None, facts=None, last=None):
    data = get(sid)
    if goal:
        data['goal'] = goal
    if facts:
        for f in facts:
            f = (f or '').strip()
            if f and f not in data['facts']:
                data['facts'].append(f)
        data['facts'] = data['facts'][-12:]
    if last:
        data['last'] = last
    data['updated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    _store[sid] = {'ts': time.time(), 'data': data}
    return data


def reset(sid):
    _store.pop(sid, None)
