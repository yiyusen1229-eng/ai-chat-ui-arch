# -*- coding: utf-8 -*-
"""导演骨架（先不接模型）。

结构：大纲 → 卷章 → 幕 → 拍。
这一版只把**形状**定下来：

    plan(kind)      按长度给出一个"空壳计划"（幕数、每幕拍数、预算字数）
    beat_prompt()   拼出将来要发给导演模型的提示词（现在不发，只返回文本，便于检查）
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


def beat_prompt():
    """将来发给导演模型的提示词。现在只拼出来给界面/测试看，不发请求。"""
    c = cast.get_cast()
    lines = ["【本场阵容】"]
    for m in c.get("members", []):
        if m.get("role") == "extra":
            lines.append("- 龙套：%s（导演临时写的，不需要角色卡）" % m.get("name"))
            continue
        cid = m.get("id")
        ci = char_cards.info(cid)
        card = char_cards.read_card("director", cid)
        lines.append("- %s（%s，%s）" % (ci["name"], m.get("role"), "有导演卡" if ci["has_director"] else "缺导演卡"))
        if card.strip():
            lines.append("  导演卡摘要：" + card.strip().splitlines()[0][:80])
    if _state["direction"]:
        lines.append("【玩家给的走向】" + _state["direction"])
    p = _state["planned"] or plan()
    lines.append("【这一幕】第 %d 幕 / 共 %d 拍 / 每拍约 %d 字"
                 % (_state["act"], p["beats"], p["chars_per_beat"]))
    lines.append("【要求】给出这一拍：谁开口、说什么、这一拍想让玩家感受到什么；一句一拍，不要旁白式总结。")
    return "\n".join(lines)


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
