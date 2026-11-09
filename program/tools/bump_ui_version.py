# -*- coding: utf-8 -*-
"""把 index.html 里所有 ?v= 版本号统一换成新的，避免 WebView2 用缓存里的旧脚本。

以后每次改前端，跑一次这个脚本（或手动改版本号）再重启程序。
"""
import io
import re
import sys

STAMP = sys.argv[1] if len(sys.argv) > 1 else "202611091640"

P = r"program/src/ui/index.html"
t = io.open(P, encoding="utf-8").read()

new_t, n = re.subn(r"\?v=[0-9A-Za-z]+", "?v=" + STAMP, t)
io.open(P, "w", encoding="utf-8").write(new_t)
print("已替换 %d 处 → ?v=%s" % (n, STAMP))
