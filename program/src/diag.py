# -*- coding: utf-8 -*-
"""调试面板的只读状态账本（#13）。

面板只需要回答四个问题：这一轮提示词花在哪、命中了什么、缓存有没有用上、
分类器 / 关系阶段现在什么状态。所以这里只记**数字和标签**——不记对话正文、
不记密钥（面板不是聊天记录查看器，也不该成为脱敏漏洞）。

写法：写的一方（api_chat / api_text / tts_client / …）只管往里塞，
读的一方 `/api/debug/state` 一次打包。HTTP 服务是多线程的，所以都加锁。

对外：
    count(name, n=1)              计数器（命中 / 渲染 / 轮次）
    note(kind, **kw)              模块状态（覆盖写，只留最近一次）
    note_get(kind)                读某个模块的状态
    round_record(**kw)            一轮对话的指标（滚动保留 KEEP 条）
    snapshot()                    面板用的整包（/api/debug/state 原样返回）
    token_est(s)                  近似 token（规则同 memory.est_text）
    reset()                       自测用：清空
"""
import collections
import os
import threading
import time

KEEP = 8                         # 面板只看最近 8 轮，再多也没人看

_LOCK = threading.RLock()
_COUNTERS = collections.Counter()
_NOTES = {}
_ROUNDS = collections.deque(maxlen=KEEP)
_START = time.time()


# ------------------------------------------------------------------ 写入

def count(name, n=1):
    """计数器 +n（名字用 `域.事件`：text.disk / tts.hit / chat.round …）"""
    if not name:
        return
    with _LOCK:
        _COUNTERS[name] += n


def counters():
    with _LOCK:
        return dict(_COUNTERS)


def note(kind, **kw):
    """记某个模块的最近状态（覆盖写）"""
    if not kind:
        return
    kw["at"] = int(time.time())
    with _LOCK:
        _NOTES[kind] = kw


def note_get(kind):
    with _LOCK:
        return dict(_NOTES.get(kind) or {})


def round_record(**kw):
    """记一轮对话的指标；只保留最近 KEEP 条"""
    kw["at"] = int(time.time())
    with _LOCK:
        _ROUNDS.append(kw)
        _COUNTERS["chat.round"] += 1


def rounds():
    with _LOCK:
        return [dict(r) for r in _ROUNDS]


def reset():
    with _LOCK:
        _COUNTERS.clear()
        _NOTES.clear()
        _ROUNDS.clear()


def token_est(s):
    """近似 token。规则统一走 memory.est_text（中日文 1 字 ≈ 1 token）"""
    if not s:
        return 0
    try:
        import memory
        return memory.est_text(s)
    except Exception:
        return int(len(s) * 0.6)


def uptime_s():
    return int(time.time() - _START)


# ------------------------------------------------------------------ 各模块快照

def _server():
    out = {"pid": os.getpid(), "uptime_s": uptime_s()}
    try:
        import version
        out["version"] = version.VERSION
    except Exception:
        out["version"] = ""
    out["assets_env"] = bool(os.environ.get("WA2_ASSETS_DIR"))
    out["sess_env"] = bool(os.environ.get("WA2_SESS_DIR"))
    # 数据根只报名字和环境变量名（账本里只许短标签，完整路径见启动日志「数据根:」）
    try:
        import datadir
        info = datadir.info()
        out["data"] = {
            "root": os.path.basename(info.get("root") or "") or "?",
            "env": "WA2_DATA" if info.get("data_env") else "",
            "characters": info["characters"]["source"],
            "cache": info["cache"]["source"],
            "logs": info["logs"]["source"],
        }
    except Exception:
        out["data"] = {}
    return out


def _memory_block():
    """对话记忆书：条目数 / 覆盖到第几轮 / 空洞 / 三档预算"""
    try:
        import memory
        r = memory.runtime() or {}
        trig, recent_b, entries_b = memory.budget(True)
        entries = r.get("entries") or []
        h = memory.runtime_holes()
        levels = {}
        for e in entries:
            lv = str(int((e or {}).get("level") or 0))
            levels[lv] = levels.get(lv, 0) + 1
        return {
            "entries": len(entries),
            "max_entries": int(memory.cfg().get("max_entries") or 0),
            "max_summary_entries": int(memory.cfg().get("max_summary_entries") or 0),
            "levels": levels,
            "covered": int(r.get("covered") or 0),
            "covered_derived": int(h.get("derived") or 0),
            "earliest_from": int(h.get("earliest") or 0),
            "hole": int(h.get("hole") or 0),
            "turns": int(r.get("turns") or 0),
            "packs": int(r.get("packs") or 0),
            "window_start": int(r.get("window_start") or 0),
            "budget": {"trigger": trig, "recent": recent_b, "entries": entries_b},
            "slot": r.get("slot") or "",
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def _stage_block():
    """关系阶段分类器：档位 / 轮数 / 是否在后台跑 / 下次什么时候判"""
    try:
        import persona_stage
        if hasattr(persona_stage, "debug_state"):
            return persona_stage.debug_state()
        st = persona_stage._load()
        return dict(st)
    except Exception as e:
        return {"error": str(e)[:80]}


def _tts_block():
    try:
        import tts_client, audio_gain
        st = tts_client.status() or {}
        return {
            "available": bool(st.get("available")),
            "ready": bool(st.get("ready")),
            "error": (st.get("error") or "")[:60],
            "last": note_get("tts"),
            "gain_memo": (audio_gain.stats() or {}).get("memo"),
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def _cache_block():
    out = {}
    try:
        import cache_io
        out.update(cache_io.stats())
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


def _story_block():
    """原作检索：最近一次命中（只要出处和分数，不返回正文）"""
    out = {}
    try:
        import story_rag
        out.update(story_rag.last_hits() or {})
        out["enabled"] = bool(story_rag.enabled())
        out["docs"] = story_rag.cached_blocks()     # 只报已加载的块数，不触发加载
    except Exception as e:
        out["error"] = str(e)[:80]
    return out


def _scene_block():
    return dict(note_get("scene") or {})


def _perf_block():
    """最近一次性能采样（#14）：内存 / 线程 / 句柄 / GDI / 缓存体积"""
    try:
        import perf
        return dict(perf.last() or {})
    except Exception as e:
        return {"error": str(e)[:80]}


def _route_block():
    """实际模型路由：模型名 / 主机 / 来源（请求 / 角色配置 / 全局设置）

    2026-11-23：用户报「配的是 OpenCode，DeepSeek 官网还在扣钱」——以前聊天请求
    没带模型、也没有路由层，界面和实际用的对不上。这一块就是给面板/排查看的。
    """
    try:
        import main
        from urllib.parse import urlparse
        r = main.chat_route() or {}
        u = urlparse(r.get("base_url") or "")
        return {"model": r.get("model") or "", "host": u.netloc or "",
                "source": r.get("source") or "", "has_key": bool(r.get("api_key"))}
    except Exception as e:
        return {"error": str(e)[:80]}


# ------------------------------------------------------------------ 面板整包

def snapshot():
    """`/api/debug/state` 的返回值。全部只读，不改任何运行时状态。"""
    return {
        "ok": True,
        "server": _server(),
        "counters": counters(),
        "rounds": rounds(),
        "route": _route_block(),
        "modules": {
            "memory": _memory_block(),
            "stage": _stage_block(),
            "tts": _tts_block(),
            "story": _story_block(),
            "scene": _scene_block(),
            "perf": _perf_block(),
            "cache": _cache_block(),
        },
    }
