# -*- coding: utf-8 -*-
"""预合成：把队列里接下来要播的一两句先合成好，播到时直接命中缓存。

为什么要这个：文字是提前取的（导演一次推 2~3 拍，前端还会预取），
**音频却是"播到哪合成到哪"** —— 长句合成实测 17~20 秒（`wt-voice.js` 里记着），
自动模式或快进时每句都要先等这一下，听起来就是"卡"。

这里的做法：`/api/play/next` 取走一条之后，把后面还没播的那两条送去合成。
玩家正在听当前这句，合成锁（`tts_client._synth_lock`）被它拿着，预合成排在后面，
等它播完，下一句的音频往往已经躺进磁盘缓存了。

规矩：
  · **只在有人听的时候预热**：由取队列那条触发，不在生成台词时就抢（那会和刚播的这句抢锁）
  · 一次一个线程、一句一句来（GPT-SoVITS 本身串行）
  · 排过的就记住，同样的文本 + 情绪 + 角色不重复排
  · `WA2_TTS_PREFETCH=0` 或语音没就绪 / 安全模式 → 什么也不做
  · 失败静默：预合成失败不影响播放，真播时还会再试一次
"""
import os
import threading
import time

QUEUE_MAX = 6          # 最多攒这么多条没做的（再多也没意义：玩家早跳过去了）
DEFAULT_N = 2          # 每次往后看几句

_LOCK = threading.Lock()
_JOBS = []             # [(文本, 情绪, 角色id)]
_SEEN = set()          # 排过 / 做过的 key
_WORKER = None
_CFG = {"enabled": True, "synth": None, "ready": None}
_LAST = {"at": "", "text": "", "ok": None, "ms": 0, "done": 0, "skip": 0, "err": ""}


def _env_on():
    return (os.environ.get("WA2_TTS_PREFETCH") or "1") != "0"


def configure(enabled=True, synth=None, ready=None):
    """main 启动时注入：合成函数和"语音就绪了吗"。

    不直接 import tts_client —— 自测里能换成假的，也不会把语音服务拉起来。
    """
    on = bool(enabled) and _env_on()
    with _LOCK:
        _CFG["enabled"] = on
        _CFG["synth"] = synth
        _CFG["ready"] = ready
        _JOBS[:] = []
        _SEEN.clear()
    return on


def enabled():
    return bool(_CFG["enabled"]) and _env_on()


def _key(ja, emo, cid):
    return ((ja or "").strip(), (emo or "").strip(), (cid or "").strip())


def kick(lines, n=DEFAULT_N):
    """给接下来要播的几条排队；返回真正排进去的条数"""
    if not enabled():
        return 0
    synth = _CFG.get("synth")
    if not synth:
        return 0
    r = _CFG.get("ready")
    if r:
        try:
            if not r():
                return 0
        except Exception:
            return 0
    added = 0
    with _LOCK:
        for ln in (lines or [])[:max(0, int(n or 0))]:
            if not isinstance(ln, dict):
                continue
            ja = (ln.get("ja") or "").strip()
            if not ja:                      # 旁白 / 玩家：没有日语，不用预热
                continue
            if (ln.get("type") or "say") != "say":
                continue
            k = _key(ja, ln.get("emotion"), ln.get("cid"))
            if k in _SEEN:
                continue
            if len(_JOBS) >= QUEUE_MAX:
                break
            _SEEN.add(k)
            _JOBS.append(k)
            added += 1
    if added:
        _ensure_worker()
    return added


def _ensure_worker():
    global _WORKER
    with _LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_loop, name="tts-prefetch", daemon=True)
        _WORKER.start()


def _loop():
    global _WORKER
    while True:
        with _LOCK:
            if not _JOBS:
                _WORKER = None
                return
            text, emo, cid = _JOBS.pop(0)
        t0 = time.time()
        ok, err = False, ""
        try:
            # 成人场面里情绪一律按 H 念：预合成要是按 calm 合成，实时播放就会命中
            # 这段"平常语气"的缓存 —— 用户看到的就是"第一次平常、回想才是 H"
            _emo = emo or "neutral"
            try:
                import hscene
                _emo = hscene.effective_emotion(_emo)
            except Exception:
                pass
            data = _CFG["synth"](text, "ja", emo=_emo, char=cid or "")
            ok = bool(data)
            if not ok:
                err = "合成返回空"
        except Exception as e:
            err = str(e)[:80]
        with _LOCK:
            _LAST.update({"at": time.strftime("%H:%M:%S"), "text": text[:24],
                          "ok": ok, "err": err, "ms": int((time.time() - t0) * 1000),
                          "done": _LAST["done"] + (1 if ok else 0),
                          "skip": _LAST["skip"] + (0 if ok else 1)})


def clear():
    """换场 / 清空队列：没做的就别做了（已经合成好的音频留着，下次照样命中）"""
    with _LOCK:
        n = len(_JOBS)
        _JOBS[:] = []
        _SEEN.clear()
    return n


def drain(timeout=10.0):
    """等队列跑完（自测用；正常运行时没人调）"""
    end = time.time() + max(0.0, timeout)
    while time.time() < end:
        with _LOCK:
            busy = bool(_JOBS) or (_WORKER is not None and _WORKER.is_alive())
        if not busy:
            return True
        time.sleep(0.02)
    return False


def stats():
    with _LOCK:
        return {"ok": True, "enabled": enabled(), "pending": len(_JOBS),
                "seen": len(_SEEN), "queue_max": QUEUE_MAX, "n": DEFAULT_N,
                "last": dict(_LAST)}
