# -*- coding: utf-8 -*-
"""DeepSeek（OpenAI 兼容）客户端，纯标准库。"""

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULTS = {
    'api_key': '',
    'base_url': 'https://api.deepseek.com',
    'model': 'deepseek-chat',
    'temperature': 0.3,
    'timeout': 180,
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LLMError(Exception):
    pass


_usage = {'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}


def usage():
    return dict(_usage)


def reset_usage():
    _usage.update({'calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0})


def load_config():
    cfg = dict(DEFAULTS)
    path = os.path.join(ROOT, 'config.json')
    if os.path.exists(path):
        raw = None
        # 兼容：记事本可能存成 UTF-8 BOM / ANSI(GBK) / UTF-16
        for enc in ('utf-8-sig', 'gb18030', 'utf-16'):
            try:
                with open(path, 'r', encoding=enc) as f:
                    raw = f.read()
                break
            except (UnicodeDecodeError, UnicodeError):
                continue
        try:
            cfg.update({k: v for k, v in json.loads(raw or '{}').items() if v not in (None, '')})
        except Exception:
            pass
    cfg['api_key'] = (os.environ.get('DEEPSEEK_API_KEY') or cfg['api_key'] or '').strip()
    cfg['base_url'] = (os.environ.get('DEEPSEEK_BASE_URL') or cfg['base_url']).rstrip('/')
    cfg['model'] = os.environ.get('DEEPSEEK_MODEL') or cfg['model']
    return cfg


def _post(cfg, payload):
    url = cfg['base_url'] + '/chat/completions'
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST', headers={
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + cfg['api_key'],
        'Accept': 'application/json',
    })
    with urllib.request.urlopen(req, timeout=cfg['timeout']) as r:
        return json.loads(r.read().decode('utf-8'))


def chat(messages, json_mode=False, temperature=None, max_tokens=4096, model=None):
    cfg = load_config()
    if not cfg['api_key']:
        raise LLMError('未配置 API Key：请在 config.json 或环境变量 DEEPSEEK_API_KEY 中设置')
    payload = {
        'model': model or cfg['model'],
        'messages': messages,
        'temperature': cfg['temperature'] if temperature is None else temperature,
        'max_tokens': max_tokens,
        'stream': False,
    }
    if json_mode:
        payload['response_format'] = {'type': 'json_object'}
    last_err = None
    for attempt in range(3):
        try:
            resp = _post(cfg, payload)
            u = resp.get('usage') or {}
            _usage['calls'] += 1
            _usage['prompt_tokens'] += u.get('prompt_tokens', 0) or 0
            _usage['completion_tokens'] += u.get('completion_tokens', 0) or 0
            return resp['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', 'ignore')[:300]
            except Exception:
                pass
            last_err = 'HTTP %s %s' % (e.code, body)
            if e.code in (400, 401, 402, 403, 404):
                break
            time.sleep(1.5 * (attempt + 1))
        except Exception as e:
            last_err = '%s: %s' % (type(e).__name__, e)
            time.sleep(1.5 * (attempt + 1))
    raise LLMError(last_err or '调用失败')


def _strip_fence(text):
    t = (text or '').strip()
    if t.startswith('```'):
        t = re.sub(r'^```[a-zA-Z]*\s*', '', t)
        t = re.sub(r'\s*```$', '', t)
    return t.strip()


def _repair_json(t):
    t = t.strip()
    if t.startswith('{') and not t.endswith('}'):
        # 尝试补齐截断的 JSON
        depth = 0
        in_str = False
        esc = False
        for ch in t:
            if in_str:
                if esc:
                    esc = False
                elif ch == '\\':
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in '{[':
                    depth += 1
                elif ch in '}]':
                    depth -= 1
        t = t + ('"' if in_str else '') + ('}' if depth > 0 else '')
    return t


def chat_json(messages, temperature=None, max_tokens=4096, model=None):
    raw = chat(messages, json_mode=True, temperature=temperature,
               max_tokens=max_tokens, model=model)
    t = _strip_fence(raw)
    try:
        return json.loads(t)
    except Exception:
        t2 = _repair_json(t)
        try:
            return json.loads(t2)
        except Exception:
            m = re.search(r'\{.*\}', t2, re.S)
            if m:
                try:
                    return json.loads(_repair_json(m.group(0)))
                except Exception:
                    pass
            raise LLMError('模型返回的不是合法 JSON：%s' % t[:200])
