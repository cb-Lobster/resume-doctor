# -*- coding: utf-8 -*-
"""澄清追问（Clarify）。

不靠模型瞎编问题：先用**确定性规则**从简历里找出"信息缺口"，
再生成带快捷选项的问句。用户答了 → 作为「用户确认事实」注入评分；
全跳过 → 完全走原有逻辑，一分不差。
"""

import re

NEGATIVE = ('没有', '无', '确实没有', '没有实际规模', '暂无', '不适用', '没做过')

# 每条规则：id / 维度 / 事实标签 / 预期收益区间 / 触发条件 / 问句 / 快捷选项
RULES = [
    {
        'id': 'quant_scale', 'dim': 'quantified', 'fact': '项目规模', 'gain': (5, 8),
        'when': lambda c: c['met'].get('quant_count_exp', 0) == 0 and (
            c['sections'].get('projects') or c['sections'].get('experience')),
        'q': '你的项目/实习大概是什么规模？（部署了几台机器、多少用户、多大并发）',
        'why': '"成果量化"占 20 分，没有规模数字这一项基本拿不到一半分。',
        'chips': ['1-3 台', '5-10 台', '20 台以上', '确实没有可写的规模'],
    },
    {
        'id': 'quant_result', 'dim': 'star', 'fact': '结果指标', 'gain': (4, 6),
        'when': lambda c: not c['met'].get('result_words') and (
            c['sections'].get('projects') or c['sections'].get('experience')),
        'q': '这些项目做完有没有可量化的结果？（耗时下降、故障率、通过率、效率提升）',
        'why': '"表达质量（STAR）"看重结果环节，只有动作没有结果为常见扣分点。',
        'chips': ['有，能写百分比', '有，但只有绝对值', '没有统计过'],
    },
    {
        'id': 'jd_gap', 'dim': 'jd_match', 'fact': 'JD 关键词经历', 'gain': (6, 10),
        'when': lambda c: bool((c['met'].get('jd_coverage') or {}).get('missing'))
        and ((c['met'].get('jd_coverage') or {}).get('rate') or 1) < 0.5,
        'q': None,   # 动态生成
        'why': '"JD 关键词匹配"占 25 分，是权重最高的维度。',
        'chips': ['用过，能写进项目', '只学过，没实战', '完全没接触'],
    },
    {
        'id': 'no_skills', 'dim': 'structure', 'fact': '技能清单', 'gain': (3, 5),
        'when': lambda c: not c['sections'].get('skills'),
        'q': '你实际用过哪些工具/技术栈？可以按"熟练 / 会用 / 了解"三档说。',
        'why': '"结构与重点"要求模块完整，缺技能清单会被判定结构不全。',
        'chips': ['Linux + K8s + Docker', 'Linux + 监控告警', 'Python + Shell 自动化'],
    },
    {
        'id': 'no_certs', 'dim': 'completeness', 'fact': '证书情况', 'gain': (3, 4),
        'when': lambda c: not c['sections'].get('certs'),
        'q': '有没有证书或在考的证书？（CKA / RHCE / 软考 / 英语等级都算）',
        'why': '"硬性信息完整"里证书是加分项，应届生尤其看重。',
        'chips': ['有 CKA/CKS', '有 RHCE/RHCSA', '在考，预计近期拿证', '暂时没有'],
    },
    {
        'id': 'no_eta', 'dim': 'completeness', 'fact': '到岗时间', 'gain': (2, 4),
        'when': lambda c: not c['sections'].get('intent')
        and not re.search(r'到岗|可实习|实习时长|每周|入职时间', c['text']),
        'q': '最快什么时候能到岗？每周能实习几天、能持续多久？',
        'why': '实习岗 HR 第一眼看的就是到岗时间，缺失会被直接跳过。',
        'chips': ['随时到岗，可长期', '一周内到岗', '需一周内安排，每周 5 天'],
    },
    {
        'id': 'no_contact', 'dim': 'completeness', 'fact': '联系方式', 'gain': (2, 3),
        'when': lambda c: not c['met'].get('has_phone') and not c['met'].get('has_email'),
        'q': '简历里没有看到手机号或邮箱，是否需要补上？（不用填真实号码，写"有"即可）',
        'why': '联系方式缺失时 HR 无法联系，属于硬性信息缺失。',
        'chips': ['有手机号', '有邮箱', '两者都有'],
    },
    {
        'id': 'self_eval_bloat', 'dim': 'details', 'fact': '自我评价', 'gain': (2, 3),
        'when': lambda c: (c['met'].get('self_eval_chars') or 0) > 150,
        'q': '自我评价写了较长一段，是否愿意压缩成 2 行、把篇幅让给项目经历？',
        'why': '应届生简历建议 1 页，自我评价空泛会挤掉更有价值的项目篇幅。',
        'chips': ['可以删掉', '压缩成 2 行', '想保留'],
    },
]

MAX_Q = 5


def _jd_gap_question(ctx):
    cov = ctx['met'].get('jd_coverage') or {}
    miss = [x for x in (cov.get('missing') or []) if x][:5]
    if not miss:
        return None
    return 'JD 里提到了「%s」，你有相关经历吗？（有一条能写进项目就够）' % '、'.join(miss)


def build(ctx):
    """返回问题列表（最多 5 条，按预期收益降序）。"""
    out = []
    for r in RULES:
        try:
            if not bool(r['when'](ctx)):
                continue
        except Exception:
            continue
        q = r['q']
        if q is None:
            q = _jd_gap_question(ctx)
            if not q:
                continue
        lo, hi = r['gain']
        out.append({
            'id': r['id'], 'dim': r['dim'], 'fact': r['fact'],
            'q': q, 'why': r['why'],
            'gain': '+%d~%d 分' % (lo, hi), 'gain_lo': lo, 'gain_hi': hi,
            'chips': list(r.get('chips') or []),
        })
        if len(out) >= MAX_Q:
            break
    out.sort(key=lambda x: -x['gain_hi'])
    return out


def facts(questions, answers):
    """把作答整理成「用户确认事实」；跳过/否定回答不算事实。"""
    facts, neg = [], []
    amap = {q['id']: q for q in questions}
    for qid, val in (answers or {}).items():
        val = (val or '').strip()
        if not val or qid not in amap:
            continue
        q = amap[qid]
        if any(n in val for n in NEGATIVE) and len(val) <= 12:
            neg.append('%s：确认没有（%s）' % (q['fact'], val))
            continue
        facts.append('%s：%s' % (q['fact'], val))
    return facts, neg


def summarize(questions, answers):
    """给报告用的摘要：答了几题、跳了几题、覆盖哪些维度。"""
    answers = answers or {}
    answered = [q for q in questions if (answers.get(q['id']) or '').strip()]
    return {
        'asked': len(questions),
        'answered': len(answered),
        'dims': sorted({q['dim'] for q in answered}),
    }
