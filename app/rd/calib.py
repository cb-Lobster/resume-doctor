# -*- coding: utf-8 -*-
"""人工评分校准 + 建议采纳统计。

校准口径（都写进看板显示，避免"看起来像玄学"）：
- MAE      平均绝对偏差 = mean(|agent - human|)，越小越准
- 命中率    |偏差| <= 5 分的版本占比
- Pearson r 相关系数，样本 < 3 不计算（避免 1-2 个点算出一个漂亮数字）
- 维度偏差  mean(agent_dim - human_dim)，> +2 视为"系统性偏高"
"""

import math

from . import db

DIM_META = [('jd_match', 'JD 关键词匹配', 25), ('quantified', '成果量化', 20),
            ('structure', '结构与重点', 15), ('star', '表达质量（STAR）', 15),
            ('completeness', '硬性信息完整', 15), ('details', '细节规范', 10)]


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return round(sxy / math.sqrt(sxx * syy), 3)


def calibration(user_id):
    versions = db.list_versions(user_id, limit=100)
    rows = []
    for v in versions:
        hs = db.get_hr_score(v['id'])
        if not hs:
            continue
        diff = round((v['total'] or 0) - hs['total'], 1)
        rows.append({
            'version_id': v['id'], 'seq': v['seq'], 'label': v.get('label') or v.get('filename'),
            'created_at': v['created_at'], 'agent': v['total'], 'human': hs['total'],
            'diff': diff, 'abs_diff': abs(diff), 'in5': abs(diff) <= 5,
            'reviewer': hs.get('reviewer') or '', 'note': hs.get('note') or '',
            'has_dims': bool(hs.get('dims')),
            'agent_dims': {d['key']: d['score'] for d in (v.get('dims') or []) if isinstance(d, dict) and d.get('key')},
            'human_dims': hs.get('dims') or {},
        })

    n = len(rows)
    mae = round(sum(r['abs_diff'] for r in rows) / n, 2) if n else None
    hit = round(sum(1 for r in rows if r['in5']) / n, 3) if n else None
    r_val = _pearson([float(r['agent']) for r in rows], [float(r['human']) for r in rows]) if n >= 3 else None

    dim_bias = []
    for key, name, mx in DIM_META:
        deltas = [r['agent_dims'][key] - r['human_dims'][key]
                  for r in rows
                  if key in r['agent_dims'] and isinstance(r['human_dims'].get(key), (int, float))]
        if deltas:
            bias = round(sum(deltas) / len(deltas), 2)
        else:
            bias = None
        dim_bias.append({'key': key, 'name': name, 'max': mx, 'bias': bias,
                         'sample': len(deltas),
                         'flag': '偏高' if (bias is not None and bias > 2 and len(deltas) >= 2) else
                                 ('偏低' if (bias is not None and bias < -2 and len(deltas) >= 2) else '')})

    advice = []
    if n == 0:
        advice.append('还没有人工评分样本：先在下面给任意一个版本打分，指标才会出现。')
    elif n < 3:
        advice.append('样本 %d 个，Pearson 相关系数需 ≥3 个样本才有统计意义（当前显示"样本不足"）。' % n)
    else:
        if mae is not None and mae > 8:
            advice.append('MAE %.1f 分偏大：Agent 与人工口径差异明显，建议先对齐"成果量化"的评分尺度。' % mae)
        if r_val is not None and r_val < 0.6:
            advice.append('相关系数 %.2f 偏低：打分排序与人判不一致，检查是否被"字数多"带偏。' % r_val)
    for d in dim_bias:
        if d['bias'] is not None and abs(d['bias']) > 2 and d['sample'] >= 2:
            advice.append('%s 维度%s %.1f 分（%d 个样本）：可在 kb/rubric.md 里收紧该维度档位描述。'
                          % (d['name'], '系统性偏高' if d['bias'] > 0 else '系统性偏低',
                             abs(d['bias']), d['sample']))

    return {
        'rows': rows, 'n': n, 'mae': mae, 'hit5': hit, 'corr': r_val,
        'corr_ok': r_val is not None, 'dim_bias': dim_bias, 'advice': advice,
    }


def adoption_board(user_id):
    versions = db.list_versions(user_id, limit=100)
    versions.sort(key=lambda v: v['seq'])
    total_items, adopted_items = 0, 0
    per_version = []
    for v in versions:
        ad = db.adoptions(v['id'])
        n_items = len((v.get('deductions') or []))
        n_adopt = sum(1 for k, val in ad.items() if val and k.startswith('ded_'))
        total_items += n_items
        adopted_items += min(n_adopt, n_items) if n_items else 0
        per_version.append({'version_id': v['id'], 'seq': v['seq'], 'total': v['total'],
                            'items': n_items, 'adopted': n_adopt,
                            'label': v.get('label') or v.get('filename')})

    # 采纳后提分：拿"上一版有采纳记录"的版本对做 delta 对比
    lift_rows = []
    for i in range(1, len(versions)):
        prev, cur = versions[i - 1], versions[i]
        ad = db.adoptions(prev['id'])
        n_adopt = sum(1 for k, val in ad.items() if val and k.startswith('ded_'))
        if not ad:
            continue
        lift_rows.append({'from_seq': prev['seq'], 'to_seq': cur['seq'],
                          'adopted': n_adopt,
                          'delta': (cur['total'] or 0) - (prev['total'] or 0)})

    with_adopt = [r['delta'] for r in lift_rows if r['adopted'] > 0]
    no_adopt = [r['delta'] for r in lift_rows if r['adopted'] == 0]
    lift = {
        'with_adopt_avg': round(sum(with_adopt) / len(with_adopt), 1) if with_adopt else None,
        'no_adopt_avg': round(sum(no_adopt) / len(no_adopt), 1) if no_adopt else None,
        'samples': len(lift_rows),
    }

    rate = round(adopted_items / total_items, 3) if total_items else None
    advice = []
    if total_items == 0:
        advice.append('还没有扣分点记录：先做一次诊断。')
    elif rate is not None and rate < 0.3:
        advice.append('采纳率 %d%% 偏低：建议检查扣分点写得是否可执行（"怎么改"那一栏）。' % round(rate * 100))
    if lift['with_adopt_avg'] is not None and lift['no_adopt_avg'] is not None:
        gap = lift['with_adopt_avg'] - lift['no_adopt_avg']
        advice.append('采纳建议的版本平均提分 %.1f，未采纳的 %.1f，差 %.1f 分——这就是建议有效性的直接证据。'
                      % (lift['with_adopt_avg'], lift['no_adopt_avg'], gap))

    return {
        'items_total': total_items, 'items_adopted': adopted_items, 'rate': rate,
        'per_version': per_version, 'lift': lift, 'lift_rows': lift_rows, 'advice': advice,
    }
