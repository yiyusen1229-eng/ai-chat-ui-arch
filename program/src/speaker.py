# -*- coding: utf-8 -*-
"""说话人上下文：一个 id → 这一拍「她能看到的全部」。

上下文隔离的落点。按设计文档 6.1，角色开口时只给：

    她自己的人设（角色卡）+ 私有记忆书 + 对在场者的态度
    + 公共最近 K 轮原话 + 这一拍的任务（含红线）

这里负责前三项里「按 id 解析」的部分（最近原话在 scenelog，任务由导演给）。
以前这三项和模型 / 语音 / 立绘各有各的入口：actor 自己拼 characters/<id>/card/，
cast 自己读 model.json，characters 又有 display_name / sprite_index。同一个 id
有四套解析，改一处漏三处。现在统一走 ctx()。

数据来源（都是已有的，这里只做归口，不另写读取器）：
    card      char_cards.read_card("director", cid)
    memory    characters/<id>/memory/*.txt   （char_res，按 mtime 热读）
    relations characters/<id>/relations.json （新文件，没有就当空）
    model     characters/<id>/model.json     （没有就回落全局 chat）
    voice     characters/<id>/voice/**/*.wav
    sprite    characters/<id>/portrait/
"""
import json
import os

import char_cards
import characters as chars

# 注入上限：给角色的一拍上下文要短，别把上下文吃光
CARD_MAX = 1200
MEMORY_MAX = 600
RELATIONS_MAX = 400
RELATION_LINE_MAX = 80

OWN_CACHE = {}          # {路径: (mtime, 读到的内容)}


# ------------------------------------------------------------------ 定位
def char_dir(cid):
    """角色文件夹：先按 id，再按显示名反查（存档 / 阵容里两种都出现过）"""
    if not cid:
        return ""
    try:
        root = chars.root()
    except Exception:
        return ""
    d = os.path.join(root, cid)
    if os.path.isdir(d):
        return d
    try:
        for name in os.listdir(root):
            if chars.display_name(name) == cid:
                p = os.path.join(root, name)
                if os.path.isdir(p):
                    return p
    except OSError:
        pass
    return ""


def name(cid):
    try:
        return chars.display_name(cid) or cid or ""
    except Exception:
        return cid or ""


# ------------------------------------------------------------------ 读文件（按 mtime 缓存）
def _read(path):
    if not path:
        return ""
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return ""
    hit = OWN_CACHE.get(path)
    if hit and hit[0] == mt:
        return hit[1]
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            val = f.read().strip()
    except Exception:
        val = ""
    OWN_CACHE[path] = (mt, val)
    return val


def _first_file(d, exts):
    """目录里第一个指定后缀的文件（导入端只留一份，所以按名字排序取第一个）"""
    if not d:
        return ""
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return ""
    for n in names:
        if os.path.splitext(n)[1].lower() in exts:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    return ""


# ------------------------------------------------------------------ 各项
def card(cid):
    """角色卡（给角色的那一份就是导演卡：150~300 字的压缩卡）"""
    try:
        return (char_cards.read_card("director", cid) or "").strip()[:CARD_MAX]
    except Exception:
        return ""


def memory(cid):
    """私有记忆书：这个角色「本来就知道」的旧事，每拍都带上"""
    try:
        import char_res
        return (char_res.memory_text(cid) or "")[:MEMORY_MAX]
    except Exception:
        return ""


def relations(cid):
    """对在场者的态度：{"雪菜": "嘴上不承认，其实还气她"}

    文件不合规就当没有：读坏了的角色不该因此演不出来。
    """
    d = char_dir(cid)
    p = os.path.join(d, "relations.json") if d else ""
    raw = _read(p)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    out = {}
    if isinstance(data, dict):
        for k, v in data.items():
            k = str(k or "").strip()
            if not k:
                continue
            if isinstance(v, dict):
                v = v.get("text") or v.get("attitude") or ""
            v = str(v or "").strip()
            if v:
                out[k] = v[:RELATION_LINE_MAX]
    elif isinstance(data, list):
        for it in data:
            if not isinstance(it, dict):
                continue
            k = str(it.get("name") or it.get("id") or "").strip()
            v = str(it.get("text") or it.get("attitude") or "").strip()
            if k and v:
                out[k] = v[:RELATION_LINE_MAX]
    return out


def relations_text(cid, present=None, max_chars=RELATIONS_MAX):
    """只写「在场的人」怎么看她 —— 不在场的不给，天然少串味"""
    rel = relations(cid)
    if not rel:
        return ""
    allow = None
    if present:
        allow = set(x for x in present if x)
        allow.add(name(cid))
    rows = []
    for k, v in rel.items():
        if allow is not None and k not in allow:
            continue
        rows.append("· %s：%s" % (k, v))
    text = "\n".join(rows)
    return text[:max_chars]


def model(cid):
    """这个角色的聊天模型；没配就回落全局 chat（和导演同一套回落逻辑）"""
    d = char_dir(cid)
    p = os.path.join(d, "model.json") if d else ""
    try:
        if p and os.path.isfile(p):
            raw = json.loads(_read(p) or "{}")
            sec = (raw or {}).get("chat") or {}
            if sec.get("key") or sec.get("endpoint"):
                return {"base_url": sec.get("endpoint") or "",
                        "api_key": sec.get("key") or "",
                        "model": sec.get("model") or "",
                        "own": True}
    except Exception:
        pass
    try:
        import main
        g = (main.model_cfg_all() or {}).get("chat") or {}
        return {"base_url": g.get("endpoint") or "", "api_key": g.get("key") or "",
                "model": g.get("model") or "", "own": False}
    except Exception:
        return {"base_url": "", "api_key": "", "model": "", "own": False}


def has_chat(cid):
    """这个角色有没有配自己的聊天模型（界面上的「已接模型」）"""
    d = char_dir(cid)
    p = os.path.join(d, "model.json") if d else ""
    try:
        if p and os.path.isfile(p):
            sec = (json.loads(_read(p) or "{}") or {}).get("chat") or {}
            return bool(sec.get("connected"))
    except Exception:
        pass
    return False


def voice_ready(cid):
    """有没有可用的音色素材（voice/ 下至少一个 wav，情绪子目录也算）"""
    d = char_dir(cid)
    root = os.path.join(d, "voice") if d else ""
    if not root or not os.path.isdir(root):
        return False
    for _dp, _dn, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".wav"):
                return True
    return False


def portrait_dir(cid):
    d = char_dir(cid)
    return os.path.join(d, "portrait") if d else ""


def ctx(cid, present=None):
    """一次拿全：给 actor 拼提示词、给 cast.status 显示状态，都用它"""
    d = char_dir(cid)
    c = card(cid)
    m = model(cid)
    return {
        "id": cid,
        "name": name(cid),
        "dir": d,
        "card": c,
        "has_card": bool(c),
        "memory": memory(cid),
        "relations": relations(cid),
        "relations_text": relations_text(cid, present),
        "model": m,
        "has_chat": has_chat(cid),
        "model_name": (m or {}).get("model") or "",
        "has_voice": voice_ready(cid),
        "portrait_dir": portrait_dir(cid),
    }
