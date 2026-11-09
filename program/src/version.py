# -*- coding: utf-8 -*-
"""版本号与更新检查的常量（发布时改这里 + 仓库根目录 version.json）。"""

VERSION = "2026-11-09.1"                     # 本机版本：YYYY-MM-DD.N
REPO = "yiyusen1229-eng/ai-chat-ui-arch"     # GitHub 仓库
RAW_VERSION_URL = "https://raw.githubusercontent.com/%s/master/version.json" % REPO
REPO_URL = "https://github.com/%s" % REPO
PATCH_URL = "https://github.com/%s/tree/master/other/docs/patches" % REPO


def ver_key(v):
    """'2026-11-04.2' -> (2026, 11, 4, 2)；解析失败返回 (0,0,0,0)"""
    try:
        date, _, n = str(v).partition(".")
        y, m, d = (int(x) for x in date.split("-"))
        return (y, m, int(d), int(n or 0))
    except Exception:
        return (0, 0, 0, 0)

