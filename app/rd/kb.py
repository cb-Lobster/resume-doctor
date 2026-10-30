# -*- coding: utf-8 -*-
"""本地知识库 + BM25 检索（零依赖）。kb/*.md 按二级标题切片。"""

import math
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KB_DIR = os.path.join(ROOT, 'kb')
LATIN_RE = re.compile(r'[a-zA-Z][a-zA-Z0-9+#./-]{1,}')
CJK_RE = re.compile(r'[\u4e00-\u9fa5]')


def tokenize(text):
    text = text or ''
    toks = [w.lower() for w in LATIN_RE.findall(text)]
    cjk = ''.join(CJK_RE.findall(text))
    toks += [cjk[i:i + 2] for i in range(max(0, len(cjk) - 1))]
    return toks


class KB:
    def __init__(self, kb_dir=KB_DIR):
        self.docs = []      # [{file, title, text, toks}]
        self._load(kb_dir)
        self._build_index()

    def _load(self, kb_dir):
        if not os.path.isdir(kb_dir):
            return
        for fn in sorted(os.listdir(kb_dir)):
            if not fn.endswith('.md'):
                continue
            with open(os.path.join(kb_dir, fn), 'r', encoding='utf-8-sig') as f:
                raw = f.read()
            parts = re.split(r'(?m)^##\s+', raw)
            head = parts[0].strip()
            if head:
                self.docs.append({'file': fn, 'title': '总述', 'text': head})
            for p in parts[1:]:
                lines = p.split('\n')
                self.docs.append({'file': fn, 'title': lines[0].strip(),
                                  'text': '\n'.join(lines[1:]).strip()})
        for d in self.docs:
            d['toks'] = tokenize(d['title'] + '\n' + d['text'])
            d['len'] = len(d['toks'])

    def _build_index(self):
        self.df = {}
        for d in self.docs:
            for t in set(d['toks']):
                self.df[t] = self.df.get(t, 0) + 1
        self.N = max(1, len(self.docs))
        self.avg = sum(d['len'] for d in self.docs) / self.N if self.docs else 1

    def search(self, query, top=3, file_filter=None):
        q = set(tokenize(query))
        if not q:
            return []
        scored = []
        k1, b = 1.5, 0.75
        for d in self.docs:
            if file_filter and d['file'] != file_filter:
                continue
            tf = {}
            for t in d['toks']:
                if t in q:
                    tf[t] = tf.get(t, 0) + 1
            if not tf:
                continue
            s = 0.0
            for t, f in tf.items():
                idf = math.log(1 + (self.N - self.df.get(t, 0) + 0.5) / (self.df.get(t, 0) + 0.5))
                s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * d['len'] / self.avg))
            scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return [{'title': d['title'], 'file': d['file'], 'text': d['text'],
                 'score': round(s, 3)} for s, d in scored[:top]]

    def rubric(self, dim_key):
        """按维度名取评分细则段落。"""
        alias = {
            'jd_match': 'JD 关键词匹配',
            'quantified': '成果量化',
            'structure': '结构与重点',
            'star': '表达质量',
            'completeness': '硬性信息完整',
            'details': '细节规范',
        }
        name = alias.get(dim_key, dim_key)
        for d in self.docs:
            if d['file'] == 'rubric.md' and name in d['title']:
                return d['text']
        return self.search(name, top=1, file_filter='rubric.md')[0]['text'] \
            if self.search(name, top=1, file_filter='rubric.md') else ''

    def samples(self, query, top=2):
        return self.search(query, top=top, file_filter='samples.md')

    def guardrails(self):
        return '\n'.join(d['text'] for d in self.docs if d['file'] == 'guardrails.md')

    def competency(self, query, top=2):
        return self.search(query, top=top, file_filter='competency.md')


_kb = None


def get_kb():
    global _kb
    if _kb is None:
        _kb = KB()
    return _kb
