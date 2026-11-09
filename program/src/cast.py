# -*- coding: utf-8 -*-
"""场次阵容：这一场有哪些人、谁是主角、谁是龙套。

只做状态与校验，不生成内容（生成在 director.py 里，模型调用另接）。

数据结构（内存，暂不落盘；存档要带 cast 时再序列化）：
    {
      "members": [
          {"id": "和纱", "role": "protagonist"},   # 玩家扮演
          {"id": "雪菜", "role": "actor"},         # 能被导演调度
          {"name": "路人A", "role": "extra"}       # 龙套：导演现写，不进角色库
      ],
      "turns": 0
    }
"""
import io
import json
import os

import char_cards
import characters as chars

_state = {"members": [], "turns": 0}

ROLE_NAMES = {"protagonist": "主角", "actor": "角色", "extra": "龙套"}


def _char_dir(cid):
    return os.path.join(chars.root(), cid) if cid else ""


def _model_state(cid):
    """读某个角色自己的模型配置（没有就返回未配置）"""
    p = os.path.join(_char_dir(cid), "model.json")
    out = {"chat": False, "voice": False, "model": ""}
    if not cid or not os.path.isfile(p):
        return out
    try:
        d = json.loads(io.open(p, encoding="utf-8").read()) or {}
    except Exception:
        return out
    chat = d.get("chat") or {}
    voice = d.get("voice") or {}
    out["chat"] = bool(chat.get("connected"))
    out["voice"] = bool(voice.get("connected") or voice.get("endpoint"))
    out["model"] = chat.get("model") or ""
    return out


def _voice_ready(cid):
    """有没有可用的音色素材（voice/ 下至少一个 wav）"""
    d = os.path.join(_char_dir(cid), "voice")
    if not os.path.isdir(d):
        return False
    for root, _dirs, files in os.walk(d):
        for f in files:
            if f.lower().endswith(".wav"):
                return True
    return False


def get_cast():
    return {"members": [dict(m) for m in _state["members"]], "turns": _state["turns"]}


def set_cast(members):
    """整表替换。只收合法条目，龙套不需要角色包。"""
    clean = []
    for m in (members or []):
        if not isinstance(m, dict):
            continue
        role = m.get("role") or "actor"
        if role not in ROLE_NAMES:
            continue
        if role == "extra":
            name = (m.get("name") or "").strip()
            if name:
                clean.append({"name": name, "role": "extra"})
            continue
        cid = (m.get("id") or "").strip()
        if not cid or not os.path.isdir(_char_dir(cid)):
            continue
        clean.append({"id": cid, "role": role})
    _state["members"] = clean
    return {"ok": True, "cast": get_cast()}


def add_member(cid, role="actor"):
    for m in _state["members"]:
        if m.get("id") == cid:
            m["role"] = role
            return {"ok": True}
    return set_cast(_state["members"] + [{"id": cid, "role": role}])


def remove_member(cid):
    _state["members"] = [m for m in _state["members"] if m.get("id") != cid]
    return {"ok": True}


def clear():
    _state["members"] = []
    _state["turns"] = 0
    return {"ok": True}


def protagonist():
    for m in _state["members"]:
        if m.get("role") == "protagonist":
            return m
    return None


def actors():
    return [m for m in _state["members"] if m.get("role") in ("actor", "protagonist")]


def status():
    """给标题栏菜单用：每个成员的卡片/模型/语音状态"""
    out = []
    for m in _state["members"]:
        if m.get("role") == "extra":
            out.append({"name": m.get("name"), "role": "extra", "label": ROLE_NAMES["extra"],
                        "chat": False, "voice": False, "model": "", "has_card": False})
            continue
        cid = m.get("id")
        ms = _model_state(cid)
        ci = char_cards.info(cid)
        out.append({
            "id": cid, "name": ci["name"], "role": m.get("role"),
            "label": ROLE_NAMES.get(m.get("role"), m.get("role")),
            "chat": ms["chat"], "voice": ms["voice"] or _voice_ready(cid),
            "model": ms["model"], "has_card": ci["has_director"]
        })
    return {
        "members": out,
        "turns": _state["turns"],
        "protagonist": (protagonist() or {}).get("id", ""),
        "can_join": [{"id": c["id"], "name": chars.display_name(c["id"]),
                      "ok": char_cards.can_join_cast(c["id"])}
                     for c in chars.list_characters()]
    }
