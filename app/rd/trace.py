# -*- coding: utf-8 -*-
"""评分依据溯源（Traceability）。

思路：模型只负责"给分数"，**依据由脚本反查**——把 kb/rubric.md 里的
「## 维度 N：名称（X 分）」和「- a-b：档位描述」解析成区间表，
再用维度分数落到具体档位。这样每条依据都真实存在于知识库原文，
不会被模型编造，也不会出现"分数和依据对不上"。
"""

import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUBRIC_PATH = os.path.join(ROOT, 'kb', 'rubric.md')

DIM_ORDER = ['jd_match', 'quantified', 'structure', 'star', 'completeness', 'details']

_HEAD_RE = re.compile(r'(?m)^##\s*维度\s*(\d+)\s*[：:]\s*(.+?)\s*[（(]\s*(\d+)\s*分\s*[）)]\s*$')
_BAND_RE = re.compile(r'(?m)^\s*[-*]\s*(\d+)\s*[-–—~]\s*(\d+)\s*[：:]\s*(.+?)\s*$')

_cache = None


def _parse(path):
    with open(path, 'r', encoding='utf-8-sig') as f:
        lines = f.read().splitlines()

    heads = []
    for i, line in enumerate(lines):
        m = _HEAD_RE.match(line)
        if m:
            heads.append((i, int(m.group(1)), m.group(2).strip(), int(m.group(3))))

    dims = {}
    for idx, (start, no, name, mx) in enumerate(heads):
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        body = lines[start + 1:end]
        lead = ''
        bands = []
        for line in body:
            bm = _BAND_RE.match(line)
            if bm:
                bands.append({'lo': int(bm.group(1)), 'hi': int(bm.group(2)),
                              'desc': bm.group(3).strip()})
            elif line.strip() and not lead:
                lead = line.strip()
        key = DIM_ORDER[no - 1] if 1 <= no <= len(DIM_ORDER) else 'dim%d' % no
        dims[key] = {
            'key': key, 'no': no, 'name': name, 'max': mx,
            'lead': lead, 'bands': bands,
            'line': start + 1,                       # 1-based 行号，便于跳原文
            'anchor': _anchor(name),
        }
    return dims


def _anchor(name):
    """生成 kb 原文的定位锚点，前端可直接展示成 #维度2-成果量化。"""
    return re.sub(r'[^\w\u4e00-\u9fa5]+', '-', name).strip('-')


def rubric():
    global _cache
    if _cache is None:
        try:
            _cache = _parse(RUBRIC_PATH)
        except Exception:
            _cache = {}
    return _cache


def band_of(dim_key, score):
    """分数 → 命中的档位（区间包含优先；都不包含时取最近档）。"""
    d = rubric().get(dim_key)
    if not d or not d['bands']:
        return None
    s = float(score or 0)
    for b in d['bands']:
        if b['lo'] - 0.001 <= s <= b['hi'] + 0.001:
            return b
    best, bestd = None, 1e9
    for b in d['bands']:
        mid = (b['lo'] + b['hi']) / 2.0
        if abs(mid - s) < bestd:
            best, bestd = b, abs(mid - s)
    return best


def ref(dim_key, score, extra=''):
    """返回可直接塞进报告的依据对象；找不到 rubric 时返回 None（不造假）。"""
    d = rubric().get(dim_key)
    if not d:
        return None
    b = band_of(dim_key, score)
    if not b:
        return None
    clause = '维度 %d · %s · %d-%d 档' % (d['no'], d['name'], b['lo'], b['hi'])
    return {
        'doc': 'kb/rubric.md',
        'dim_key': dim_key,
        'dim_no': d['no'],
        'dim_name': d['name'],
        'max': d['max'],
        'band': '%d-%d' % (b['lo'], b['hi']),
        'clause': clause,
        'quote': b['desc'],                      # 档位原文（真实存在）
        'lead': d['lead'],                       # 该维度的检查要点原文
        'line': d['line'],
        'anchor': d['anchor'],
        'score': score,
        'note': extra,
    }


def name_to_key(name):
    """把模型返回的中文维度名映射回 key（用于给扣分点挂依据）。"""
    if not name:
        return None
    raw = str(name)
    for k, d in rubric().items():
        if d['name'] and (d['name'] in raw or raw in d['name']):
            return k
    alias = {
        'jd_match': ['JD', '关键词', '匹配'],
        'quantified': ['量化', '数字', '成果'],
        'structure': ['结构', '重点', '排版结构'],
        'star': ['STAR', '表达', '句式'],
        'completeness': ['完整', '硬性', '信息'],
        'details': ['细节', '规范', '格式'],
    }
    for k, words in alias.items():
        for w in words:
            if w in raw:
                return k
    return None


def dims_catalog():
    """给前端/健康检查用：六个维度的档位表。"""
    out = []
    for k in DIM_ORDER:
        d = rubric().get(k)
        if not d:
            continue
        out.append({'key': k, 'no': d['no'], 'name': d['name'], 'max': d['max'],
                    'bands': d['bands'], 'line': d['line'], 'anchor': d['anchor']})
    return out
