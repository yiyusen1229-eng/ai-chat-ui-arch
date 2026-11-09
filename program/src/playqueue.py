# -*- coding: utf-8 -*-
"""播放队列（后端内存）：导演排好的「拍」先进这里，前端一条条取。

为什么放后端：前端刷新/切窗口不该把已经生成好的东西丢掉；
另外跳过的时候要能立刻换下一条，前端只负责显示。

现在只做队列本身，不接模型：谁往里 push 由 director.py 决定。

一条记录：
    {"id": 1, "speaker": "和纱", "text": "...", "type": "say"|"narr"|"act", "voice": true}
"""
import itertools
import threading
import time

_lock = threading.Lock()
_items = []
_seq = itertools.count(1)
_state = {"paused": False, "playing": False, "generating": False}


def push(lines):
    """批量入队，返回写入条数"""
    n = 0
    with _lock:
        for ln in (lines or []):
            if not isinstance(ln, dict) or not (ln.get("text") or "").strip():
                continue
            item = {
                "id": next(_seq),
                "speaker": ln.get("speaker") or "",
                "text": ln.get("text") or "",
                "type": ln.get("type") or "say",
                "voice": bool(ln.get("voice", True)),
                "at": time.time()
            }
            _items.append(item)
            n += 1
    return n


def peek():
    with _lock:
        return dict(_items[0]) if _items else None


def pop():
    """取一条（跳过就是调它）"""
    with _lock:
        return dict(_items.pop(0)) if _items else None


def clear():
    with _lock:
        n = len(_items)
        _items.clear()
        _state["playing"] = False
    return {"ok": True, "cleared": n}


def snapshot():
    with _lock:
        return {
            "pending": len(_items),
            "head": dict(_items[0]) if _items else None,
            "paused": _state["paused"],
            "playing": _state["playing"],
            "generating": _state["generating"]
        }


def set_flag(name, value):
    if name in _state:
        _state[name] = bool(value)
    return {"ok": True, "state": dict(_state)}
