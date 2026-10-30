# -*- coding: utf-8 -*-
"""简历诊断 Agent（Resume Doctor · Demo）——本地 Web 服务。

运行：双击项目根目录的「启动.bat」（内置 Python 运行时，无需安装任何东西）
或：runtime\\python\\python.exe app\\app.py --port 8765 --open

零第三方依赖，仅用 Python 标准库。
"""

import argparse
import base64
import json
import mimetypes
import os
import re
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from rd import auth, calib, db, kb, llm, memory, pipeline, trace

ROOT = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ROOT, 'web')
UPLOADS = os.path.join(ROOT, 'uploads')
SAMPLE_DIR = os.path.join(os.path.dirname(ROOT), 'sample')
SAMPLES = {
    'jd': ('JD-云计算运维实习.txt', '云计算运维实习 JD'),
    'good1': ('简历示例v1-张伟.txt', '张伟 v1（待改进）'),
    'good2': ('简历示例v2-张伟.txt', '张伟 v2（修改后）'),
    'bad': ('简历示例v0-李萌-需改进.txt', '李萌（问题较多）'),
}
JOBS = {}
LOCK = threading.Lock()


def _now():
    return time.strftime('%H:%M:%S')


def _job_new():
    jid = uuid.uuid4().hex[:12]
    with LOCK:
        JOBS[jid] = {
            'id': jid, 'status': 'running', 'created': time.time(),
            'progress': {'stage': '排队中', 'pct': 2, 'msg': '任务已创建'},
            'logs': [], 'report': None, 'error': None, 'version_id': None,
            'clarify': None, 'ctx': None, 'traceback': None,
        }
    return jid


def _job_update(jid, stage=None, pct=None, msg=None):
    with LOCK:
        j = JOBS.get(jid)
        if not j:
            return
        if stage is not None:
            j['progress']['stage'] = stage
        if pct is not None:
            j['progress']['pct'] = pct
        if msg is not None:
            j['progress']['msg'] = msg
            j['logs'].append({'t': _now(), 'stage': j['progress']['stage'], 'msg': msg})
            j['logs'] = j['logs'][-40:]


def _cleanup_jobs():
    cutoff = time.time() - 3600
    with LOCK:
        for k in [k for k, v in JOBS.items() if v['created'] < cutoff]:
            JOBS.pop(k, None)


def _read_sample(name):
    item = SAMPLES.get(name)
    if not item:
        raise ValueError('没有这个示例：%s' % name)
    fn = item[0]
    path = os.path.join(SAMPLE_DIR, fn)
    if not os.path.isfile(path):
        raise ValueError('示例文件缺失：' + fn)
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            with open(path, 'r', encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError('示例文件编码无法识别：' + fn)


def _hr_dims(dims, target):
    """把人工总分按 Agent 各维度的比例摊回六维度，整数且总和严格等于 target。

    演示数据需要"人工分带六维度"，否则校准页的维度偏差区无样本可算。
    按比例摊分保证维度结构与 Agent 判断同向（差异只体现在整体松紧），
    更接近真实人工评分的样子；不做随机扰动，保证每次演示结果一致。
    """
    rows = []
    for d in (dims or []):
        try:
            rows.append({'key': d['key'], 'score': float(d.get('score') or 0),
                         'max': float(d.get('max') or 100)})
        except Exception:
            continue
    if not rows:
        return {}
    total_raw = sum(r['score'] for r in rows) or 1.0
    out, used = {}, 0
    for i, r in enumerate(rows):
        if i == len(rows) - 1:
            val = int(target) - used
        else:
            val = int(round(float(target) * r['score'] / total_raw))
            val = max(0, min(val, int(r['max'])))
            used += val
        out[r['key']] = val
    # 末项可能溢出该维度上限，把超出部分挪给第一个维度（其上限通常是 25，有空间）
    last, first = rows[-1], rows[0]
    over = out[last['key']] - int(last['max'])
    if over > 0:
        out[last['key']] -= over
        out[first['key']] += over
    return out



def _save_upload(payload):
    if not payload.get('file_b64'):
        return None
    os.makedirs(UPLOADS, exist_ok=True)
    name = os.path.basename(payload.get('filename') or 'resume')
    name = re.sub(r'[^0-9A-Za-z\u4e00-\u9fa5._-]', '_', name) or 'resume'
    path = os.path.join(UPLOADS, '%s_%s' % (int(time.time()), name))
    with open(path, 'wb') as f:
        f.write(base64.b64decode(payload['file_b64']))
    return path


def _worker(jid, payload):
    """阶段一：解析 + 追问。有问题就停下等用户作答。"""
    try:
        resume_path = _save_upload(payload)

        def progress(stage, pct, msg):
            _job_update(jid, stage, pct, msg)

        ctx = pipeline.prepare(
            user_id=payload.get('user_id'),
            session_id=payload.get('session_id') or 'default',
            filename=payload.get('filename') or '简历',
            resume_path=resume_path,
            resume_text=payload.get('resume_text'),
            jd_text=payload.get('jd_text') or '',
            label=payload.get('label'),
            engine=payload.get('engine') or 'auto',
            progress=progress,
        )
        questions = ctx.get('questions') or []
        if questions and not payload.get('skip_clarify'):
            with LOCK:
                JOBS[jid]['ctx'] = ctx
                JOBS[jid]['clarify'] = {'questions': questions, 'answers': {}}
                JOBS[jid]['status'] = 'waiting'
                JOBS[jid]['progress'] = dict(JOBS[jid]['progress'])
            _job_update(jid, '补充确认', 40, '等待确认（可直接跳过，不影响出报告）')
            return
        _finish(jid, ctx, {}, payload.get('engine') == 'offline')
    except Exception as e:
        with LOCK:
            j = JOBS.get(jid)
            if j is not None:
                j['status'] = 'error'
                j['error'] = str(e)
                j['traceback'] = traceback.format_exc()[-2000:]


def _finish(jid, ctx, answers, forced_offline=False):
    """阶段二：评分 + 依据 + 改写 + 落库。"""
    try:
        def progress(stage, pct, msg):
            _job_update(jid, stage, pct, msg)

        with LOCK:
            j = JOBS.get(jid)
            if j is not None:
                j['status'] = 'running'
        report, vid = pipeline.finish(ctx, answers, progress)
        with LOCK:
            j = JOBS.get(jid)
            if j is not None:
                j['report'] = report
                j['version_id'] = vid
                j['status'] = 'done'
                j['ctx'] = None
    except Exception as e:
        with LOCK:
            j = JOBS.get(jid)
            if j is not None:
                j['status'] = 'error'
                j['error'] = str(e)
                j['traceback'] = traceback.format_exc()[-2000:]


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    if os.name == 'nt':
        allow_reuse_address = False


class Handler(BaseHTTPRequestHandler):
    server_version = 'ResumeDoctorDemo/2.0'

    def log_message(self, fmt, *args):
        if os.environ.get('RD_VERBOSE'):
            super().log_message(fmt, *args)

    # ---------- helpers ----------
    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        if getattr(self, "_cookie", ""):
            self.send_header("Set-Cookie", self._cookie)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self):
        n = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(n) if n else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _static(self, rel):
        path = os.path.normpath(os.path.join(WEB, rel.lstrip('/')))
        if not path.startswith(WEB) or not os.path.isfile(path):
            return self._send(404, {'error': 'not found'})
        ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
        if ctype.startswith('text/') or ctype in ('application/javascript', 'application/json'):
            ctype += '; charset=utf-8'
        with open(path, 'rb') as f:
            data = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)

    # ---------- routes ----------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        p = u.path
        if not auth.ensure(self, p):
            return
        if p in ('/', '/index.html'):
            return self._static('index.html')
        if p.startswith('/static/'):
            return self._static(p[len('/static/'):])

        if p == '/api/health':
            cfg = llm.load_config()
            return self._send(200, {
                'ok': True, 'model': cfg['model'],
                'has_key': bool(cfg['api_key']),
                'kb_docs': len(kb.get_kb().docs),
                'dims': [{'key': k, 'name': n, 'max': m} for k, n, m in pipeline.DIMENSIONS],
                'rubric': trace.dims_catalog(),
                'version': '2.0-demo',
            })

        if p == '/api/rubric':
            return self._send(200, {'catalog': trace.dims_catalog(),
                                    'path': 'kb/rubric.md'})

        if p == '/api/sample':
            name = (q.get('name') or [''])[0]
            item = SAMPLES.get(name)
            if not item:
                return self._send(404, {'error': '没有这个示例'})
            try:
                return self._send(200, {'text': _read_sample(name),
                                        'filename': item[0], 'title': item[1]})
            except ValueError as e:
                return self._send(404, {'error': str(e)})

        if p.startswith('/api/job/'):
            jid = p.rsplit('/', 1)[-1]
            _cleanup_jobs()
            with LOCK:
                j = JOBS.get(jid)
                if not j:
                    return self._send(404, {'error': '任务不存在或已过期'})
                out = {
                    'id': j['id'], 'status': j['status'], 'progress': j['progress'],
                    'logs': j['logs'][-12:], 'error': j['error'],
                    'version_id': j['version_id'], 'clarify': j['clarify'],
                }
                if j['status'] == 'done':
                    out['report'] = j['report']
            return self._send(200, out)

        if p == '/api/versions':
            uid = (q.get('user_id') or [''])[0]
            if not uid:
                return self._send(400, {'error': 'missing user_id'})
            versions = db.list_versions(uid)
            for v in versions:
                v['hr_score'] = db.get_hr_score(v['id'])
            return self._send(200, {'versions': versions,
                                    'stats': db.adoption_rate(uid)})

        if p.startswith('/api/version/'):
            try:
                vid = int(p.rsplit('/', 1)[-1])
            except ValueError:
                return self._send(400, {'error': 'bad id'})
            v = db.get_version(vid)
            if not v:
                return self._send(404, {'error': '版本不存在'})
            v['adoptions'] = db.adoptions(vid)
            v['hr_score'] = db.get_hr_score(vid)
            v['engine'] = (v.get('engine') or 'llm')
            if (q.get('compare') or [''])[0] in ('1', 'true'):
                prev = db.prev_version(v.get('user_id') or '', v['seq']) if v.get('user_id') else None
                v['compare'] = pipeline.rule_compare(prev, v) if prev else None
            return self._send(200, v)

        if p == '/api/session':
            sid = (q.get('session_id') or ['default'])[0]
            return self._send(200, memory.get(sid))

        if p == '/api/calibration':
            uid = (q.get('user_id') or [''])[0]
            if not uid:
                return self._send(400, {'error': 'missing user_id'})
            return self._send(200, calib.calibration(uid))

        if p == '/api/adoption_board':
            uid = (q.get('user_id') or [''])[0]
            if not uid:
                return self._send(400, {'error': 'missing user_id'})
            return self._send(200, calib.adoption_board(uid))

        return self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        body = self._json_body()
        if auth.handle(self, p, body):
            return
        if not auth.ensure(self, p):
            return

        if p == '/api/analyze':
            if not (body.get('jd_text') or '').strip():
                return self._send(400, {'error': '请粘贴目标岗位 JD'})
            if not body.get('file_b64') and len((body.get('resume_text') or '').strip()) < 80:
                return self._send(400, {'error': '请上传简历文件或粘贴简历正文（≥80 字）'})
            jid = _job_new()
            threading.Thread(target=_worker, args=(jid, body), daemon=True).start()
            return self._send(200, {'job_id': jid})

        if p == '/api/clarify':
            jid = body.get('job_id')
            answers = body.get('answers') or {}
            with LOCK:
                j = JOBS.get(jid)
                if not j:
                    return self._send(404, {'error': '任务不存在或已过期'})
                if j['status'] != 'waiting' or not j['ctx']:
                    return self._send(409, {'error': '当前任务不在等待确认状态'})
                ctx = j['ctx']
                j['clarify'] = {'questions': ctx.get('questions') or [], 'answers': answers}
                j['status'] = 'running'
            threading.Thread(target=_finish, args=(jid, ctx, answers), daemon=True).start()
            return self._send(200, {'ok': True})

        if p == '/api/adopt':
            vid = body.get('version_id')
            key = body.get('item_key')
            if not vid or not key:
                return self._send(400, {'error': 'missing params'})
            db.set_adoption(int(vid), str(key), bool(body.get('adopted')))
            v = db.get_version(int(vid))
            board = calib.adoption_board(v['user_id']) if v else None
            return self._send(200, {'ok': True, 'board': board})

        if p == '/api/hr_score':
            vid = body.get('version_id')
            if not vid:
                return self._send(400, {'error': 'missing version_id'})
            try:
                total = float(body.get('total'))
            except Exception:
                return self._send(400, {'error': '请填人工总分（0-100）'})
            total = max(0.0, min(100.0, total))
            dims = body.get('dims') or {}
            clean = {}
            for k, _n, mx in pipeline.DIMENSIONS:
                if isinstance(dims.get(k), (int, float)) or (isinstance(dims.get(k), str) and dims.get(k).strip()):
                    try:
                        clean[k] = round(max(0.0, min(float(mx), float(dims[k]))), 1)
                    except Exception:
                        pass
            try:
                row = db.set_hr_score(int(vid), total, clean,
                                      body.get('reviewer') or '', body.get('note') or '')
            except ValueError as e:
                return self._send(400, {'error': str(e)})
            v = db.get_version(int(vid))
            return self._send(200, {'ok': True, 'hr_score': row,
                                    'board': calib.calibration(v['user_id'])})

        if p == '/api/hr_score/clear':
            vid = body.get('version_id')
            if not vid:
                return self._send(400, {'error': 'missing version_id'})
            db.del_hr_score(int(vid))
            v = db.get_version(int(vid))
            return self._send(200, {'ok': True,
                                    'board': calib.calibration(v['user_id']) if v else None})

        if p == '/api/demo_seed':
            # 一键演示数据：两个版本（离线引擎，秒出）+ 人工评分 + 采纳记录
            uid = db.ensure_user(body.get('user_id'))
            seeded = [v for v in db.list_versions(uid, limit=100)
                      if (v.get('label') or '').startswith('演示-')]
            if len(seeded) >= 2:
                return self._send(200, {'ok': True, 'seeded': False, 'user_id': uid,
                                        'msg': '演示数据已存在'})
            try:
                jd = _read_sample('jd')
                made = []
                for key, label in (('bad', '演示-v1 李萌'), ('good2', '演示-v2 张伟')):
                    text = _read_sample(key)
                    ctx = pipeline.prepare(user_id=uid, session_id='demo_seed',
                                           filename=SAMPLES[key][0], resume_text=text,
                                           jd_text=jd, label=label, engine='offline')
                    rep, vid = pipeline.finish(ctx, {}, None)
                    made.append({'version_id': vid, 'seq': rep['version']['seq'],
                                 'total': rep['total'], 'label': label,
                                 'dims': rep.get('dims') or []})
                v1, v2 = made[0]['version_id'], made[1]['version_id']
                db.set_hr_score(v1, 56, _hr_dims(made[0]['dims'], 56),
                                '王老师（演示）', '按实习岗初筛口径')
                db.set_hr_score(v2, 88, _hr_dims(made[1]['dims'], 88),
                                '王老师（演示）', '按实习岗初筛口径')
                db.set_adoption(v1, 'ded_0', True)
                db.set_adoption(v1, 'ded_1', True)
                db.set_adoption(v1, 'ded_2', False)
                return self._send(200, {'ok': True, 'seeded': True, 'user_id': uid,
                                        'versions': made})
            except Exception as e:
                return self._send(500, {'error': '演示数据生成失败：%s' % e})

        if p == '/api/reset_session':
            memory.reset(body.get('session_id') or 'default')
            return self._send(200, {'ok': True})

        if p == '/api/test_llm':
            t0 = time.time()
            try:
                r = llm.chat([{'role': 'user', 'content': '只回复两个字：正常'}], max_tokens=16)
                return self._send(200, {'ok': True, 'reply': r.strip(),
                                        'ms': int((time.time() - t0) * 1000)})
            except Exception as e:
                return self._send(200, {'ok': False, 'error': str(e)[:300]})

        return self._send(404, {'error': 'not found'})


def _bind(host, port, tries=11):
    last = None
    for i in range(tries):
        p = port + i
        try:
            return _Server((host, p), Handler), p
        except OSError as e:
            last = e
            code = getattr(e, 'errno', None)
            if code in (48, 98, 10048) or 'address' in str(e).lower():
                if i == 0:
                    print(' 端口 %d 已被占用（可能已有一个窗口在运行），尝试其它端口…' % p)
                continue
            raise
    raise SystemExit('端口 %d~%d 都无法绑定：%s' % (port, port + tries - 1, last))


def main():
    ap = argparse.ArgumentParser(description='Resume Doctor Demo - 简历诊断 Agent')
    ap.add_argument('--port', type=int, default=int(os.environ.get('RD_PORT', '8765')))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    args = ap.parse_args()

    db.init()
    cfg = llm.load_config()
    srv, port = _bind(args.host, args.port)
    print('=' * 64)
    print(' 简历诊断 Agent（Resume Doctor · Demo 版）已启动')
    print(' 打开浏览器访问： http://%s:%d' % (args.host, port))
    print(' 模型：%s    API Key：%s' % (cfg['model'], '已配置' if cfg['api_key'] else '未配置（自动走离线演示模式）'))
    print(' 知识库片段：%d 个    评分依据档位：%d 条'
          % (len(kb.get_kb().docs), sum(len(x['bands']) for x in trace.dims_catalog())))
    print(' 新增能力：澄清追问 / 依据溯源 / 人工校准 / 采纳看板')
    print(' 停止服务：Ctrl + C')
    print('=' * 64)
    if args.open:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open('http://%s:%d' % (args.host, port))).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止。')


if __name__ == '__main__':
    main()
