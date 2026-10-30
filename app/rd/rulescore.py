# -*- coding: utf-8 -*-
"""离线兜底规则引擎。

用途：教师机断网 / 没配 Key / 接口被墙时，保证演示流程仍然完整可跑。
特点：全部确定性规则，不编造任何经历；改写只给"句式框架 + 占位符"。
报告里会标记 engine=offline，前端顶部显示"离线演示模式"。
"""

import re

from . import resume, trace

VOCAB = [
    'Kubernetes', 'K8s', 'Docker', 'Containerd', 'Linux', 'Shell', 'Python', 'Go',
    'Nginx', 'Apache', 'MySQL', 'PostgreSQL', 'Redis', 'MongoDB', 'ELK', 'Kafka',
    'Prometheus', 'Grafana', 'Zabbix', 'Jenkins', 'GitLab', 'Git', 'Ansible',
    'Terraform', 'Helm', 'Istio', 'VMware', 'Ceph', 'NFS', 'LVS', 'Keepalived',
    'HAProxy', 'TCP/IP', 'DNS', 'HTTP', 'SSH', 'VPN', '阿里云', '腾讯云', '华为云',
    'AWS', 'Azure', '云原生', '边缘计算', 'KubeEdge', '信创', '麒麟', '统信',
    '运维', '监控', '告警', '容器', '集群', '高可用', '负载均衡', '自动化',
    '故障排查', '性能调优', '日志', '备份', '安全加固', 'CI/CD', '虚拟化',
    '中间件', '数据库', '脚本', '值班', '部署', 'CKA', 'CKS', 'RHCE', 'RHCSA',
]

REQ_RE = re.compile(r'(要求|熟悉|掌握|了解|具备|优先|加分|至少|能够|负责|参与|协助)')
BULLET_RE = re.compile(r'^\s*[-•·●◆■□▪*]\s*')


def extract_jd_rules(jd_text):
    """不调模型，从 JD 原文抽结构化要求。"""
    text = jd_text or ''
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    title = '目标岗位'
    for line in lines[:8]:
        if re.search(r'(工程师|运维|开发|实习|岗|职位|专员|助理)', line) and len(line) <= 30:
            title = line.strip('：#* ')
            break
    else:
        if lines:
            title = lines[0][:24]

    low = text.lower()
    keywords, seen = [], set()
    for w in VOCAB:
        if w.lower() in low and w.lower() not in seen:
            keywords.append(w)
            seen.add(w.lower())
    for w in re.findall(r'[A-Za-z][A-Za-z0-9+#./-]{1,}', text):
        w = w.strip('.,;:')
        if len(w) > 1 and w.lower() not in seen and w.lower() not in ('the', 'and', 'with'):
            keywords.append(w)
            seen.add(w.lower())
    keywords = keywords[:25]

    reqs, duties = [], []
    for line in lines:
        clean = BULLET_RE.sub('', line).strip()
        if not clean or len(clean) > 60:
            continue
        if re.search(r'(要求|熟悉|掌握|了解|具备|优先|加分|至少)', clean):
            reqs.append(clean[:30])
        elif re.search(r'(负责|参与|协助|工作内容|岗位职责)', clean):
            duties.append(clean[:30])

    seniority = '实习' if '实习' in text else ('校招' if '应届' in text else '社招')
    return {
        'title': title, 'seniority': seniority,
        'hard_requirements': reqs[:12], 'plus_points': [],
        'keywords': keywords, 'responsibilities': duties[:10],
    }


def _clamp(v, mx):
    return max(0.0, min(float(mx), float(v)))


def _evidence_lines(sections, keys, limit=3):
    """从简历里挑真实原文行作为证据（只挑有内容的行）。"""
    out = []
    for k in keys:
        for line in (sections.get(k) or '').splitlines():
            s = BULLET_RE.sub('', line).strip()
            if 8 <= len(s) <= 80:
                out.append(s)
            if len(out) >= limit:
                return out
    return out


def score(parsed, jd, met, facts=None):
    sections = parsed.get('sections') or {}
    facts = facts or []
    hit_fact = ' '.join(facts)

    cov = met.get('jd_coverage') or {}
    rate = float(cov.get('rate') or 0)
    found = set(met.get('sections_found') or [])

    dims = []

    # 1 JD 关键词匹配（25）
    s1 = 25 * (0.15 + 0.85 * min(1.0, rate))
    if hit_fact:
        s1 += 2
    s1 = _clamp(s1, 25)
    dims.append({
        'key': 'jd_match', 'score': round(s1, 1),
        'reason': 'JD 关键词命中 %d/%d（覆盖率 %d%%），命中项会被 HR 在初筛前几秒扫到，未命中的都是丢分点。'
                  % (len(cov.get('hit') or []), cov.get('total') or 0, round(rate * 100)),
        'evidence': ['、'.join((cov.get('hit') or [])[:8])] if (cov.get('hit') or []) else
                    ['简历文本里没有出现 JD 的关键技术名词'],
        'fix': '把 JD 里出现、你也真实用过的词写进项目描述（不要只堆在技能栏）。',
    })

    # 2 成果量化（20）
    n = int(met.get('quant_count_exp') or 0)
    if n == 0:
        s2 = 4
    elif n <= 2:
        s2 = 9
    elif n <= 5:
        s2 = 14
    elif n <= 9:
        s2 = 18
    else:
        s2 = 19
    if hit_fact and n == 0:
        s2 = max(s2, 8)
    dims.append({
        'key': 'quantified', 'score': round(_clamp(s2, 20), 1),
        'reason': '项目/实习段落里可识别的量化数字 %d 处。%s'
                  % (n, '几乎没有数字，HR 无法判断你做成了什么。' if n <= 2 else
                     '有数字，但需要确认是否写清了规模与结果两个维度。'),
        'evidence': (met.get('quant_samples') or [])[:3] or
                    (_evidence_lines(sections, ['projects', 'experience'], 2) or ['（未识别到项目/实习段落）']),
        'fix': '每条经历补一个数字：规模（几台/几人）、效果（百分比）、结果（故障率/耗时）。',
    })

    # 3 结构与重点（15）
    core = ['education', 'skills', 'projects', 'experience', 'certs']
    nfound = len([c for c in core if any(c in f or resume.SECTION_LABEL.get(c, '') in f for f in found)])
    s3 = 15 * (0.35 * (nfound / 5.0)
               + 0.30 * (1.0 if ('项目经历' in found or '实习/工作经历' in found) else 0.0)
               + 0.20 * min(1.0, (met.get('bullet_count') or 0) / 8.0)
               + 0.15 * (1.0 if '求职意向' in found else 0.0))
    dims.append({
        'key': 'structure', 'score': round(_clamp(s3, 15), 1),
        'reason': '识别到 %d/5 个核心小节（教育/技能/项目/实习/证书），要点 %d 条。%s'
                  % (nfound, met.get('bullet_count') or 0,
                     '缺少：' + '、'.join(met.get('sections_missing') or []) if met.get('sections_missing') else '模块齐全。'),
        'evidence': ['识别到的小节：' + '、'.join(found)] if found else ['未识别到标准小节标题'],
        'fix': '补齐缺失小节，并把与岗位最相关的项目放到最前面。',
    })

    # 4 表达质量（15）
    act = sum((met.get('action_words') or {}).values())
    res = sum((met.get('result_words') or {}).values())
    s4 = 15 * (0.25 + 0.50 * min(1.0, act / 6.0) + 0.25 * min(1.0, res / 3.0))
    dims.append({
        'key': 'star', 'score': round(_clamp(s4, 15), 1),
        'reason': '动作类动词 %d 处、结果类描述 %d 处。%s'
                  % (act, res, '只有动作没有结果，读起来像"职责罗列"。' if res == 0 else
                     '有结果描述，继续把情境和任务补完整即可。'),
        'evidence': _evidence_lines(sections, ['projects', 'experience'], 2) or ['（未识别到经历段落）'],
        'fix': '每条经历按「情境-任务-行动-结果」重写，用动词开头、单条一行。',
    })

    # 5 硬性信息完整（15）
    items, miss = 0, []
    for key, label in (('education', '教育背景'), ('skills', '技能清单'),
                       ('certs', '证书荣誉'), ('intent', '求职意向/到岗时间')):
        if label in found:
            items += 1
        else:
            miss.append(label)
    if met.get('has_phone') or met.get('has_email'):
        items += 1
    else:
        miss.append('联系方式')
    s5 = 15 * (items / 5.0)
    if hit_fact:
        s5 += 1.5
    dims.append({
        'key': 'completeness', 'score': round(_clamp(s5, 15), 1),
        'reason': '硬性信息齐全 %d/5 项。%s' % (items, ('缺少：' + '、'.join(miss)) if miss else '信息完整。'),
        'evidence': ['缺少：' + '、'.join(miss)] if miss else ['五项硬性信息齐全'],
        'fix': '缺什么补什么，尤其"到岗时间"和"联系方式"是实习生简历的必填项。',
    })

    # 6 细节规范（10）
    chars = int(met.get('chars') or 0)
    vague = sum((met.get('vague_words') or {}).values())
    s6 = 7.0
    if 600 <= chars <= 1800:
        s6 += 1.2
    elif chars > 2400:
        s6 -= 1.5
    if (met.get('bullet_count') or 0) >= 8:
        s6 += 0.8
    if vague >= 8:
        s6 -= 2.0
    elif vague >= 4:
        s6 -= 1.0
    dims.append({
        'key': 'details', 'score': round(_clamp(s6, 10), 1),
        'reason': '正文 %d 字（约 %.1f 页），空泛词 %d 处（熟悉/了解/参与/负责…）。'
                  % (chars, chars / 1100.0, vague),
        'evidence': ['空泛词统计：' + '、'.join('%s×%d' % (k, v) for k, v in
                                          list((met.get('vague_words') or {}).items())[:5])] if vague else
                    ['未检出高频空泛词'],
        'fix': '删掉"吃苦耐劳/团队精神"这类口号，把篇幅换成一个具体项目。',
    })

    # 名称 / 满分 / 占比
    meta = {k: (n, m) for k, n, m in
            [('jd_match', 'JD 关键词匹配', 25), ('quantified', '成果量化', 20),
             ('structure', '结构与重点', 15), ('star', '表达质量（STAR）', 15),
             ('completeness', '硬性信息完整', 15), ('details', '细节规范', 10)]}
    for d in dims:
        name, mx = meta[d['key']]
        d['name'], d['max'] = name, mx
        d['rate'] = round(d['score'] / mx, 3)
    total = int(round(sum(d['score'] for d in dims)))

    # 扣分点：最弱三个维度
    weak = sorted(dims, key=lambda d: d['rate'])[:3]
    advice = {
        'jd_match': ('JD 关键词覆盖率只有 %d%%，把真实用过的技术名写进项目描述。' % round(rate * 100),
                     '把 JD 与简历逐词对照，缺的词要么补进项目，要么承认不会。'),
        'quantified': ('项目经历几乎没有任何数字，无法判断产出。',
                       '每条经历补规模与结果两个数字，例如"3 台机器 / 故障恢复 40 分钟"。'),
        'structure': ('小节不完整或没有把相关经历前置。',
                      '按"求职意向→教育→技能→项目→证书"排序，岗位相关项目放最前。'),
        'star': ('经历写成职责罗列，缺结果与个人贡献边界。',
                 '改成"动词 + 对象 + 方法 + 结果"，每条一行。'),
        'completeness': ((('缺少：' + '、'.join(miss)) if miss else '硬性信息基本齐全，但格式不够规范。'),
                         '按清单补齐，到岗时间写到求职意向里。'),
        'details': ('空泛词多、篇幅与格式需要收拾。',
                    '删口号句，统一时间格式，控制在 1 页。'),
    }
    deductions = []
    for d in weak:
        p, a = advice[d['key']]
        deductions.append({
            'dimension': d['name'], 'problem': p, 'advice': a,
            'impact': '预计提升 %d~%d 分' % (int(d['max'] - d['score']),
                                          int((d['max'] - d['score']) * 0.8 + 3)),
        })

    level = '高度匹配' if rate >= 0.7 else ('基本匹配' if rate >= 0.4 else '偏低')
    one_liner = '总分 %d：%s' % (
        total, '基础扎实，缺的是量化表达' if total >= 75 else
        ('方向对口，但证据不足' if total >= 60 else '结构完整度不够，先补硬性信息'))
    summary = ('离线规则引擎评分：JD 关键词命中 %d%%、量化数字 %d 处、动作词 %d 处、空泛词 %d 处。'
               '总分 %d/100，%s。' % (round(rate * 100), n, act, vague, total,
                                    '主要失分在' + '、'.join(d['name'] for d in weak)) )
    risks = []
    if n <= 1:
        risks.append('"你这些项目具体部署了多少台机器？出过什么故障，怎么定位的？"——没有数字会被连续追问。')
    if res == 0:
        risks.append('"做完之后效果怎么样？"——缺结果描述会被认为只是跟着做了一遍。')
    if not (met.get('has_phone') or met.get('has_email')):
        risks.append('简历没有联系方式，HR 想联系也联系不到。')
    if rate < 0.4:
        risks.append('"JD 要求的 K8s/监控你实际做过吗？"——关键词没覆盖会直接被判定不匹配。')

    return {
        'dims': dims, 'total': total,
        'one_liner': one_liner, 'summary': summary,
        'jd_fit': {'level': level, 'comment': '按 JD 关键词覆盖率 %d%% 判定（规则引擎口径）。' % round(rate * 100)},
        'deduction_top3': deductions,
        'interview_risks': risks[:4] or ['暂无明显疑点。'],
        'next_actions': [a['advice'] for a in deductions][:3],
    }


REWRITE_FRAMES = {
    'quantified': '【动作】负责/搭建/迁移 + 【对象】+ 【规模】__ 台/__ 个节点/__ 用户 + 【方法】使用 __ 工具 + 【结果】__%（如耗时下降/故障率降低）',
    'star': '【情境】在 __ 场景下 + 【任务】需要解决 __ + 【行动】我做了 __（工具/步骤）+ 【结果】最终 __（数字）',
    'jd_match': '把 JD 中的「__」写进项目描述：在 __ 项目中用 __ 完成了 __，而不是只列在技能栏。',
    'structure': '把「__」这一段整体移到项目经历之前，并压缩到 3 行以内。',
    'completeness': '在求职意向里补一行：目标岗位 __ ｜ 到岗时间 __ ｜ 可实习时长 __。',
    'details': '删掉"__"这类口号句，把空出来的 3 行写成一个具体项目。',
}


def rewrites(parsed, jd, met, scored):
    """离线改写：只给句式框架 + 占位符，绝不编造事实。"""
    sections = parsed.get('sections') or {}
    weak = sorted(scored['dims'], key=lambda d: d['rate'])[:2]
    pool = _evidence_lines(sections, ['projects', 'experience', 'campus'], 4)
    out = []
    for i, d in enumerate(weak):
        original = pool[i] if i < len(pool) else '（简历中对应条目未识别到，请在上方粘贴原文）'
        frame = REWRITE_FRAMES.get(d['key'], REWRITE_FRAMES['star'])
        out.append({
            'id': 'r%d' % (i + 1),
            'section': d['name'],
            'original': original,
            'rewritten': frame,
            'reason': '该维度得分 %s/%s，属于当前最薄弱项，按右侧框架重写后预计可提 %d~%d 分。'
                      % (d['score'], d['max'], int(d['max'] - d['score']),
                         int((d['max'] - d['score']) * 0.8)),
            'gaps': ['规模数字', '结果指标'],
            'placeholders': ['__'],
            'fabrication_risks': [],
        })
    return out
