# -*- coding: utf-8 -*-
"""洗稿查重（/xigao 步骤2 的编排者独立复核工具，长期维护版；前身为 运行垃圾/tasks/洗稿调教/查重.py）

口径（xigao.md「查重的两层口径」）：去除全部标点空白后，连续 N 字（默认8）滑窗比对，
零命中才放行；命中片段全部打印并向右扩展到最长。
比对前默认剔除"第N场／画面／人物动作／台词"这类行首格式脚手架（属"格式跟随原稿"，不算抄）；
并默认剔除带货段区间【进入带货段落】–【返回剧情主线】（2026-08-06 裁定：带货段话术沿用对标、
豁免查重），--keep-daihuo 可关闭剔除。

用法：
  python 工具/洗稿查重.py <原稿> <成品1> [成品2 ...] [--window 8] [--keep-scaffold] [--keep-daihuo]

行为：每个成品 vs 原稿逐一比对；给了多个成品时（一稿多洗），成品之间再两两比对。
任意一对有命中 → 退出码 1。
"""
import sys, re, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SCAFFOLD = re.compile(r"^\s*(第\s*[0-9一二三四五六七八九十百]+\s*场|画面|人物动作|台词)\s*[:：]?", re.M)
DAIHUO = re.compile(r"【进入带货段落】.*?【返回剧情主线】", re.S)

def load(path, strip_scaffold=True, strip_daihuo=True):
    text = io.open(path, encoding="utf-8").read()
    if strip_daihuo:
        text = DAIHUO.sub("", text)
    if strip_scaffold:
        text = SCAFFOLD.sub("", text)
    return re.sub(r"[^\w一-鿿]", "", text, flags=re.UNICODE).replace("_", "")

def find_hits(a, b, n):
    """b 中与 a 连续 n 字重合的片段（向右扩展到最长）"""
    grams = {}
    for i in range(len(a) - n + 1):
        grams.setdefault(a[i:i+n], []).append(i)
    hits = []
    i = 0
    while i <= len(b) - n:
        w = b[i:i+n]
        if w in grams:
            best = w
            for start in grams[w]:
                k, m = start + n, i + n
                while k < len(a) and m < len(b) and a[k] == b[m]:
                    k += 1; m += 1
                if m - i > len(best):
                    best = b[i:m]
            hits.append(best)
            i += len(best)
        else:
            i += 1
    return hits

def main():
    argv = sys.argv[1:]
    window, strip, strip_daihuo, args = 8, True, True, []
    i = 0
    while i < len(argv):
        x = argv[i]
        if x == "--window":
            window = int(argv[i + 1]); i += 2
        elif x.startswith("--window="):
            window = int(x.split("=", 1)[1]); i += 1
        elif x == "--keep-scaffold":
            strip = False; i += 1
        elif x == "--keep-daihuo":
            strip_daihuo = False; i += 1
        else:
            args.append(x); i += 1
    if len(args) < 2:
        print(__doc__); sys.exit(2)
    src, prods = args[0], args[1:]
    texts = {p: load(p, strip, strip_daihuo) for p in [src] + prods}
    pairs = [(src, p) for p in prods]
    for i in range(len(prods)):
        for j in range(i + 1, len(prods)):
            pairs.append((prods[i], prods[j]))
    failed = False
    for a, b in pairs:
        hits = find_hits(texts[a], texts[b], window)
        tag = "%s vs %s" % (a, b)
        if hits:
            failed = True
            print("[命中 %d] %s" % (len(hits), tag))
            for h in hits:
                print("  " + h)
        else:
            print("[通过] %s（无连续%d字重合）" % (tag, window))
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
