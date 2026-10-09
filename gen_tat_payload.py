#!/usr/bin/env python3
"""生成可下发到 Lighthouse MCP execute_command(TAT) 的部署载荷。

用法：
    python3 gen_tat_payload.py [--site DIR] [--src URL] [--webroot DIR] [-o OUT]

设计要点：
- 清单直接从 site/ 目录实时计算 md5 + 字节数，不手写、不会漂移。
- 载荷内嵌 python（服务器侧只需 python3，Ubuntu 自带 3.12）。
- 中文文件名在载荷里写成 \\uXXXX 转义：TAT Command 经多层传递时手打中文
  极易写坏（曾把「灵界地图总览.webp」写成「霊界地図総覧」导致 404）。
- 服务器侧对每个文件先比 md5，相同则 SKIP —— 载荷可重复执行，只覆盖真变了的。
- 落盘走 .new + md5/size 双断言 + shutil.move，原子替换，校验不过不会污染线上。
- 生成后自检：ast.parse 语法、\\uXXXX 还原等于原文件名、总长 < TAT 上限 2048。
"""
import argparse
import ast
import hashlib
import os
import sys

TAT_LIMIT = 2048


def py_str(s):
    """把字符串写成 python 字面量，非 ASCII 一律 \\uXXXX 转义。"""
    out = []
    for c in s:
        if c == '"' or c == "\\":
            out.append("\\" + c)
        elif ord(c) < 128:
            out.append(c)
        else:
            out.append("\\u%04x" % ord(c))
    return '"' + "".join(out) + '"'


def scan(site_dir):
    items = []
    for name in sorted(os.listdir(site_dir)):
        p = os.path.join(site_dir, name)
        if not os.path.isfile(p):
            continue
        data = open(p, "rb").read()
        items.append((name, hashlib.md5(data).hexdigest(), len(data)))
    if not items:
        sys.exit("site 目录为空：%s" % site_dir)
    return items


def build(items, src, webroot):
    lines = [
        "python3 - <<'PYEOF'",
        "import urllib.request as u, urllib.parse, os, hashlib, shutil",
        'B = "%s/"' % src.rstrip("/"),
        'D = "%s"' % webroot,
        "FILES = {",
    ]
    for name, md5, size in items:
        lines.append(" %s: (\"%s\", %d)," % (py_str(name), md5, size))
    lines += [
        "}",
        "os.makedirs(D, exist_ok=True)",
        "for name, (md5, size) in FILES.items():",
        "    p = os.path.join(D, name)",
        '    old = hashlib.md5(open(p, "rb").read()).hexdigest() '
        'if os.path.exists(p) else "-"',
        "    if old == md5:",
        '        print("SKIP", name, "unchanged"); continue',
        '    tmp = p + ".new"',
        '    data = u.urlopen(B + urllib.parse.quote(name) + "?v=" + md5[:8], '
        "timeout=180).read()",
        "    got = hashlib.md5(data).hexdigest()",
        "    assert got == md5, (name, got, md5)",
        "    assert len(data) == size, (name, len(data), size)",
        '    open(tmp, "wb").write(data); shutil.move(tmp, p)',
        '    print("OK", name, len(data), "was", old[:8])',
        "PYEOF",
    ]
    return "\n".join(lines) + "\n"


def selfcheck(text, items):
    inner = text.split("PYEOF\n", 1)[1].rsplit("\nPYEOF", 1)[0]
    ast.parse(inner)  # 语法必须能编译
    for name, _, _ in items:
        assert ast.literal_eval(py_str(name)) == name, "转义还原失败：%r" % name
        assert name not in ("", ".", "..")
    assert len(text) < TAT_LIMIT, "载荷 %d 字符，超 TAT 上限 %d" % (len(text), TAT_LIMIT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", default="site", help="本地站点目录（默认 site）")
    ap.add_argument(
        "--src",
        default="https://fanren-timeline-site.app.workbuddy.host",
        help="发布源根地址",
    )
    ap.add_argument(
        "--webroot", default="/opt/frxxz/html", help="服务器站点根目录"
    )
    ap.add_argument("-o", "--out", default="/tmp/tat_deploy_payload.txt")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    items = scan(a.site)
    text = build(items, a.src, a.webroot)
    selfcheck(text, items)
    open(a.out, "w", encoding="utf-8").write(text)

    if not a.quiet:
        print("清单（实时计算自 %s）：" % a.site)
        for name, md5, size in items:
            print("  %-26s %s %8dB" % (name, md5, size))
        print(
            "载荷 %d 字符 / 上限 %d，已写入 %s" % (len(text), TAT_LIMIT, a.out)
        )
        print("自检通过：语法可编译、\\uXXXX 可还原、长度合规")


if __name__ == "__main__":
    main()
