# -*- coding: utf-8 -*-
"""文件 → 文本。零依赖：PDF 用自研解析，DOCX 用 zipfile + xml.etree。"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET

from . import pdftext

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _docx_text(path):
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        return '', 'DOCX 打开失败：%s' % e
    names = z.namelist()
    part = 'word/document.xml'
    if part not in names:
        return '', 'DOCX 结构异常：缺少 word/document.xml'
    xml = z.read(part)
    try:
        root = ET.fromstring(xml)
    except Exception as e:
        return '', 'DOCX 解析失败：%s' % e
    lines = []
    for p in root.iter(W + 'p'):
        buf = []
        for node in p.iter():
            tag = node.tag
            if tag == W + 't':
                buf.append(node.text or '')
            elif tag == W + 'tab':
                buf.append('\t')
            elif tag == W + 'br':
                buf.append('\n')
        line = ''.join(buf).strip()
        if line:
            lines.append(line)
    # 表格文字已包含在 document.xml 的 p 中
    return '\n'.join(lines), None


def _pdf_fallback_text(path):
    """极端情况下的兜底：直接抽取文本流里的 ASCII（中文会缺失）。"""
    with open(path, 'rb') as f:
        data = f.read()
    chunks = re.findall(rb'\((?:[^()\\]|\\.)*\)', data)
    txt = ' '.join(c[1:-1].decode('latin-1', 'ignore') for c in chunks)
    return re.sub(r'\s+', ' ', txt).strip()


def extract_text(path):
    """返回 (text, error)。error 为 None 表示成功。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.pdf':
        text, err = pdftext.extract(path)
        if err:
            return text, err
        return text, None
    if ext == '.docx':
        text, err = _docx_text(path)
        if err:
            return text, err
        if len(text.strip()) < 20:
            return text, 'DOCX 中几乎没有文本内容'
        return text, None
    if ext in ('.txt', '.md', '.text'):
        for enc in ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16'):
            try:
                with open(path, 'r', encoding=enc) as f:
                    return f.read(), None
            except UnicodeDecodeError:
                continue
        return '', '文本文件编码无法识别（请另存为 UTF-8）'
    if ext == '.doc':
        return '', '旧版 .doc 暂不支持，请在 Word/WPS 中另存为 .docx 后再上传'
    return '', '不支持的文件类型：%s（支持 PDF / DOCX / TXT）' % (ext or '未知')
