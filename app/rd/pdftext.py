# -*- coding: utf-8 -*-
"""纯标准库 PDF 文本提取（支持 FlateDecode + 对象流 + Identity-H/ToUnicode CMap）。

只针对"文字型"PDF（Word / LibreOffice / Chrome 导出的简历）。扫描件（图片）需 OCR，
本模块会明确返回空结果并由上层提示。
"""

import re
import zlib


class PdfTextError(Exception):
    pass


def _split_stream(raw):
    i = raw.find(b'stream')
    if i == -1:
        return raw, None
    d = raw[:i]
    j = i + 6
    if raw[j:j + 2] == b'\r\n':
        j += 2
    elif raw[j:j + 1] in (b'\n', b'\r'):
        j += 1
    m = re.search(rb'/Length\s+(\d+)', d)
    if m:
        s = raw[j:j + int(m.group(1))]
    else:
        s = raw[j:raw.rfind(b'endstream')]
    return d, s


def _inflate(b):
    try:
        return zlib.decompress(b)
    except Exception:
        return None


def _balanced(text, pos):
    i, depth = pos + 2, 1
    while i < len(text) and depth:
        if text[i:i + 2] == b'<<':
            depth += 1
            i += 2
        elif text[i:i + 2] == b'>>':
            depth -= 1
            i += 2
        else:
            i += 1
    return text[pos + 2:i - 2]


def _get_dict(text, key):
    for m in re.finditer(rb'/' + key + rb'(?![A-Za-z])', text):
        j = m.end()
        while j < len(text) and text[j:j + 1] in b' \r\n\t':
            j += 1
        if text[j:j + 2] == b'<<':
            return _balanced(text, j)
    return None


def _get_ref(text, key):
    m = re.search(rb'/' + key + rb'\s+(\d+)\s+0\s+R', text)
    return int(m.group(1)) if m else None


def _parse_cmap(txt):
    cmap = {}
    for blk in re.findall(rb'beginbfchar(.*?)endbfchar', txt, re.S):
        for src, dst in re.findall(rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', blk):
            cmap[bytes.fromhex(src.decode())] = bytes.fromhex(dst.decode())
    for blk in re.findall(rb'beginbfrange(.*?)endbfrange', txt, re.S):
        for lo, hi, rest in re.findall(
                rb'<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]+>|\[[^\]]*\])', blk):
            lo_i, hi_i, w = int(lo, 16), int(hi, 16), len(lo) // 2
            if rest.startswith(b'['):
                for k, it in enumerate(re.findall(rb'<([0-9A-Fa-f]+)>', rest)):
                    cmap[(lo_i + k).to_bytes(w, 'big')] = bytes.fromhex(it.decode())
            else:
                base = bytes.fromhex(rest[1:-1].decode())
                bi = int.from_bytes(base, 'big')
                for k in range(hi_i - lo_i + 1):
                    cmap[(lo_i + k).to_bytes(w, 'big')] = (bi + k).to_bytes(len(base), 'big')
    return cmap


def _unescape(s):
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i:i + 1]
        if c == b'\\':
            nxt = s[i + 1:i + 2]
            mp = {b'n': b'\n', b'r': b'\r', b't': b'\t', b'b': b'\b', b'f': b'\f',
                  b'(': b'(', b')': b')', b'\\': b'\\'}
            if nxt in mp:
                out += mp[nxt]
                i += 2
            elif nxt.isdigit():
                j = i + 1
                oct_ = b''
                while j < len(s) and len(oct_) < 3 and s[j:j + 1].isdigit():
                    oct_ += s[j:j + 1]
                    j += 1
                out.append(int(oct_, 8) & 0xFF)
                i = j
            else:
                out += nxt
                i += 2
        else:
            out += c
            i += 1
    return bytes(out)


class _Pdf:
    def __init__(self, data):
        self.data = data
        self.objects = {}
        self.streams = {}
        for m in re.finditer(rb'(?<![0-9])(\d+)\s+0\s+obj', data):
            e = data.find(b'endobj', m.end())
            raw = data[m.end():e if e != -1 else len(data)]
            d, s = _split_stream(raw)
            n = int(m.group(1))
            self.objects[n] = d
            if s is not None and b'FlateDecode' in d:
                inf = _inflate(s)
                if inf is not None:
                    self.streams[n] = inf
        self._expand_objstm()

    def _expand_objstm(self):
        for n in list(self.objects):
            head = self.objects[n]
            if b'/ObjStm' not in head or n not in self.streams:
                continue
            mf = re.search(rb'/First\s+(\d+)', head)
            mn = re.search(rb'/N\s+(\d+)', head)
            if not mf or not mn:
                continue
            first, count = int(mf.group(1)), int(mn.group(1))
            pairs = [(int(a), int(b)) for a, b in
                     re.findall(rb'(\d+)\s+(\d+)', self.streams[n][:first])[:count]]
            body = self.streams[n][first:]
            for i, (onum, off) in enumerate(pairs):
                end = pairs[i + 1][1] if i + 1 < len(pairs) else len(body)
                self.objects[onum] = body[off:end]

    def pages(self):
        return sorted(n for n, v in self.objects.items()
                      if re.search(rb'/Type\s*/Page(?![s\w])', v))

    def fonts(self, pnum):
        res = _get_dict(self.objects[pnum], b'Resources')
        if res is None:
            return {}
        fdict = _get_dict(res, b'Font')
        if fdict is None:
            rn = _get_ref(res, b'Font')
            if rn is None or rn not in self.objects:
                return {}
            fdict = self.objects[rn]
        return {name.decode(): int(num) for name, num in
                re.findall(rb'/([A-Za-z0-9#_+\-.]+)\s+(\d+)\s+0\s+R', fdict)}

    def cmap(self, fnum):
        fv = self.objects.get(fnum, b'')
        tu = _get_ref(fv, b'ToUnicode')
        if tu and tu in self.streams:
            return _parse_cmap(self.streams[tu])
        return {}

    def items(self, pnum):
        """返回 [(y, x, text), ...]"""
        v = self.objects[pnum]
        m = re.search(rb'/Contents\s*(\[[^\]]*\]|\d+\s+0\s+R)', v)
        if not m:
            return []
        cnums = [int(x) for x in re.findall(rb'(\d+)\s+0\s+R', m.group(1))]
        content = b''.join(self.streams.get(c, b'') for c in cnums)
        cmaps = {name: self.cmap(num) for name, num in self.fonts(pnum).items()}
        cur = None
        x = y = 0.0
        out = []
        for line in content.split(b'\n'):
            mf = re.search(rb'/([A-Za-z0-9#_+\-.]+)\s+[\d.]+\s+Tf', line)
            if mf:
                cur = mf.group(1).decode()
            tm = re.search(rb'([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+Tm', line)
            if tm:
                x, y = float(tm.group(5)), float(tm.group(6))
            else:
                td = re.search(rb'([-\d.]+)\s+([-\d.]+)\s+(?:Td|TD)\b', line)
                if td:
                    x += float(td.group(1))
                    y += float(td.group(2))
            cm = cmaps.get(cur) or {}
            buf = []
            for m2 in re.finditer(rb'\((?:[^()\\]|\\.)*\)|<([0-9A-Fa-f\s]+)>', line):
                raw = m2.group(0)
                if raw.startswith(b'('):
                    buf.append(_unescape(raw[1:-1]).decode('latin-1'))
                    continue
                hx = re.sub(rb'\s', b'', m2.group(1))
                if len(hx) % 2:
                    hx += b'0'
                try:
                    bs = bytes.fromhex(hx.decode())
                except Exception:
                    continue
                if not cm:
                    buf.append(bs.decode('latin-1'))
                    continue
                for k in range(0, len(bs), 2):
                    dst = cm.get(bs[k:k + 2])
                    if dst:
                        try:
                            buf.append(dst.decode('utf-16-be'))
                        except Exception:
                            pass
            t = ''.join(buf).strip()
            if t:
                out.append((y, x, t))
        return out


def _char_weight(ch):
    """近似字符宽度权重（以中文字宽为 1）。"""
    o = ord(ch)
    if o > 0x2E80:          # CJK 及全角标点
        return 1.0
    if ch == ' ':
        return 0.30
    if ch in 'ilj.,:;\'"|!':
        return 0.28
    if ch.isupper() or ch in 'mwMW':
        return 0.72
    return 0.55


def _join_runs(runs):
    """按 x 排序把同一视觉行的文本片段拼成一行，必要时补空格。"""
    runs = sorted(runs)
    if not runs:
        return ''
    if len(runs) == 1:
        return runs[0][1]
    first_x, last_x = runs[0][0], runs[-1][0]
    span = last_x - first_x
    if span <= 0:
        return ''.join(t for _, t in runs)
    # 用"除最后一段外"的实际跨度反推中文字宽（各字符宽度按权重折算）
    total_w = sum(sum(_char_weight(c) for c in t) for _, t in runs[:-1]) or 1.0
    w_cjk = span / total_w
    out = [runs[0][1]]
    prev_x, prev_text = runs[0]
    for x, t in runs[1:]:
        prev_w = sum(_char_weight(c) for c in prev_text) * w_cjk
        gap = x - (prev_x + prev_w)
        if gap > 0.25 * w_cjk:
            out.append(' ')
        out.append(t)
        prev_x, prev_text = x, t
    return ''.join(out)


def extract(path):
    """PDF → 纯文本（按版面顺序，尽量保留行）。失败返回 ('', 错误说明)。"""
    with open(path, 'rb') as f:
        data = f.read()
    if not data.startswith(b'%PDF'):
        return '', '文件不是有效的 PDF'
    try:
        pdf = _Pdf(data)
    except Exception as e:  # 解析失败兜底
        return '', 'PDF 结构解析失败：%s' % e
    page_lines = []
    for p in pdf.pages():
        items = pdf.items(p)
        if not items:
            continue
        buckets = {}
        for y, x, t in items:
            buckets.setdefault(round(y / 8), []).append((x, t))
        for k in sorted(buckets):
            page_lines.append(_join_runs(buckets[k]))
    text = '\n'.join(page_lines)
    if len(text.strip()) < 20:
        return '', '未能从 PDF 中提取到文本（可能是扫描件/图片型 PDF，需要 OCR 或改用 Word/文本）'
    return text, None
