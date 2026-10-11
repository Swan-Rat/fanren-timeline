#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pingshu_split.py — 把 17 卷评书版 Markdown 拆成结构化常量 PSBOOK，幂等注入时间线 HTML。

零依赖（仅标准库）。解析口径（已对底本逐卷核对）：
  · 卷：H1「# 凡人修仙之仙界篇 · 评书版（第X卷 · 名）」，按中文卷号排序；
  · 回-节块：H2「## 第 N 回 · 标题（章号标注）」，共 111 块（84 回，其中 16 回拆 2–5 节）；
    章号标注 6 种变体：（外传）/（chX）/（chX–Y）/（chX–Y，本回N节）/（…连写）/无标注；
    标题内可嵌套全角括号（如「第五衰（煞衰）降临（ch599–602）」）→ 取行尾最后一个（ch…）；
  · 无标注块（卷16 第65/66回）回退查本卷「本卷回目」表回填章号区间；
  · 块内：H3 小节；引用块按首行分类——
      **【…·脉络】** → 脉络块(m)；本回/本节/本段原文… → 块首统计(st)；
      原文 X → 评书 Y → 节末统计(s)；**说书人…** → 评点(c)；其余 → 泛引用(q)；
  · 正文单行成段，仅极少数跨行（无空行相接者并入同段）；行内保留 **粗体** 与 `代码` 标记。

用法：
  python3 pingshu_split.py                      # 自检 + 注入默认 HTML
  python3 pingshu_split.py --check              # 只自检不注入
  python3 pingshu_split.py --out /tmp/ps.js     # 只导出常量不注入
"""
import argparse
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_HTML = os.path.join(HERE, '仙界篇-纪传体时间线.html')
SRC_GLOB = os.path.join(HERE, 'research', 'pingshu', '仙界篇评书版-第*.md')
M_BEGIN = '/*__PSBOOK__'
M_END = '/*__PSBOOK_END__*/'

CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
      '六': 6, '七': 7, '八': 8, '九': 9}


def cn2int(s):
    if '十' in s:
        a, b = s.split('十')
        return (CN.get(a, 1) if a else 1) * 10 + (CN[b] if b else 0)
    return CN[s]


H2_RE = re.compile(
    r'^## 第 (\d+) 回 · (.+?)（(外传|ch(\d+)(?:–(\d+))?)(?:，([^）]*))?）\s*$')
H2_BARE_RE = re.compile(r'^## 第 (\d+) 回 · (.+?)\s*$')
CH_RE = re.compile(r'ch(\d+)(?:–(\d+))?')
MO_RE = re.compile(r'^>\s*\*\*【([^】]*)】\*\*\s*(.*)$')


def parse_ch_cell(cell):
    """回目表「章」列 → (c0,c1)；外传→(0,0)。"""
    if '外传' in cell and not re.search(r'ch\d', cell):
        return (0, 0)
    ms = CH_RE.findall(cell)
    if not ms:
        return None
    lo = min(int(a) for a, _ in ms)
    hi = max(int(b) if b else int(a) for a, b in ms)
    return (lo, hi)


def parse_volume(path):
    lines = open(path, encoding='utf-8').read().split('\n')
    m = re.search(r'第([一二三四五六七八九十]+)卷 · (.+?)）\s*$', lines[0])
    vol_no, vol_name = cn2int(m.group(1)), m.group(2).strip()

    # 回目表（回号 → 章号区间），供无标注块回填
    huimu = {}
    i = 0
    while i < len(lines):
        if lines[i].startswith('## 本卷回目'):
            i += 1
            while i < len(lines) and not lines[i].startswith('## 第'):
                L = lines[i].strip()
                if L.startswith('|') and not set(L) <= set('|-: '):
                    cells = [c.strip() for c in L.strip('|').split('|')]
                    if len(cells) >= 3 and cells[0].isdigit():
                        rng = parse_ch_cell(cells[2])
                        if rng:
                            huimu[int(cells[0])] = rng
                i += 1
            continue
        i += 1

    blocks, cur, cur_sec, para = [], None, None, None

    def flush_para():
        nonlocal para
        if para is not None and cur_sec is not None:
            cur_sec['i'].append(['p', para])
        para = None

    def new_sec(h):
        nonlocal cur_sec
        flush_para()
        cur_sec = {'h': h, 'i': []}
        cur['sec'].append(cur_sec)
        return cur_sec

    i = 0
    while i < len(lines):
        L = lines[i]
        if L.startswith('## 第 '):
            flush_para()
            mm = H2_RE.match(L)
            if mm:
                hui = int(mm.group(1))
                title = mm.group(2).strip()
                if mm.group(3) == '外传':
                    c0 = c1 = 0
                else:
                    c0 = int(mm.group(4))
                    c1 = int(mm.group(5)) if mm.group(5) else c0
                sec = (mm.group(6) or '').strip()
            else:
                mb = H2_BARE_RE.match(L)
                if not mb:
                    sys.exit('[pingshu] 无法解析 H2: %s (%s)' % (L, os.path.basename(path)))
                hui, title = int(mb.group(1)), mb.group(2).strip()
                sec = ''
                rng = huimu.get(hui)
                if not rng:
                    sys.exit('[pingshu] 回%d 无章号标注且回目表缺失 (%s)'
                             % (hui, os.path.basename(path)))
                c0, c1 = rng
            cur = {'hui': hui, 't': title, 's': sec, 'c0': c0, 'c1': c1,
                   'st': '', 'sec': []}
            blocks.append(cur)
            cur_sec = None
            para = None
            i += 1
            continue
        if cur is None:
            i += 1
            continue
        if L.startswith('### '):
            new_sec(L[4:].strip())
            i += 1
            continue
        if L.startswith('>'):
            unit = [L]
            j = i + 1
            while j < len(lines) and lines[j].startswith('>'):
                unit.append(lines[j])
                j += 1
            text = unit[0]
            cont = [u[1:].strip() if u.startswith('> ') else u.lstrip('>') for u in unit[1:]]
            body = (text[1:].strip() if text.startswith('> ') else text.lstrip('>'))
            if cont:
                body = body + ''.join(cont)
            if cur_sec is None:
                new_sec('')
            first = unit[0]
            if re.match(r'^>\s*\*\*【', first):
                mo = MO_RE.match(first)
                label = mo.group(1) if mo else ''
                mbody = (mo.group(2) if mo else body)
                if cont:
                    mbody = mbody + ''.join(cont)
                cur_sec['i'].append(['m', label, mbody])
            elif re.match(r'^>\s*本[回节段]', first):
                if not cur['st'] and not cur_sec['i'] and para is None:
                    cur['st'] = body
                else:
                    cur_sec['i'].append(['s', body])
            elif re.match(r'^>\s*原文', first):
                cur_sec['i'].append(['s', body])
            elif re.match(r'^>\s*\*\*说书人', first):
                cur_sec['i'].append(['c', body])
            else:
                cur_sec['i'].append(['q', body])
            i = j
            continue
        if not L.strip() or L.strip() == '---' or L.startswith('|'):
            flush_para()
            i += 1
            continue
        # 正文段（跨行无空行相接者并入）
        # ⚠️ 兜底：本回若通篇纯正文（无 ### 小节、无 > 引用块），cur_sec 仍为 None，
        # flush_para() 的 `cur_sec is not None` 条件会把整回正文静默丢弃
        # （卷1 第8回「灭天鬼宗」11 段即因此丢失）。此处补建空标题小节。
        if cur_sec is None:
            new_sec('')
        if para is None:
            para = L.strip()
        else:
            para = para + L.strip()
        #  lookahead：下一行若非空且非特殊则继续累加
        k = i + 1
        if k < len(lines) and lines[k].strip() and not re.match(
                r'^(#|>|\||---)', lines[k]):
            i += 1
            continue
        flush_para()
        i += 1
    flush_para()
    return {'no': vol_no, 'name': vol_name, 'blocks': blocks}


def build(vols):
    vol_names = [v['name'] for v in vols]
    idx = {v['no']: k for k, v in enumerate(vols)}
    blocks = []
    for v in vols:
        for b in v['blocks']:
            o = {'v': idx[v['no']], 'h': b['hui'], 't': b['t'],
                 'c0': b['c0'], 'c1': b['c1']}
            if b['s']:
                o['s'] = b['s']
            if b['st']:
                o['st'] = b['st']
            sec = []
            for s in b['sec']:
                if not s['i'] and not s['h']:
                    continue
                so = {}
                if s['h']:
                    so['h'] = s['h']
                so['i'] = s['i']
                sec.append(so)
            o['sec'] = sec
            blocks.append(o)
    return {'vols': vol_names, 'blocks': blocks}


def self_check(vols, data):
    nb = len(data['blocks'])
    nsec = sum(1 for b in data['blocks'] for s in b['sec'] if s.get('h'))
    nmo = sum(1 for b in data['blocks'] for s in b['sec'] for it in s['i'] if it[0] == 'm')
    nst = sum(1 for b in data['blocks'] if b.get('st'))
    print('[pingshu] 卷=%d 块=%d 小节(H3)=%d 脉络块=%d 块首统计=%d'
          % (len(vols), nb, nsec, nmo, nst))
    ok = True
    # 章号覆盖自检
    covered = {}
    for i, b in enumerate(data['blocks']):
        for c in range(b['c0'], b['c1'] + 1):
            if c not in covered:
                covered[c] = i
    miss = [c for c in range(0, 1395) if c not in covered]
    print('[pingshu] 章号覆盖 0–1394：缺口 %s' % (miss if miss else '无'))
    if miss:
        ok = False
    # 空块自检：正文被静默丢弃时章号覆盖仍完整，必须单独查
    # （卷1 第8回曾因纯正文无 H3/引用块导致 sec=[] 而未被发现）
    empty = []
    for b in data['blocks']:
        chars = sum(len(it[-1]) for s in b['sec'] for it in s['i']
                    if isinstance(it[-1], str))
        if not b['sec'] or chars < 50:
            empty.append('卷%d第%d回《%s》(%d字)'
                         % (b['v'] + 1, b['h'], b['t'], chars))
    print('[pingshu] 空块/内容过少：%s' % (empty if empty else '无'))
    if empty:
        ok = False
    return ok


def inject(html_path, js):
    src = open(html_path, encoding='utf-8').read()
    bi = src.find(M_BEGIN)
    ei = src.find(M_END)
    if bi < 0 or ei < 0:
        sys.exit('[pingshu] HTML 中未找到注入标记 %s / %s' % (M_BEGIN, M_END))
    bi = src.index('\n', bi) + 1
    new = src[:bi] + js + '\n' + src[ei:]
    open(html_path, 'w', encoding='utf-8').write(new)
    print('[pingshu] 已注入 %s（数据区 %d KB，整页 %d KB）'
          % (os.path.basename(html_path), len(js.encode('utf-8')) // 1024,
             len(new.encode('utf-8')) // 1024))


def main():
    ap = argparse.ArgumentParser(description='评书全本 → PSBOOK 常量并注入时间线 HTML')
    ap.add_argument('--html', default=DEFAULT_HTML)
    ap.add_argument('--check', action='store_true', help='只自检不注入')
    ap.add_argument('--out', help='只导出常量到文件（不注入）')
    args = ap.parse_args()

    files = sorted(glob.glob(SRC_GLOB),
                   key=lambda p: cn2int(re.search(r'第([一二三四五六七八九十]+)卷',
                                                  os.path.basename(p)).group(1)))
    vols = [parse_volume(p) for p in files]
    data = build(vols)
    ok = self_check(vols, data)
    js = 'const PSBOOK=' + json.dumps(data, ensure_ascii=False,
                                      separators=(',', ':')) + ';'
    if args.out:
        open(args.out, 'w', encoding='utf-8').write(js)
        print('[pingshu] 导出 %s（%d KB）' % (args.out, len(js.encode('utf-8')) // 1024))
    if not args.check and not args.out:
        inject(args.html, js)
    if not ok:
        sys.exit(1)


if __name__ == '__main__':
    main()
