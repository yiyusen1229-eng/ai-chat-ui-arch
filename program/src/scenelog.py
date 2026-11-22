# -*- coding: utf-8 -*-
"""本场已经播出去的原话（环形缓冲）。

作用只有一个：给角色当「刚才发生的事」。
上下文隔离里，每个角色只能看到**公共**的最近几句台词 / 旁白，
看不到别人的私有记忆和内心（设计文档 6.4）。

为什么记在 pop 而不在 push：
    队列会预生成 2~3 拍，push 进去的还没播，玩家根本没看见；
    pop 才是「真的播出去了」，拿来当"刚才"才准。

一条：{"speaker": "和纱", "text": "...", "type": "say"|"narr", "at": 时间戳}
"""
import threading
import time

MAX_LINES = 200         # 留够读档后接着演的量（存档动辄两三百条）
RECENT_K = 20           # 默认给角色看最近 20 条
RECENT_CHARS = 1500     # 拼成一段文字时的上限（约 750 token）

_lock = threading.Lock()
_lines = []


def add(speaker, text, typ="say"):
    """记一条播出去的（旁白 speaker 为空）"""
    t = (text or "").strip()
    if not t:
        return
    with _lock:
        _lines.append({
            "speaker": (speaker or "").strip(),
            "text": t,
            "type": (typ or "say"),
            "at": time.time(),
        })
        if len(_lines) > MAX_LINES:
            del _lines[0:len(_lines) - MAX_LINES]


def recent(k=RECENT_K):
    """最近 k 条（时间正序：旧的在前）"""
    with _lock:
        n = max(1, int(k or 1))
        return [dict(x) for x in _lines[-n:]]


def rows_from(messages, player="你", limit=200, default_speaker=""):
    """把一段对话历史（存档 / 前端 window.__history）变成场景原话。

    多角色不再单独维护"刚才发生的事"：前端每次请求导演都带上 history，
    和 /api/chat 一个路子 —— 读档后天然接得上，不用另做 seed、也不用另记玩家的话。

    取中文那侧（zh 优先，老档没有就用 content）；玩家消息记在玩家名下；旁白不带名字。
    default_speaker：assistant 消息没有 speaker 时算谁的（单角色老存档全是这个情况，
    那时候说话的是**当前角色**，不是玩家 —— 算成玩家的话整段历史都变成"你在自说自话"）。
    """
    rows = []
    for m in (messages or [])[-max(1, int(limit or 1)):]:
        if not isinstance(m, dict):
            continue
        text = (m.get("zh") or m.get("content") or "").strip()
        if not text:
            continue
        if m.get("role") == "user":
            rows.append({"speaker": player or "你", "text": text, "type": "say"})
        elif m.get("narr"):
            rows.append({"speaker": "", "text": text, "type": "narr"})
        else:
            who = (m.get("speaker") or "").strip() or (default_speaker or "").strip() \
                  or (player or "你")
            rows.append({"speaker": who, "text": text, "type": "say"})
    return rows


def render(rows, max_chars=0):
    """把行拼成给模型看的一段：【刚才发生的事】用。

    台词写成「名字：「…」」，旁白直接一行；太长从最旧的那头截掉。
    """
    out = []
    for x in rows or []:
        sp = x.get("speaker") or ""
        if x.get("type") == "narr" or not sp:
            out.append(x.get("text") or "")
        else:
            out.append("%s：「%s」" % (sp, x.get("text") or ""))
    text = "\n".join(out)
    if max_chars and len(text) > max_chars:
        text = text[-max_chars:]
        nl = text.find("\n")
        if 0 <= nl < len(text) - 1:
            text = text[nl + 1:]          # 别从半句话开始
    return text


def as_text(k=RECENT_K, max_chars=RECENT_CHARS):
    """拼成给模型看的一段：【刚才发生的事】用。"""
    rows = recent(k)
    out = []
    for x in rows:
        sp = x.get("speaker") or ""
        if x.get("type") == "narr" or not sp:
            out.append(x.get("text") or "")
        else:
            out.append("%s：「%s」" % (sp, x.get("text") or ""))
    text = "\n".join(out)
    if max_chars and len(text) > max_chars:
        text = text[-max_chars:]
        nl = text.find("\n")
        if 0 <= nl < len(text) - 1:
            text = text[nl + 1:]          # 别从半句话开始
    return text


def clear():
    with _lock:
        n = len(_lines)
        _lines.clear()
    return n


def seed(messages, player="你", limit=200):
    """用一段已有的历史把「刚才发生的事」铺回来（读档时的兜底）。

    正常路径已经不需要它了：前端每次请求导演都带上 history。
    留着是为了「没带 messages 的调用」（队列占位、脚本、自测）也有上下文。
    返回铺了几条。
    """
    rows = rows_from(messages, player, limit)
    with _lock:
        _lines.clear()
        for r in rows[-MAX_LINES:]:
            _lines.append({"speaker": r["speaker"], "text": r["text"],
                           "type": r["type"], "at": time.time()})
    return len(rows)


def stats():
    with _lock:
        return {"lines": len(_lines), "max": MAX_LINES,
                "first": _lines[0]["text"][:40] if _lines else "",
                "last": _lines[-1]["text"][:40] if _lines else ""}
