# -*- coding: utf-8 -*-
"""简历结构化解析 + 确定性指标（不依赖大模型，用于给 LLM 提供客观证据）。"""

import re

# 小节标题词 → 标准名
SECTION_PATTERNS = [
    ('education', ['教育背景', '教育经历', '学习经历', '教育', '学历']),
    ('skills', ['专业技能', '技能清单', '技能', '技术栈', '专业能力', 'IT技能']),
    ('projects', ['项目经历', '项目经验', '项目实践', '实践经历', '主要项目']),
    ('experience', ['实习经历', '工作经历', '实习经验', '工作经验', '职业经历']),
    ('certs', ['证书', '荣誉', '获奖', '奖项', '技能证书', '证书与荣誉']),
    ('self_eval', ['自我评价', '个人评价', '自我介绍', '个人总结']),
    ('intent', ['求职意向', '期望职位', '目标岗位']),
    ('campus', ['校园经历', '社团经历', '学生工作', '社会实践']),
]

SECTION_LABEL = {
    'education': '教育背景', 'skills': '专业技能', 'projects': '项目经历',
    'experience': '实习/工作经历', 'certs': '证书荣誉', 'self_eval': '自我评价',
    'intent': '求职意向', 'campus': '校园/社会实践', 'other': '其他',
}

HEADING_RE = re.compile(
    r'^\s*(?:[一二三四五六七八九十]+[、.．)）]|\d+[、.．)）]|[-*•·]|\【|\[)?\s*'
    r'(教育背景|教育经历|学习经历|教育情况|'
    r'专业技能|技能清单|技能特长|技术栈|专业能力|IT技能|个人技能|技能|'
    r'项目经历|项目经验|项目实践|实践经历|主要项目|项目|'
    r'实习经历|工作经历|实习经验|工作经验|职业经历|实践经历|'
    r'证书|荣誉|获奖情况|奖项|技能证书|证书与荣誉|证书荣誉|证书及荣誉|荣誉证书|'
    r'自我评价|个人评价|自我介绍|个人总结|个人简介|'
    r'求职意向|期望职位|目标岗位|'
    r'校园经历|社团经历|学生工作|社会实践|校园活动|'
    r'基本信息|个人信息|联系方式|个人信息及联系方式)'
    r'\s*(?:(?:[:：])\s*(.*))?$')
QUANT_RE = re.compile(
    r'\d+(?:\.\d+)?\s*(?:%|％|倍|万|千|亿|个|人|台|套|次|条|篇|项|家|天|小时|分钟|秒|ms|'
    r'GB|TB|MB|QPS|TPS|核|节点|台机器|并发|用户|行代码)?')
PHONE_RE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
IDCARD_RE = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
ADDRESS_RE = re.compile(r'(?:[\u4e00-\u9fa5]{2,8}(?:省|市|区|县))[\u4e00-\u9fa5\d]{0,20}?(?:\d+号|小区|路|街|巷|栋|号楼|室)')
DATE_RANGE_RE = re.compile(r'20\d{2}\s*[.\-/年]\s*(?:0?[1-9]|1[0-2])?\s*[月.\-/]?\s*(?:[-–—~至]|至今)?\s*(?:20\d{2}\s*[.\-/年]?\s*(?:0?[1-9]|1[0-2])?|至今)?')
DATE_ANY_RE = re.compile(r'20\d{2}\s*[.\-/年]\s*(?:0?[1-9]|1[0-2])')
VAGUE_WORDS = ['熟悉', '了解', '掌握', '参与', '负责', '接触', '有一定', '较强', '良好',
               '认真学习', '努力学习', '热爱', '吃苦耐劳', '团队精神', '沟通能力']
ACTION_WORDS = ['负责', '主导', '设计', '搭建', '实现', '优化', '重构', '开发', '部署',
                '排查', '定位', '推动', '落地', '监控', '上线', '自动化', '编写']
RESULT_WORDS = ['提升', '降低', '减少', '提高', '节省', '缩短', '达到', '增长', '支撑',
                '承载', '稳定', '通过率', '准确率', '响应时间', '故障率']
STAR_WORDS = ['背景', '目标', '任务', '挑战', '方案', '行动', '结果', '收益', '效果', '复盘']


def _norm_line(line):
    return line.strip().replace('｜', '|').replace('\u3000', ' ')


def split_sections(text):
    """按行切分小节；返回 {section: text}，未识别内容归入 other。"""
    lines = [_norm_line(l) for l in text.splitlines()]
    sections = {'other': []}
    cur = 'other'
    for line in lines:
        if not line:
            sections.setdefault(cur, []).append('')
            continue
        stripped = re.sub(r'^[#\-*•·●◆■□▪\s]+', '', line)
        m = HEADING_RE.match(line)
        is_heading = bool(m) and (len(m.group(1)) <= 12) and len(stripped) <= 40
        if not is_heading:
            # 形如 "技能：xxx" 的短行，也当作小节起点
            m2 = re.match(r'^(教育背景|专业技能|技能|项目经历|项目经验|实习经历|工作经历|'
                          r'自我评价|证书|荣誉|求职意向|个人信息)\s*[:：]', line)
            if m2:
                cur = _section_key(m2.group(1))
                rest = line[m2.end():].strip()
                sections.setdefault(cur, [])
                if rest:
                    sections[cur].append(rest)
                continue
        if is_heading:
            key = _section_key(m.group(1))
            cur = key
            sections.setdefault(cur, [])
            rest = (m.group(2) or '').strip()
            if rest:
                sections[cur].append(rest)
            continue
        sections.setdefault(cur, []).append(line)
    return {k: '\n'.join(v).strip() for k, v in sections.items()}


def _section_key(name):
    for key, words in SECTION_PATTERNS:
        for w in words:
            if name == w or name.startswith(w) or w in name:
                return key
    return 'other'


def metrics(text, sections, jd_keywords=None):
    """确定性客观指标。"""
    body = text
    exp_text = '\n'.join(sections.get(k, '') for k in ('projects', 'experience', 'campus'))
    chars = len(re.sub(r'\s', '', body))
    lines = [l for l in body.splitlines() if l.strip()]
    # 去掉日期片段，避免把 2024.07 当成"量化数据"
    body_no_date = re.sub(r'20\d{2}\s*[.\-/年]\s*\d{0,2}\s*[月.\-/]?\s*(?:[-–—~至]\s*)?'
                          r'(?:20\d{2}\s*[.\-/年]?\s*\d{0,2}\s*[月]?|至今)?', ' ', body)
    exp_no_date = re.sub(r'20\d{2}\s*[.\-/年]\s*\d{0,2}\s*[月.\-/]?\s*(?:[-–—~至]\s*)?'
                         r'(?:20\d{2}\s*[.\-/年]?\s*\d{0,2}\s*[月]?|至今)?', ' ', exp_text)
    quant_all = QUANT_RE.findall(body_no_date)
    quant_exp = QUANT_RE.findall(exp_no_date)
    # 过滤掉年份类数字
    quant_exp_clean = [q for q in quant_exp
                       if not re.fullmatch(r'20\d{2}', q.strip()) and q.strip()]
    found = [k for k in sections if sections.get(k) and k != 'other']
    missing = [k for k in ('education', 'skills', 'projects', 'experience', 'certs')
               if k not in found]
    date_hits = DATE_ANY_RE.findall(body)
    bullets = len([l for l in lines if re.match(r'^\s*[-•·●◆■□▪*]', l)])
    vague_hits = {w: body.count(w) for w in VAGUE_WORDS if body.count(w)}
    action_hits = {w: exp_text.count(w) for w in ACTION_WORDS if exp_text.count(w)}
    result_hits = {w: exp_text.count(w) for w in RESULT_WORDS if exp_text.count(w)}
    star_hits = {w: body.count(w) for w in STAR_WORDS if body.count(w)}
    self_eval = sections.get('self_eval', '')
    coverage = None
    if jd_keywords:
        low = body.lower()
        hit = [k for k in jd_keywords if k.lower() in low]
        miss = [k for k in jd_keywords if k.lower() not in low]
        coverage = {
            'total': len(jd_keywords),
            'hit': hit,
            'missing': miss,
            'rate': round(len(hit) / len(jd_keywords), 3) if jd_keywords else 0,
        }
    return {
        'chars': chars,
        'est_pages': round(chars / 1100, 1),
        'line_count': len(lines),
        'bullet_count': bullets,
        'sections_found': [SECTION_LABEL.get(k, k) for k in found],
        'sections_missing': [SECTION_LABEL.get(k, k) for k in missing],
        'quant_count_all': len(quant_all),
        'quant_count_exp': len(quant_exp_clean),
        'quant_samples': quant_exp_clean[:8],
        'date_count': len(date_hits),
        'has_email': bool(EMAIL_RE.search(body)),
        'has_phone': bool(PHONE_RE.search(body)),
        'vague_words': vague_hits,
        'action_words': action_hits,
        'result_words': result_hits,
        'star_words': star_hits,
        'self_eval_chars': len(re.sub(r'\s', '', self_eval)),
        'jd_coverage': coverage,
    }


def resume_blocks(text, sections):
    """给 LLM 阅读的精简结构（避免超长导致成本/延迟）。"""
    order = ['intent', 'education', 'skills', 'experience', 'projects', 'certs',
             'campus', 'self_eval', 'other']
    out = []
    for k in order:
        v = sections.get(k, '').strip()
        if not v:
            continue
        out.append('【%s】\n%s' % (SECTION_LABEL.get(k, k), v))
    return '\n\n'.join(out)


def parse(text):
    sections = split_sections(text)
    return {
        'sections': sections,
        'blocks': resume_blocks(text, sections),
    }
