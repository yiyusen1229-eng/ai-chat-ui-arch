# -*- coding: utf-8 -*-
"""导演骨架（先不接模型）。

结构：大纲 → 卷章 → 幕 → 拍。
这一版只把**形状**定下来：

    plan(kind)      按长度给出一个"空壳计划"（幕数、每幕拍数、预算字数）
    stub_beats()    没有模型时用来跑通链路的假数据

模型接进来之后，只需要在 plan() / 下一拍 里换成真实调用，其它层（队列、前端）不用动。
"""
import time

import cast
import char_cards

# 三档长度：幕 / 每幕拍数 / 每拍目标字数
LENGTHS = {
    "small":  {"acts": 1, "beats": 7,  "chars": 350, "tokens": 400},
    "medium": {"acts": 1, "beats": 12, "chars": 700, "tokens": 800},
    "large":  {"acts": 1, "beats": 25, "chars": 1500, "tokens": 1600}
}

_state = {
    "enabled": False,          # 多角色总开关
    "auto": True,              # 自动续写
    "length": "small",
    "direction": "",           # 玩家输入的剧情大致走向
    "act": 0,
    "beat": 0,
    "planned": None,           # plan() 的结果
    "started_at": 0
}


def set_options(enabled=None, auto=None, length=None, direction=None):
    if enabled is not None:
        _state["enabled"] = bool(enabled)
    if auto is not None:
        _state["auto"] = bool(auto)
    if length in LENGTHS:
        _state["length"] = length
    if direction is not None:
        _state["direction"] = str(direction)
    return {"ok": True, "state": status()}


def plan(length=None):
    """空壳计划：只算幕/拍/字数预算，不生成内容"""
    key = length if length in LENGTHS else _state["length"]
    spec = LENGTHS[key]
    _state["planned"] = {
        "length": key,
        "acts": spec["acts"],
        "beats": spec["beats"],
        "chars_per_beat": spec["chars"],
        "tokens": spec["tokens"],
        "created": time.time()
    }
    _state["act"] = 1
    _state["beat"] = 0
    _state["started_at"] = time.time()
    return dict(_state["planned"])


def stub_beats(n=2):
    """没有模型时跑通链路用的假数据：把在场角色轮流点一遍"""
    out = []
    members = [m for m in cast.get_cast().get("members", []) if m.get("role") != "extra"]
    for i in range(n):
        if not members:
            out.append({"speaker": "", "text": "（还没有安排角色）", "type": "narr", "voice": False})
            continue
        m = members[i % len(members)]
        name = cast.chars.display_name(m.get("id"))
        out.append({"speaker": name,
                    "text": "（占位台词 %d）这一拍还没接模型，先用假数据把队列和界面跑通。" % (i + 1),
                    "type": "say", "voice": False})
    return out


def _director_model():
    """从当前角色的 model.json 里读导演模型（没配就空串）"""
    try:
        import char_model
        st = char_model.get_state()
        d = (st.get("kinds") or {}).get("director") or {}
        return {
            "model": d.get("model") or "",
            "connected": bool(d.get("connected")),
            "usingGlobal": bool(d.get("usingGlobal")),
            "endpoint": d.get("endpoint") or ""
        }
    except Exception:
        return {"model": "", "connected": False, "usingGlobal": True, "endpoint": ""}


def status():
    c = cast.get_cast()
    return {
        "enabled": _state["enabled"],
        "auto": _state["auto"],
        "length": _state["length"],
        "direction": _state["direction"],
        "act": _state["act"],
        "beat": _state["beat"],
        "planned": _state["planned"],
        "cast": c,
        "model": _director_model()
    }
