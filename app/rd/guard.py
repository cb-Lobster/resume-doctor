# -*- coding: utf-8 -*-
"""安全护栏：隐私检测 + 改写"禁止虚构"校验。"""

import re

IDCARD = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
PHONE = re.compile(r'(?<!\d)1[3-9]\d{9}(?!\d)')
EMAIL = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+')
BANK = re.compile(r'(?<!\d)\d{16,19}(?!\d)')
ADDRESS = re.compile(
    r'[\u4e00-\u9fa5]{2,10}(?:省|市)[\u4e00-\u9fa5]{0,10}(?:区|县)[\u4e00-\u9fa5\d]{0,20}'
    r'(?:\d+号|\d+弄|小区|路|街|巷|栋|号楼|室)')
NUM_TOKEN = re.compile(r'\d+(?:\.\d+)?\s*(?:%|％|倍|万|千|亿|个|人|台|套|次|条|项|天|'
                       r'小时|分钟|秒|ms|GB|TB|MB|核|节点|QPS|TPS)?')
CERT_WORDS = ['CKA', 'CKS', 'CET-6', 'CET6', 'CET-4', 'CET4', 'PMP', '软考', '四级', '六级',
              '华为认证', 'HCIA', 'HCIP', 'RHCE', 'AWS', 'ACP', '雅思', '托福']


def pii_scan(text):
    """返回隐私风险列表。"""
    out = []
    if IDCARD.search(text):
        out.append({'type': '身份证号', 'level': 'high',
                    'snippet': IDCARD.search(text).group(0)[:6] + '****',
                    'advice': '简历不应出现身份证号，请直接删除。'})
    if BANK.search(text) and not IDCARD.search(text):
        m = BANK.search(text)
        out.append({'type': '疑似银行卡/长串数字', 'level': 'high',
                    'snippet': m.group(0)[:4] + '****',
                    'advice': '删除银行卡号等账户信息。'})
    if ADDRESS.search(text):
        out.append({'type': '详细住址', 'level': 'medium',
                    'snippet': ADDRESS.search(text).group(0)[:20],
                    'advice': '住址精确到"城市"即可，门牌号建议删除。'})
    return out


def fabricate_check(original, rewritten, keywords=None, context=None):
    """改写稿是否引入了原文没有的数字/证书/技术名词。

    context：用户简历全文（用于判断"这个词/数字是否在简历别处出现过"）。
    """
    base = context if context else original
    risks = []
    o_nums = set(n.strip() for n in NUM_TOKEN.findall(base))
    r_nums = set(n.strip() for n in NUM_TOKEN.findall(rewritten))
    new_nums = [n for n in r_nums - o_nums if re.search(r'\d', n)]
    # 过滤掉占位符里的提示文字
    new_nums = [n for n in new_nums if not re.fullmatch(r'\d{4}', n)]
    if new_nums:
        risks.append({'type': '新增数字', 'items': sorted(new_nums)[:6],
                      'advice': '改写稿出现了简历里没有的数字，需你确认真实性；否则改为占位符。'})
    o_up = base.upper()
    r_up = rewritten.upper()
    new_certs = [c for c in CERT_WORDS if c in r_up and c not in o_up]
    if new_certs:
        risks.append({'type': '新增证书/资质', 'items': new_certs,
                      'advice': '改写稿新增了简历里没有的证书，必须删除或由你确认。'})
    new_terms = []
    for k in (keywords or []):
        k = (k or '').strip()
        # 只对"技术专有名词"（含英文字母/数字，如 K8s、tcpdump、CI/CD）做新增检测，
        # 避免把"云主机""监控"这类通用中文词误判成虚构。
        if len(k) < 2 or not re.search(r'[A-Za-z0-9]', k):
            continue
        if k.lower() in rewritten.lower() and k.lower() not in base.lower():
            new_terms.append(k)
    if new_terms:
        risks.append({'type': '新增技能/工具', 'items': new_terms[:8],
                      'advice': '改写稿出现了简历里没有的技术名词：若你确实没用过请删除，只有真实掌握的技能才能写进简历。'})
    return risks, bool(risks)


def placeholder_check(rewritten):
    """检查是否使用了【请补充：...】占位符。"""
    return re.findall(r'【请补充[:：]?[^】]*】', rewritten)
