# -*- coding: utf-8 -*-
"""角色卡：给导演看的卡 / 给角色自己看的卡 / 主角卡。

多角色系统里，模型不是读"角色包"，而是读这三张卡：

    characters/<id>/card/director.md    角色卡（给导演看的 150~300 字压缩卡）
    characters/<id>/card/protagonist.md 主角卡：玩家扮演谁（临时放这里，将来进 player/）

约定：
  · director.md 缺失 → 这个角色不能进多角色场次（导演不知道该怎么调度它）；
  · 文件名叫 director.md 只是因为它给导演用；对用户/文档一律叫「角色卡」。

这里只负责读写与校验，不碰模型。
"""
import io
import os

import characters as chars

KINDS = ("director", "protagonist")
TITLES = {"director": "角色卡", "protagonist": "主角卡"}


def card_dir(cid=None):
    base = chars.active_dir() if cid is None else os.path.join(chars.root(), cid)
    return os.path.join(base, "card") if base else ""


def card_path(kind, cid=None):
    if kind not in KINDS:
        return ""
    d = card_dir(cid)
    return os.path.join(d, kind + ".md") if d else ""


def read_card(kind, cid=None):
    p = card_path(kind, cid)
    if not p or not os.path.isfile(p):
        return ""
    try:
        return io.open(p, encoding="utf-8").read()
    except Exception:
        return ""


def write_card(kind, text, cid=None):
    p = card_path(kind, cid)
    if not p:
        return {"ok": False, "error": "没有角色目录"}
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8", newline="").write(text or "")
        return {"ok": True, "path": p}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def has_card(kind, cid=None):
    p = card_path(kind, cid)
    return bool(p and os.path.isfile(p) and os.path.getsize(p) > 0)


def info(cid=None):
    """给界面/场次用的卡片状态"""
    return {
        "id": cid or chars.active_id(),
        "name": chars.display_name(cid),
        "has_director": has_card("director", cid),
        "has_protagonist": has_card("protagonist", cid),
        "dir": card_dir(cid)
    }


def can_join_cast(cid=None):
    """能不能进多角色场次：必须有导演卡"""
    return has_card("director", cid)
