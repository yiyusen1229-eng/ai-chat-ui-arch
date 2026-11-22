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

import scenelog

_lock = threading.Lock()
_items = []
_seq = itertools.count(1)
_state = {"paused": False, "playing": False, "generating": False}


def push(lines):
    """批量入队，返回写入条数。

    日语原文（ja）和情绪（emotion）必须原样带过去：前端配音拿 `ja` 送 TTS，
    这里要是丢了，就会拿中文字幕去合成日语 —— 听上去像换了个人。
    """
    n = 0
    with _lock:
        for ln in (lines or []):
            if not isinstance(ln, dict) or not (ln.get("text") or "").strip():
                continue
            item = {
                "id": next(_seq),
                "speaker": ln.get("speaker") or "",
                # 说话人的角色 id：立绘 / 音色都按它找；speaker 只负责显示名字
                "cid": ln.get("cid") or "",
                "text": ln.get("text") or "",
                "type": ln.get("type") or "say",
                "voice": bool(ln.get("voice", True)),
                "ja": ln.get("ja") or "",
                "emotion": ln.get("emotion") or "",
                # 这一拍是不是成人场面：跟着行走，才不会"实时用平常语气、
                # 回想里反而对"（回想那边本来就有场面标记）
                "hscene": bool(ln.get("hscene", False)),
                "at": time.time()
            }
            _items.append(item)
            n += 1
    return n


def peek():
    with _lock:
        return dict(_items[0]) if _items else None


def pending(n=2):
    """还没播的前 n 条（不取走）。预合成用：看一眼后面要念什么。"""
    k = max(0, int(n or 0))
    with _lock:
        return [dict(x) for x in _items[:k]]


def pop():
    """取一条（跳过就是调它）

    这里是「真的播出去了」的时刻，顺手记进 scenelog：
    下一拍角色开口时，"刚才发生的事"就取这里，而不是还没播的预生成队列。
    """
    with _lock:
        if not _items:
            return None
        it = dict(_items.pop(0))
    scenelog.add(it.get("speaker"), it.get("text"), it.get("type"))
    return it


def clear(reset_scene=False):
    """清空队列。

    注意：默认**不动 scenelog**。前端每次玩家发话都会清一次队列（为了丢掉上一轮
    没播完的预生成），要是顺手把"刚才发生的事"也清了，导演和角色每一轮都从零开始
    —— 表现就是「每轮对话接不上」。只有真的换场（读档 / 换角色）才传 reset_scene。
    """
    with _lock:
        n = len(_items)
        _items.clear()
        _state["playing"] = False
    if reset_scene:
        scenelog.clear()
    return {"ok": True, "cleared": n, "scene_reset": bool(reset_scene)}


def snapshot():
    with _lock:
        return {
            "pending": len(_items),
            "head": dict(_items[0]) if _items else None,
            "paused": _state["paused"],
            "playing": _state["playing"],
            "generating": _state["generating"]
        }
