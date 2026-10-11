#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
md2epub.py — 把评书版 Markdown 转成可在京东读书 / 华为阅读等 App 导入的 EPUB 3。

零依赖（仅 Python 标准库）。支持的 Markdown 子集（即 research/pingshu/ 各卷用到的全部语法）：
  # / ## / ###  标题（## 切分为独立章节文件，### 生成小节锚点并入目录）
  > 引用块      （含 "> - " 列表、缩进续行、以 **粗体** 开头的行另起一段）
  | 管道表格    （第二行为 |---| 分隔行）
  **粗体**、`行内代码`、--- 水平线、单行自然段

用法：
  python3 md2epub.py research/pingshu/仙界篇评书版-第一卷-灵寰界.md
  python3 md2epub.py 输入.md -o 输出.epub --author "署名"
  python3 md2epub.py research/pingshu/*.md          # 批量
"""
import argparse
import hashlib
import os
import re
import sys
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ---------------------------------------------------------------- 行内语法

INLINE_RE = re.compile(r'(\*\*.+?\*\*|`[^`]+`)')


def esc(t):
    return t.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def inline(text):
    """行内 Markdown → XHTML 片段（粗体 / 行内代码，其余转义）。"""
    out = []
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            out.append('<strong>%s</strong>' % esc(part[2:-2]))
        elif part.startswith('`') and part.endswith('`') and len(part) > 2:
            out.append('<code>%s</code>' % esc(part[1:-1]))
        else:
            out.append(esc(part))
    return ''.join(out)


# ---------------------------------------------------------------- 块级解析

def parse_blocks(lines):
    """把行序列解析成块列表：heading/hr/quote/table/p。"""
    blocks, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        m = re.match(r'^(#{1,6})\s+(.*)$', s)
        if m:
            blocks.append(('heading', len(m.group(1)), m.group(2).strip()))
            i += 1
            continue
        if re.match(r'^-{3,}$', s) or re.match(r'^\*{3,}$', s):
            blocks.append(('hr',))
            i += 1
            continue
        if s.startswith('>'):
            quote = []
            while i < n and lines[i].strip().startswith('>'):
                q = lines[i].strip()[1:]
                if q.startswith(' '):
                    q = q[1:]
                quote.append(q)
                i += 1
            blocks.append(('quote', parse_quote(quote)))
            continue
        if s.startswith('|'):
            rows = []
            while i < n and lines[i].strip().startswith('|'):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(('table', parse_table(rows)))
            continue
        blocks.append(('p', s))
        i += 1
    return blocks


def parse_quote(qlines):
    """引用块内部：返回 [('p', html) | ('ul', [html,...])] 列表。

    规则（针对本系列文档的实际写法）：
      - "- " 开头 → 列表项；随后以两个空格缩进的行是列表项续行。
      - 以 "**" 开头的行 → 新起一段（如「**本卷范围**：…」）。
      - 其余非空行 → 接在当前段后（续行）。空行 → 分段/结束列表。
    """
    items, cur_p, cur_ul, cur_li = [], [], [], None

    def flush_p():
        nonlocal cur_p
        if cur_p:
            items.append(('p', inline(''.join(cur_p))))
            cur_p = []

    def flush_ul():
        nonlocal cur_ul, cur_li
        if cur_li is not None:
            cur_ul.append(inline(''.join(cur_li)))
            cur_li = None
        if cur_ul:
            items.append(('ul', cur_ul))
            cur_ul = []

    for q in qlines:
        if not q.strip():
            flush_p()
            flush_ul()
            continue
        if q.startswith('- '):
            flush_p()
            if cur_li is not None:
                cur_ul.append(inline(''.join(cur_li)))
            cur_li = [q[2:].strip()]
            continue
        if cur_li is not None and q.startswith('  '):
            cur_li.append(q.strip())
            continue
        flush_ul()
        if q.startswith('**') or not cur_p:
            flush_p()
            cur_p = [q.strip()]
        else:
            cur_p.append(q.strip())
    flush_p()
    flush_ul()
    return items


def parse_table(rows):
    """管道表格 → (表头单元格列表, 数据行列表)；分隔行剔除。"""
    def cells(r):
        r = r.strip()
        if r.startswith('|'):
            r = r[1:]
        if r.endswith('|'):
            r = r[:-1]
        return [c.strip() for c in r.split('|')]

    head, body = None, []
    for r in rows:
        cs = cells(r)
        if all(re.match(r'^:?-{2,}:?$', c) for c in cs if c != '') and any(cs):
            continue  # 分隔行
        if head is None:
            head = cs
        else:
            body.append(cs)
    return head or [], body


# ---------------------------------------------------------------- 章节切分

def split_chapters(blocks):
    """按 H2 切章。返回 (preamble_blocks, [(h2_title, blocks), ...])。"""
    pre, chapters, cur = [], [], None
    for b in blocks:
        if b[0] == 'heading' and b[1] == 2:
            cur = (b[2], [])
            chapters.append(cur)
        elif cur is None:
            pre.append(b)
        else:
            cur[1].append(b)
    # H1 从 preamble 里摘出来做书名
    title = None
    rest = []
    for b in pre:
        if title is None and b[0] == 'heading' and b[1] == 1:
            title = b[2]
        else:
            rest.append(b)
    return title, rest, chapters


def trim_hr(blocks):
    """去掉章节首尾的水平线。"""
    while blocks and blocks[0][0] == 'hr':
        blocks = blocks[1:]
    while blocks and blocks[-1][0] == 'hr':
        blocks = blocks[:-1]
    return blocks


# ---------------------------------------------------------------- XHTML 生成

CSS = """\
@charset "UTF-8";
body { font-family: "Songti SC", "Noto Serif CJK SC", serif; line-height: 1.75;
       margin: 1em 0.9em; }
h1 { font-size: 1.5em; text-align: center; margin: 1.2em 0 0.8em; line-height: 1.4; }
h2 { font-size: 1.25em; margin: 0.6em 0 0.8em; padding-bottom: 0.3em;
     border-bottom: 1px solid #999; line-height: 1.4; }
h3 { font-size: 1.08em; margin: 1.4em 0 0.5em; }
p  { margin: 0.55em 0; text-indent: 2em; text-align: justify; }
blockquote { margin: 0.9em 0.3em; padding: 0.5em 0.8em; font-size: 0.92em;
             border-left: 3px solid #a07830; background: #f7f2e7; color: #555; }
blockquote p { text-indent: 0; margin: 0.35em 0; }
blockquote ul { margin: 0.3em 0 0.3em 1.4em; padding: 0; }
blockquote li { margin: 0.25em 0; text-align: justify; }
table { border-collapse: collapse; margin: 0.8em auto; font-size: 0.85em;
        width: 100%; }
th, td { border: 1px solid #999; padding: 0.3em 0.5em; text-align: left; }
th { background: #f0ead9; }
code { font-family: "SF Mono", Menlo, monospace; font-size: 0.88em; }
hr { border: none; border-top: 1px solid #ccc; margin: 1.6em 20%; }
.titlepage { text-align: center; margin-top: 22%; }
.titlepage .sub { color: #777; font-size: 0.9em; margin-top: 1.5em; }
.vol-tag { text-align: center; color: #888; font-size: 0.85em;
           margin: 0 0 0.8em; text-indent: 0; }
.volpage { text-align: center; margin-top: 26%; }
.volpage h1 { border: none; }
"""


def render_blocks(blocks, h3_counter=None):
    """块列表 → XHTML 字符串。h3_counter 传入 list 则给 h3 编锚点 id。"""
    out = []
    for b in blocks:
        kind = b[0]
        if kind == 'heading':
            lvl, text = b[1], b[2]
            if lvl == 3 and h3_counter is not None:
                h3_counter.append((text, 's%d' % len(h3_counter)))
                out.append('<h3 id="%s">%s</h3>' % (h3_counter[-1][1], inline(text)))
            elif lvl == 1:
                out.append('<h1>%s</h1>' % inline(text))
            else:
                out.append('<h%d>%s</h%d>' % (min(lvl, 6), inline(text), min(lvl, 6)))
        elif kind == 'hr':
            out.append('<hr/>')
        elif kind == 'p':
            out.append('<p>%s</p>' % inline(b[1]))
        elif kind == 'quote':
            inner = []
            for it in b[1]:
                if it[0] == 'p':
                    inner.append('<p>%s</p>' % it[1])
                else:
                    inner.append('<ul>%s</ul>' % ''.join('<li>%s</li>' % x for x in it[1]))
            out.append('<blockquote>%s</blockquote>' % ''.join(inner))
        elif kind == 'table':
            head, body = b[1]
            ncols = max([len(head)] + [len(r) for r in body]) if (head or body) else 0
            t = ['<table>']
            if head:
                t.append('<thead><tr>%s</tr></thead>' %
                         ''.join('<th>%s</th>' % inline(c) for c in head))
            t.append('<tbody>')
            for r in body:
                r = list(r) + [''] * (ncols - len(r))
                t.append('<tr>%s</tr>' % ''.join('<td>%s</td>' % inline(c) for c in r))
            t.append('</tbody></table>')
            out.append(''.join(t))
    return '\n'.join(out)


def xhtml_doc(title, body_html):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN" lang="zh-CN">\n'
        '<head>\n<meta charset="utf-8"/>\n<title>%s</title>\n'
        '<link rel="stylesheet" type="text/css" href="stylesheet.css"/>\n</head>\n'
        '<body>\n%s\n</body>\n</html>\n' % (esc(title), body_html)
    )


# ---------------------------------------------------------------- EPUB 组装

def build_epub(md_path, out_path, author=None, subject='评书'):
    with open(md_path, encoding='utf-8') as f:
        lines = f.read().split('\n')

    blocks = parse_blocks(lines)
    title, pre, chapters = split_chapters(blocks)
    if not title:
        title = os.path.splitext(os.path.basename(md_path))[0]
    author = author or '据忘语原著转述'

    book_id = 'urn:uuid:' + str(uuid.UUID(hashlib.md5(
        (title + md_path).encode('utf-8')).hexdigest()))
    modified = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    files = {}          # arcname -> bytes
    spine = []          # (id, href, title)
    nav_entries = []    # (href, title, [(anchor_id, sub_title), ...])

    # 扉页（preamble：凡例、体量说明等）
    tp_body = ['<div class="titlepage"><h1>%s</h1>'
               '<div class="sub">%s</div></div>' % (inline(title), esc(author))]
    if pre:
        tp_body.append(render_blocks(trim_hr(pre)))
    files['OEBPS/titlepage.xhtml'] = xhtml_doc(title, '\n'.join(tp_body))
    spine.append(('titlepage', 'titlepage.xhtml', title))

    # 各章
    for idx, (h2, cblocks) in enumerate(chapters, 1):
        fid = 'ch%03d' % idx
        href = fid + '.xhtml'
        h3s = []
        body = render_blocks(trim_hr(cblocks), h3_counter=h3s)
        files['OEBPS/' + href] = xhtml_doc(h2, '<section epub:type="chapter">\n'
                                           '<h2>%s</h2>\n%s\n</section>'
                                           % (inline(h2), body))
        spine.append((fid, href, h2))
        nav_entries.append((href, h2, h3s))

    # nav.xhtml（EPUB3 导航文档，含 H3 嵌套）
    ol = []
    for href, t, subs in nav_entries:
        if subs:
            sub_ol = ''.join('<li><a href="%s#%s">%s</a></li>'
                             % (href, aid, esc(st)) for st, aid in subs)
            ol.append('<li><a href="%s">%s</a>\n<ol>%s</ol>\n</li>'
                      % (href, esc(t), sub_ol))
        else:
            ol.append('<li><a href="%s">%s</a></li>' % (href, esc(t)))
    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">\n'
        '<head><meta charset="utf-8"/><title>目录</title></head>\n'
        '<body>\n<nav epub:type="toc" id="toc">\n<h1>目录</h1>\n<ol>\n%s\n</ol>\n</nav>\n'
        '</body>\n</html>\n' % '\n'.join(ol))
    files['OEBPS/nav.xhtml'] = nav

    # toc.ncx（兼容老版本阅读器）
    np, order = [], 0
    for href, t, subs in nav_entries:
        order += 1
        if subs:
            children = []
            for st, aid in subs:
                order += 1
                children.append(
                    '<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text>'
                    '</navLabel><content src="%s#%s"/></navPoint>'
                    % (order, order, esc(st), href, aid))
            np.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text>'
                      '</navLabel><content src="%s"/>%s</navPoint>'
                      % (order - len(subs), order - len(subs), esc(t), href,
                         ''.join(children)))
        else:
            np.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text>'
                      '</navLabel><content src="%s"/></navPoint>'
                      % (order, order, esc(t), href))
    # 修正嵌套时的外层 playOrder（上面按“子项已计入”回推，重新统一编号更稳妥）
    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">\n'
        '<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="2"/>'
        '<meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>\n'
        '<docTitle><text>%s</text></docTitle>\n<navMap>\n%s\n</navMap>\n</ncx>\n'
        % (book_id, esc(title), '\n'.join(np)))
    files['OEBPS/toc.ncx'] = ncx

    # content.opf
    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="stylesheet.css" media-type="text/css"/>',
    ]
    for fid, href, _ in spine:
        manifest.append('<item id="%s" href="%s" media-type="application/xhtml+xml"/>'
                        % (fid, href))
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="zh-CN">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '<dc:identifier id="bookid">%s</dc:identifier>\n'
        '<dc:title>%s</dc:title>\n'
        '<dc:creator id="creator">%s</dc:creator>\n'
        '<dc:language>zh-CN</dc:language>\n'
        '<dc:subject>%s</dc:subject>\n'
        '<dc:subject>仙侠</dc:subject>\n'
        '<meta property="dcterms:modified">%s</meta>\n'
        '</metadata>\n<manifest>\n%s\n</manifest>\n'
        '<spine toc="ncx">\n%s\n</spine>\n</package>\n'
        % (book_id, esc(title), esc(author), esc(subject), modified,
           '\n'.join(manifest),
           '\n'.join('<itemref idref="%s"/>' % fid for fid, _, _ in spine)))
    files['OEBPS/content.opf'] = opf

    files['OEBPS/stylesheet.css'] = CSS
    files['META-INF/container.xml'] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')

    _zip_epub(files, out_path)
    print('[md2epub] %s → %s（%d 章，%d KB）'
          % (os.path.basename(md_path), out_path, len(spine) - 1,
             max(1, os.path.getsize(out_path) // 1024)))
    return out_path


# ---------------------------------------------------------------- 合订本

CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
          '六': 6, '七': 7, '八': 8, '九': 9}


def cn2int(s):
    """中文数字（1–99）→ int。"""
    if '十' in s:
        a, b = s.split('十')
        return (CN_NUM.get(a, 1) if a else 1) * 10 + (CN_NUM[b] if b else 0)
    return CN_NUM[s]


def parse_book(md_path):
    """解析单卷 → dict(title=H1, vol=卷名括号内文字, num=卷号, pre, chapters)。"""
    with open(md_path, encoding='utf-8') as f:
        blocks = parse_blocks(f.read().split('\n'))
    title, pre, chapters = split_chapters(blocks)
    if not title:
        title = os.path.splitext(os.path.basename(md_path))[0]
    m = re.search(r'第([一二三四五六七八九十百]+)卷', title)
    num = cn2int(m.group(1)) if m else 0
    vm = re.search(r'（(.+)）\s*$', title)
    return {'title': title, 'num': num, 'vol': vm.group(1) if vm else title,
            'pre': pre, 'chapters': chapters, 'path': md_path}


def _nav_ol(entries):
    """递归生成 nav.xhtml 的嵌套 <ol>。entries: [(href, title, subs)]。"""
    parts = []
    for href, title, subs in entries:
        if subs:
            parts.append('<li><a href="%s">%s</a>\n<ol>%s</ol>\n</li>'
                         % (href, esc(title), _nav_ol(subs)))
        else:
            parts.append('<li><a href="%s">%s</a></li>' % (href, esc(title)))
    return '\n'.join(parts)


def _ncx_points(entries, order):
    """递归生成 NCX navPoint 列表；order 为 [当前 playOrder] 可变计数。"""
    out = []
    for href, title, subs in entries:
        order[0] += 1
        mine = order[0]
        if subs:
            children = _ncx_points(subs, order)
            out.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s'
                       '</text></navLabel><content src="%s"/>%s</navPoint>'
                       % (mine, mine, esc(title), href, ''.join(children)))
        else:
            out.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s'
                       '</text></navLabel><content src="%s"/></navPoint>'
                       % (mine, mine, esc(title), href))
    return out


def build_merged_epub(md_paths, out_path, author=None, book_title=None):
    """多卷合订为一本：卷分隔页 + 每回独立章节文件，三级目录（卷/回/节）。"""
    books = sorted((parse_book(p) for p in md_paths), key=lambda b: b['num'])
    if not book_title:
        book_title = re.sub(r'（第[一二三四五六七八九十百]+卷.*）\s*$', '',
                            books[0]['title']) + '（全本合订）'
    author = author or '据忘语原著转述'
    book_id = 'urn:uuid:' + str(uuid.UUID(hashlib.md5(
        (book_title + '|'.join(b['path'] for b in books)).encode('utf-8')).hexdigest()))
    modified = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    files, spine, nav_entries = {}, [], []
    n_ch = n_sec = 0

    def add(fid, href, doc_title, body_html):
        files['OEBPS/' + href] = xhtml_doc(doc_title, body_html)
        spine.append((fid, href))

    # 总扉页
    n_ch_all = sum(len(b['chapters']) for b in books)
    tp = ['<div class="titlepage"><h1>%s</h1><div class="sub">%s</div>'
          '<div class="sub">全 %d 卷合订本 · 共 %d 回</div></div>'
          % (inline(book_title), esc(author), len(books), n_ch_all)]
    add('titlepage', 'titlepage.xhtml', book_title, '\n'.join(tp))

    for b in books:
        vid = 'v%02d' % b['num']
        # 卷分隔页：卷名 + 本卷凡例/回目表
        vp = ['<div class="volpage"><h1>%s</h1></div>' % inline(b['vol'])]
        if b['pre']:
            vp.append(render_blocks(trim_hr(b['pre'])))
        add(vid, vid + '.xhtml', b['vol'], '\n'.join(vp))
        ch_entries = []
        nav_entries.append((vid + '.xhtml', b['vol'], ch_entries))
        for ci, (h2, cblocks) in enumerate(b['chapters'], 1):
            fid, href = '%sc%02d' % (vid, ci), '%sc%02d.xhtml' % (vid, ci)
            h3s = []
            body = render_blocks(trim_hr(cblocks), h3_counter=h3s)
            add(fid, href, b['vol'] + ' · ' + h2,
                '<section epub:type="chapter">\n'
                '<p class="vol-tag">%s</p>\n<h2>%s</h2>\n%s\n</section>'
                % (esc(b['vol']), inline(h2), body))
            n_ch += 1
            n_sec += len(h3s)
            ch_entries.append((href, h2,
                               [(href + '#' + aid, st, []) for st, aid in h3s]))

    files['OEBPS/nav.xhtml'] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">\n'
        '<head><meta charset="utf-8"/><title>目录</title></head>\n'
        '<body>\n<nav epub:type="toc" id="toc">\n<h1>目录</h1>\n<ol>\n%s\n</ol>\n</nav>\n'
        '</body>\n</html>\n' % _nav_ol(nav_entries))

    ncx = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN">\n'
        '<head><meta name="dtb:uid" content="%s"/><meta name="dtb:depth" content="3"/>'
        '<meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>\n'
        '<docTitle><text>%s</text></docTitle>\n<navMap>\n%s\n</navMap>\n</ncx>\n'
        % (book_id, esc(book_title), '\n'.join(_ncx_points(nav_entries, [0]))))
    files['OEBPS/toc.ncx'] = ncx

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="stylesheet.css" media-type="text/css"/>',
    ]
    manifest += ['<item id="%s" href="%s" media-type="application/xhtml+xml"/>'
                 % (fid, href) for fid, href in spine]
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="bookid" xml:lang="zh-CN">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        '<dc:identifier id="bookid">%s</dc:identifier>\n'
        '<dc:title>%s</dc:title>\n'
        '<dc:creator id="creator">%s</dc:creator>\n'
        '<dc:language>zh-CN</dc:language>\n'
        '<dc:subject>评书</dc:subject>\n<dc:subject>仙侠</dc:subject>\n'
        '<meta property="dcterms:modified">%s</meta>\n'
        '</metadata>\n<manifest>\n%s\n</manifest>\n'
        '<spine toc="ncx">\n%s\n</spine>\n</package>\n'
        % (book_id, esc(book_title), esc(author), modified, '\n'.join(manifest),
           '\n'.join('<itemref idref="%s"/>' % fid for fid, _ in spine)))
    files['OEBPS/content.opf'] = opf
    files['OEBPS/stylesheet.css'] = CSS
    files['META-INF/container.xml'] = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n</container>\n')

    _zip_epub(files, out_path)
    print('[md2epub] 合订 %d 卷 → %s（%d 回 / %d 节 / %d 个文件，%d KB）'
          % (len(books), out_path, n_ch, n_sec, len(spine),
             max(1, os.path.getsize(out_path) // 1024)))
    return out_path


def _zip_epub(files, out_path):
    """XML 良构校验后打包：mimetype 首条目且不压缩。"""
    for name, content in files.items():
        if name.endswith(('.xhtml', '.xml', '.opf', '.ncx')):
            try:
                ET.fromstring(content)
            except ET.ParseError as e:
                raise SystemExit('[md2epub] XML 校验失败 %s: %s' % (name, e))
    os.makedirs(os.path.dirname(out_path) or '.', exist_ok=True)
    with zipfile.ZipFile(out_path, 'w') as z:
        z.writestr(zipfile.ZipInfo('mimetype', date_time=(1980, 1, 1, 0, 0, 0)),
                   'application/epub+zip', compress_type=zipfile.ZIP_STORED)
        for name, content in files.items():
            z.writestr(name, content.encode('utf-8'),
                       compress_type=zipfile.ZIP_DEFLATED)


def main():
    ap = argparse.ArgumentParser(description='评书版 Markdown → EPUB 3（零依赖）')
    ap.add_argument('inputs', nargs='+', help='输入 .md 文件（可多个）')
    ap.add_argument('-o', '--output', help='输出 .epub（仅单文件输入时有效）')
    ap.add_argument('--outdir', default='ebook', help='批量模式输出目录（默认 ebook/）')
    ap.add_argument('--author', default=None, help='作者署名')
    ap.add_argument('--merge', action='store_true',
                    help='多卷合订为一本（卷/回/节三级目录）')
    ap.add_argument('--title', default=None, help='合订本总书名（默认取首卷书名去卷号）')
    args = ap.parse_args()

    if args.merge:
        out = args.output or os.path.join(args.outdir, '仙界篇评书版-全本合订.epub')
        build_merged_epub(args.inputs, out, author=args.author,
                          book_title=args.title)
        return

    for md in args.inputs:
        base = os.path.splitext(os.path.basename(md))[0]
        if args.output and len(args.inputs) == 1:
            out = args.output
        else:
            out = os.path.join(args.outdir, base + '.epub')
        build_epub(md, out, author=args.author)


if __name__ == '__main__':
    main()
