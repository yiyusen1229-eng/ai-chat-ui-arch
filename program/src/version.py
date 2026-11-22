# -*- coding: utf-8 -*-
"""版本号与更新检查的常量（发布时改这里 + 仓库根目录 version.json）。"""

VERSION = "2026-11-22.4"                     # 本机版本：YYYY-MM-DD.N
# 发布时**必须**和仓库根 version.json 一起改（tools/test_update.py 会验一致性）。
# 2026-11-22.1 那次只写了更新日志、没 bump 这两个文件，结果远端 version.json 一直停在
# 2026-11-10.1，所有老客户端最多只能「看到」11.10 —— 用户实测反馈的「只弹到 11.10」。
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

