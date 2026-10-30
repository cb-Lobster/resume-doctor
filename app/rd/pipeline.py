# -*- coding: utf-8 -*-
"""诊断主流程（两段式）。

阶段一 prepare：解析 → 抽 JD → 知识库检索 → **生成澄清追问**
阶段二 finish ：评分 → 依据溯源 → 改写 → 护栏 → 落库 → 版本对比

两个新能力在这个文件里落地：
· 澄清追问：prepare 产出 questions，finish 接收 answers，答案作为可信事实注入评分
· 依据溯源：分数算完后用 trace.ref() 反查 rubric 档位，维度与扣分点都挂"依据"
"""

import json
import re

from . import clarify, db, extract, guard, kb, llm, memory, resume, rulescore, trace

DIMENSIONS = [
    ('jd_match', 'JD 关键词匹配', 25),
    ('quantified', '成果量化', 20),
    ('structure', '结构与重点', 15),
    ('star', '表达质量（STAR）', 15),
    ('completeness', '硬性信息完整', 15),
    ('details', '细节规范', 10),
]
DIM_NAMES = {k: n for k, n, _ in DIMENSIONS}
DIM_MAX = {k: m for k, _, m in DIMENSIONS}
MAX_CHARS = 7000


def _clip(text, n=MAX_CHARS):
    text = text or ''
    return text if len(text) <= n else text[:n] + '\n...[内容过长已截断]'


def _noop(*a, **k):
    return None


# ---------------------------------------------------------------- 阶段一
def extract_jd(jd_text, progress):
    progress('提取 JD 要求', 18, '正在解析目标岗位的硬性要求与加分项')
    sys_p = ('你是资深技术招聘官。请从岗位描述（JD）中抽取结构化要求，只输出 JSON 对象。'
             '字段：title(岗位名)、seniority(实习/校招/社招)、'
             'hard_requirements(硬性要求数组，每条≤30字)、plus_points(加分项数组)、'
             'keywords(简历中应出现的关键词数组，保持原词，如 K8s、Prometheus、Linux，10-25个)、'
             'responsibilities(岗位职责数组，每条≤30字)。'
             '要求：keywords 必须是 JD 里真实出现或明确隐含的技术/工具/资质名词，不要编造。')
    content = llm.chat_json([
        {'role': 'system', 'content': sys_p},
        {'role': 'user', 'content': 'JD 原文：\n' + _clip(jd_text, 5000)},
    ], max_tokens=1500)
    return {
        'title': (content.get('title') or '目标岗位').strip(),
        'seniority': content.get('seniority') or '',
        'hard_requirements': [str(x).strip() for x in (content.get('hard_requirements') or [])][:12],
        'plus_points': [str(x).strip() for x in (content.get('plus_points') or [])][:8],
        'keywords': [str(x).strip() for x in (content.get('keywords') or [])][:25],
        'responsibilities': [str(x).strip() for x in (content.get('responsibilities') or [])][:10],
    }


def prepare(*, user_id, session_id, filename, resume_path=None, resume_text=None,
            jd_text, label=None, engine='auto', progress=None):
    db.init()
    progress = progress or _noop
    user_id = db.ensure_user(user_id)

    progress('解析简历', 6, '读取文件并抽取结构（教育/技能/项目/实习/证书）')
    if resume_path:
        text, err = extract.extract_text(resume_path)
        if err:
            raise ValueError(err)
    else:
        text = resume_text or ''
    if len(re.sub(r'\s', '', text)) < 80:
        raise ValueError('简历内容过少（不足 80 字），无法诊断')

    parsed = resume.parse(text)
    met = resume.metrics(text, parsed['sections'], None)
    progress('解析简历', 12, '已识别小节：%s' % ('、'.join(met['sections_found']) or '未识别到标准小节'))

    engine_used, offline_reason = 'llm', ''
    if engine == 'offline':
        engine_used, offline_reason = 'offline', '已手动切换到离线演示模式'
        jd = rulescore.extract_jd_rules(jd_text)
        progress('提取 JD 要求', 18, '离线模式：用规则从 JD 中抽取关键词')
    else:
        try:
            jd = extract_jd(jd_text, progress)
        except Exception as e:
            engine_used, offline_reason = 'offline', str(e)[:160]
            jd = rulescore.extract_jd_rules(jd_text)
            progress('提取 JD 要求', 18, '调用模型失败，自动切离线规则引擎：%s' % str(e)[:60])

    met = resume.metrics(text, parsed['sections'], jd['keywords'])
    memory.update(session_id, goal='%s（%s）' % (jd['title'], jd.get('seniority') or '实习'),
                  facts=['目标岗位：%s' % jd['title'],
                         '简历版本：%s' % (label or filename or '未命名')])

    progress('知识库检索', 30, '检索评分细则、岗位胜任力、优秀范例')
    progress('补充确认', 36, '正在检查简历里的信息缺口')

    ctx = {
        'user_id': user_id, 'session_id': session_id, 'filename': filename or '简历',
        'label': label, 'jd_text': jd_text, 'text': text, 'parsed': parsed,
        'sections': parsed['sections'], 'met': met,
        'jd': jd, 'engine': engine_used, 'offline_reason': offline_reason,
        'profile': db.get_profile(user_id),
    }
    questions = clarify.build(ctx)
    ctx['questions'] = questions
    if questions:
        progress('补充确认', 40, '发现 %d 个信息缺口，等待用户确认（可全部跳过）' % len(questions))
    else:
        progress('补充确认', 40, '未发现明显信息缺口，直接进入评分')
    return ctx


# ---------------------------------------------------------------- 评分
def _rubric_text():
    rub = kb.get_kb()
    parts = []
    for key, name, mx in DIMENSIONS:
        t = rub.rubric(key)
        if t:
            parts.append('### %s（满分 %d）\n%s' % (name, mx, t.strip()))
    return '\n\n'.join(parts)


def score_resume(resume_blocks, jd, met, progress, facts=None, negative=None):
    progress('逐维度评分', 45, 'LLM 按 6 个维度体检并生成扣分点')
    k = kb.get_kb()
    comp = k.competency(jd['title'] + ' ' + ' '.join(jd['keywords']), top=2)
    comp_text = '\n'.join('【%s】\n%s' % (c['title'], c['text']) for c in comp)
    sample_text = '\n'.join('【%s】\n%s' % (s['title'], s['text']) for s in k.samples(jd['title'], top=1))

    sys_p = (
        '你是一名资深 HR + 技术面试官，正在为应届生/实习生做简历诊断。'
        '请严格依据【评分细则】逐维度打分，输出一个 JSON 对象，不要输出任何解释性文字。\n'
        '硬性要求：\n'
        '1) 维度分不得超过该维度满分；\n'
        '2) 每条判断必须引用简历原文片段作为证据（evidence），不得空泛；\n'
        '3) 不得虚构简历里没有的经历、数字、证书；\n'
        '4) deduction_top3 按"改进收益最高"排序，每条要给出可直接执行的动作；\n'
        '5) 语言为中文，简洁专业。\n\n'
        '评分尺度（重要，务必对齐真实 HR 初筛口径，不要系统性偏低）：\n'
        '- 结构完整、能进面试的实习简历通常落在 65-80 分；\n'
        '- 有明显缺陷（缺量化/缺证书/表达空泛）但骨架完整的，落在 45-62 分；\n'
        '- 只有几乎不成简历（无结构、通篇口号）才低于 40 分；\n'
        '- 除该维度完全缺失外，尽量不给 0-2 分。\n\n'
        'JSON 结构：\n'
        '{"one_liner":"一句话诊断(≤30字)",'
        '"summary":"总体评价(3-5句)",'
        '"jd_fit":{"level":"偏低|基本匹配|高度匹配","comment":"一句话"},'
        '"dims":[{"key":"jd_match","name":"JD 关键词匹配","max":25,"score":18,'
        '"reason":"打分理由","evidence":["原文片段1","原文片段2"],"fix":"一句可执行动作"}],'
        '"deduction_top3":[{"dimension":"维度名","problem":"问题","advice":"怎么改","impact":"预计带来什么变化"}],'
        '"interview_risks":["面试官会追问/质疑的点"],'
        '"next_actions":["下一步动作1","下一步动作2","下一步动作3"]}\n'
        'dims 必须恰好包含 6 个元素，key 依次为 jd_match, quantified, structure, star, completeness, details。'
    )

    fact_block = ''
    if facts:
        fact_block = ('【用户补充确认的事实（用户本人回答，可作为证据，请在 evidence 中标注"用户确认"）】\n- '
                      + '\n- '.join(facts) + '\n\n')
    if negative:
        fact_block += ('【用户明确表示"没有"的项（不要为此给分）】\n- ' + '\n- '.join(negative) + '\n\n')

    user = (
        '%s【评分细则】\n%s\n\n【岗位胜任力参考】\n%s\n\n【优秀写法参考】\n%s\n\n'
        '【目标岗位】%s（%s）\n硬性要求：%s\n加分项：%s\n关键词：%s\n\n'
        '【简历结构】\n%s\n\n'
        '【客观指标（脚本统计，可信）】\n%s\n\n'
        '现在开始评分。'
    ) % (
        fact_block, _rubric_text(), comp_text, sample_text,
        jd['title'], jd.get('seniority') or '实习',
        '；'.join(jd['hard_requirements']) or '（未提取到）',
        '；'.join(jd['plus_points']) or '（无）',
        '、'.join(jd['keywords']) or '（无）',
        _clip(resume_blocks, 6000),
        json.dumps(met, ensure_ascii=False, indent=1),
    )
    data = llm.chat_json([
        {'role': 'system', 'content': sys_p},
        {'role': 'user', 'content': user},
    ], max_tokens=3500)
    return _normalize(data)


def _normalize(data):
    got = {d.get('key'): d for d in (data.get('dims') or []) if isinstance(d, dict)}
    dims = []
    for key, name, mx in DIMENSIONS:
        d = got.get(key, {})
        try:
            sc = float(d.get('score', 0))
        except Exception:
            sc = 0.0
        sc = max(0.0, min(float(mx), sc))
        dims.append({
            'key': key, 'name': name, 'max': mx,
            'score': round(sc, 1), 'rate': round(sc / mx, 3),
            'reason': (d.get('reason') or '').strip(),
            'evidence': [str(x) for x in (d.get('evidence') or [])][:4],
            'fix': (d.get('fix') or '').strip(),
        })
    total = int(round(sum(d['score'] for d in dims)))
    out = {
        'dims': dims, 'total': total,
        'one_liner': (data.get('one_liner') or '').strip(),
        'summary': (data.get('summary') or '').strip(),
        'deduction_top3': [d for d in (data.get('deduction_top3') or []) if isinstance(d, dict)][:3],
        'interview_risks': [str(x) for x in (data.get('interview_risks') or [])][:5],
        'next_actions': [str(x) for x in (data.get('next_actions') or [])][:5],
    }
    jf = data.get('jd_fit') or {}
    out['jd_fit'] = {'level': jf.get('level') or '基本匹配', 'comment': jf.get('comment') or ''}
    return out


def make_rewrites(resume_blocks, jd, scored, progress):
    progress('生成改写示例', 68, '针对最影响分数的条目生成改写参考稿')
    weak = sorted(scored['dims'], key=lambda d: d['rate'])[:3]
    weak_txt = '\n'.join('- %s（%s/%s）：%s' % (d['name'], d['score'], d['max'], d['reason'])
                         for d in weak)
    samples = '\n'.join('【%s】\n%s' % (s['title'], s['text'])
                        for s in kb.get_kb().samples(jd['title'] + ' ' + ' '.join(jd['keywords']), top=2))
    sys_p = (
        '你是资深简历优化顾问。请针对这份简历里"最影响打分"的 2-3 个条目给出改写参考稿。'
        '只输出 JSON 对象。\n'
        '红线（违反即失败）：\n'
        '1) 不得新增原文没有的经历、公司、项目、证书、数字；\n'
        '2) 技能/工具/技术名词只能使用"原文已经出现过"的，绝不能为了贴合 JD 而添加原文没有的技术；\n'
        '3) 原句缺少关键数据时，用【请补充：xxx】占位符，不许编造数字；\n'
        '4) 只改写原句的表达与结构（动作+规模+方法+结果）；\n'
        '5) 每条改写必须能与原文逐句对应，不得整段重写用户没有的内容。\n'
        'JSON 结构：{"rewrites":[{"id":"r1","section":"所属小节","original":"简历原句(必须原文照抄)",'
        '"rewritten":"改写参考稿","reason":"为什么这样改","gaps":["缺失的数据点"]}]}'
    )
    user = (
        '【目标岗位】%s\n关键词：%s\n\n【本次诊断的薄弱维度】\n%s\n\n'
        '【优秀写法参考】\n%s\n\n【简历正文】\n%s\n\n请输出 2-3 条改写。'
    ) % (jd['title'], '、'.join(jd['keywords']) or '（无）', weak_txt, samples, _clip(resume_blocks, 5000))
    data = llm.chat_json([
        {'role': 'system', 'content': sys_p},
        {'role': 'user', 'content': user},
    ], max_tokens=2500)
    out = []
    for i, r in enumerate((data.get('rewrites') or [])[:3]):
        if not isinstance(r, dict):
            continue
        item = {
            'id': r.get('id') or ('r%d' % (i + 1)),
            'section': (r.get('section') or '').strip(),
            'original': (r.get('original') or '').strip(),
            'rewritten': (r.get('rewritten') or '').strip(),
            'reason': (r.get('reason') or '').strip(),
            'gaps': [str(x) for x in (r.get('gaps') or [])][:5],
        }
        if not item['original'] or not item['rewritten']:
            continue
        risks, _ = guard.fabricate_check(item['original'], item['rewritten'],
                                        jd['keywords'], context=resume_blocks)
        item['fabrication_risks'] = risks
        out.append(item)
    return out


# ---------------------------------------------------------------- 对比
def rule_compare(prev, current):
    deltas = {}
    for d in current['dims']:
        p = next((x for x in prev.get('dims', []) if x.get('key') == d.get('key')), None)
        deltas[d['key']] = {
            'name': d['name'], 'prev': p['score'] if p else None,
            'now': d['score'], 'delta': round(d['score'] - p['score'], 1) if p else None,
        }
    result = {
        'prev_id': prev['id'], 'prev_seq': prev['seq'], 'prev_total': prev['total'],
        'delta_total': (current['total'] or 0) - (prev['total'] or 0),
        'prev_created_at': prev['created_at'],
        'dims': deltas, 'fixed': [], 'remaining': [], 'new_issues': [], 'comment': '',
    }
    prev_ded = prev.get('deductions') or []
    cur_ded = current.get('deduction_top3') or []
    cur_text = ' '.join(json.dumps(d, ensure_ascii=False) for d in cur_ded) + ' ' + \
               ' '.join(d.get('reason', '') for d in current['dims'])
    for d in prev_ded:
        dim = d.get('dimension') or ''
        pv = d.get('problem') or ''
        key_terms = [t for t in re.findall(r'[\u4e00-\u9fa5]{2,6}', pv)][:3]
        still = bool(dim) and dim in cur_text and any(t in cur_text for t in key_terms)
        (result['remaining'] if still else result['fixed']).append({'dimension': dim, 'problem': pv})
    pm = prev.get('metrics') or {}
    cm = current.get('metrics') or {}
    if (cm.get('quant_count_exp') or 0) > (pm.get('quant_count_exp') or 0):
        result['comment'] += '量化数据从 %s 增加到 %s 处。' % (
            pm.get('quant_count_exp', 0), cm.get('quant_count_exp', 0))
    return result


def compare_with_prev(user_id, seq, current, progress, engine='llm'):
    prev = db.prev_version(user_id, seq)
    if not prev:
        return None
    progress('版本对比', 82, '与上一版（v%d）逐维度对比' % prev['seq'])
    result = rule_compare(prev, current)
    if engine != 'llm':
        result['comment'] = (result['comment'] + ' （离线模式：仅做规则层对比）').strip()
        return result
    prev_ded = prev.get('deductions') or []
    cur_ded = current.get('deduction_top3') or []
    try:
        sys_p = ('你是简历教练，在对比同一用户两版简历的诊断结果。只输出 JSON 对象：'
                 '{"comment":"2-3句进步与风险总结","fixed":["已修复的问题"],'
                 '"remaining":["仍未修复的问题"],"new_issues":["本次新出现的问题"],'
                 '"advice":"下一步最该做的一件事"}')
        user = ('上一版（v%d，总分 %s）：扣分点\n%s\n\n本版（v%d，总分 %s）：扣分点\n%s\n\n'
                '维度变化：%s\n客观指标变化：量化 %s→%s，篇幅 %s→%s 页') % (
            prev['seq'], prev['total'], json.dumps(prev_ded, ensure_ascii=False),
            seq, current['total'], json.dumps(cur_ded, ensure_ascii=False),
            json.dumps(result['dims'], ensure_ascii=False),
            prev['metrics'].get('quant_count_exp'), current['metrics'].get('quant_count_exp'),
            prev['metrics'].get('est_pages'), current['metrics'].get('est_pages'))
        d = llm.chat_json([{'role': 'system', 'content': sys_p},
                           {'role': 'user', 'content': user}], max_tokens=1200)
        result['comment'] = ((d.get('comment') or '') + ' ' + result['comment']).strip()
        if d.get('fixed'):
            result['fixed_llm'] = [str(x) for x in d['fixed']][:5]
        if d.get('remaining'):
            result['remaining_llm'] = [str(x) for x in d['remaining']][:5]
        if d.get('new_issues'):
            result['new_issues'] = [str(x) for x in d['new_issues']][:5]
        result['advice'] = (d.get('advice') or '').strip()
    except Exception as e:
        result['comment'] = (result['comment'] + ' （LLM 对比分析失败：%s）' % e).strip()
    return result


# ---------------------------------------------------------------- 依据溯源
def attach_refs(scored):
    """给每个维度和每条扣分点挂上 rubric 原文依据（分数反查档位，不靠模型）。"""
    for d in scored['dims']:
        d['ref'] = trace.ref(d['key'], d['score'])
    for ded in scored.get('deduction_top3') or []:
        key = trace.name_to_key(ded.get('dimension'))
        dim = next((x for x in scored['dims'] if x['key'] == key), None)
        if dim:
            ded['ref'] = trace.ref(key, dim['score'])
            ded['dim_name'] = dim['name']
        else:
            ded['ref'] = None
    return scored


# ---------------------------------------------------------------- 阶段二
def finish(ctx, answers=None, progress=None):
    progress = progress or _noop
    answers = answers or {}
    questions = ctx.get('questions') or []
    facts, negative = clarify.facts(questions, answers)
    summary = clarify.summarize(questions, answers)

    jd, met, parsed = ctx['jd'], ctx['met'], ctx['parsed']
    engine = ctx['engine']

    if engine == 'offline':
        progress('逐维度评分', 45, '离线规则引擎：按关键词覆盖率/量化计数/STAR 词表打分')
        scored = rulescore.score(parsed, jd, met, facts)
    else:
        if facts or negative:
            progress('逐维度评分', 45, '已合并 %d 条用户确认事实，开始评分' % len(facts))
        try:
            scored = score_resume(parsed['blocks'], jd, met, progress, facts, negative)
        except Exception as e:
            progress('逐维度评分', 46, '模型评分失败，回退规则引擎：%s' % str(e)[:60])
            engine = 'offline'
            ctx['engine'] = 'offline'
            ctx['offline_reason'] = str(e)[:160]
            scored = rulescore.score(parsed, jd, met, facts)
    scored = attach_refs(scored)

    try:
        if engine == 'offline':
            rewrites = rulescore.rewrites(parsed, jd, met, scored)
            progress('生成改写示例', 68, '离线模式：输出句式框架 + 占位符（不编造事实）')
        else:
            rewrites = make_rewrites(parsed['blocks'], jd, scored, progress)
    except Exception as e:
        rewrites = []
        progress('生成改写示例', 70, '改写生成失败：%s' % e)

    progress('护栏检查', 78, '检测隐私信息与改写是否引入原文没有的事实')
    pii = guard.pii_scan(ctx['text'])
    for r in rewrites:
        r['placeholders'] = guard.placeholder_check(r['rewritten'])
    guard_block = {
        'pii': pii,
        'fabrication': [{'id': r['id'], 'risks': r.get('fabrication_risks', [])}
                        for r in rewrites if r.get('fabrication_risks')],
        'note': '改写稿仅供参考表述优化，请确认与真实经历一致后再使用；不会自动修改你的简历文件。'
                + ('（当前为离线演示模式，改写为句式框架）' if engine == 'offline' else ''),
    }

    report = {
        'user_id': ctx['user_id'],
        'jd': jd,
        'dims': scored['dims'],
        'total': scored['total'],
        'one_liner': scored['one_liner'],
        'summary': scored['summary'],
        'jd_fit': scored['jd_fit'],
        'deduction_top3': scored['deduction_top3'],
        'interview_risks': scored['interview_risks'],
        'next_actions': scored['next_actions'],
        'rewrites': rewrites,
        'guard': guard_block,
        'metrics': met,
        'sections_found': met['sections_found'],
        'filename': ctx['filename'],
        'label': ctx['label'] or ctx['filename'],
        'engine': engine,
        'offline_reason': ctx.get('offline_reason') or '',
        'score_source': '规则引擎（离线）' if engine == 'offline' else '大模型语义判断',
        'clarify': {
            'questions': questions, 'answers': answers,
            'facts': facts, 'negative': negative, 'summary': summary,
        },
    }

    vid, seq = db.add_version(
        ctx['user_id'], label=ctx['label'] or ctx['filename'], filename=ctx['filename'],
        jd_title=jd['title'], total=scored['total'], dims=scored['dims'],
        deductions=scored['deduction_top3'], rewrites=rewrites, guard=guard_block,
        metrics=met, jd_struct=jd, resume_text=ctx['text'], jd_text=ctx['jd_text'],
        extra={'one_liner': scored['one_liner'], 'summary': scored['summary'],
               'jd_fit': scored['jd_fit'], 'interview_risks': scored['interview_risks'],
               'next_actions': scored['next_actions'], 'engine': engine,
               'offline_reason': ctx.get('offline_reason') or '',
               'clarify': report['clarify']})

    current = {'dims': scored['dims'], 'total': scored['total'],
               'deduction_top3': scored['deduction_top3'], 'metrics': met}
    cmp = compare_with_prev(ctx['user_id'], seq, current, progress, engine=engine)

    report['version'] = {'id': vid, 'seq': seq, 'created_at': db.now()}
    report['compare'] = cmp
    report['adoption_stats'] = db.adoption_rate(ctx['user_id'])
    report['hr_score'] = db.get_hr_score(vid)

    prof = dict(ctx.get('profile') or {})
    prof['last_role'] = jd['title']
    prof.setdefault('roles', [])
    if jd['title'] not in prof['roles']:
        prof['roles'] = (prof['roles'] + [jd['title']])[-5:]
    prof['total_versions'] = (prof.get('total_versions') or 0) + 1
    db.save_profile(ctx['user_id'], prof)
    report['profile'] = prof
    report['usage'] = llm.usage()
    progress('完成', 100, '诊断报告已生成并归档为 v%d' % seq)
    return report, vid


def analyze(**kw):
    """兼容旧接口：不分澄清，一次跑完。"""
    progress = kw.pop('progress', None)
    answers = kw.pop('answers', None) or {}
    kw['engine'] = kw.get('engine') or 'auto'
    ctx = prepare(progress=progress, **kw)
    return finish(ctx, answers, progress)
