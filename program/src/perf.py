# -*- coding: utf-8 -*-
"""性能采样（#14）：每 N 轮往 `logs/perf_YYYY-MM-DD.csv` 记一行。

记什么（够看「跑久了会不会涨」就行）：进程内存 / 线程数 / 句柄数 / GDI·USER 对象数 /
字模缓存条数 / TTS 缓存文件数与体积 / 记忆条目数 / 上一轮对话耗时与 token。

规矩：
  · **只读**：不改任何业务状态；采样在后台线程里跑，不占主循环
  · `WA2_PERF=0` 整个关掉；`WA2_PERF_EVERY` 改间隔（默认 20 轮）
  · 文件按天，`logpack.prune()` 会跟日志一起清
  · 采样失败只记一行 WARN，绝不影响对话

对外：
    ENABLED / EVERY
    snapshot()          → dict（当场量一次，不写文件）
    sample(path=None)   → dict|None（写一行）
    maybe()             → 每 N 轮触发一次（后台线程），返回是否触发
    last()              → 最近一次采样结果（调试面板 / 自测用）
    reset()             → 自测用：清计数与 last
"""
import datetime
import os
import threading
import time

import applog
import diag

ENABLED = (os.environ.get("WA2_PERF") or "1") != "0"
EVERY = max(1, int(os.environ.get("WA2_PERF_EVERY") or 20))

COLUMNS = ["ts", "uptime_s", "round", "rss_mb", "threads", "handles", "gdi", "user",
           "text_cache", "tts_files", "tts_mb", "mem_entries", "chat_ms", "chat_tokens"]

_LOCK = threading.Lock()
_LAST = {}
_N = 0
_THREAD = None


# ------------------------------------------------------------------ 采集

def _mem_mb():
    """(工作集 MB, 峰值 MB)；拿不到返回 (0, 0)"""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return (pmc.WorkingSetSize // (1024 * 1024),
                    pmc.PeakWorkingSetSize // (1024 * 1024))
    except Exception:
        pass
    return (0, 0)


def _handles():
    """进程句柄数（句柄泄漏会一直涨）

    注意：HANDLE 是 64 位的，**必须设 argtypes**——不设的话 ctypes 按 C int 传，
    `18446744073709551615` 会被截成 -1，调用失败返回 0（以前这里就一直记 0）。
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessHandleCount.restype = wintypes.BOOL
        n = wintypes.DWORD(0)
        if k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n)):
            return int(n.value)
    except Exception:
        pass
    return 0


def _gui_objects():
    """(GDI 对象, USER 对象)：字模渲染主要吃 GDI，涨个不停就是漏了"""
    try:
        import ctypes
        from ctypes import wintypes
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        h = k32.GetCurrentProcess()
        u32.GetGuiResources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        u32.GetGuiResources.restype = wintypes.DWORD
        return (int(u32.GetGuiResources(h, 0) or 0), int(u32.GetGuiResources(h, 1) or 0))
    except Exception:
        return (0, 0)


def _text_cache_count():
    """字模磁盘缓存条数（位置由 datadir.text_cache() 决定，别再自己拼）"""
    try:
        import datadir
        d = datadir.text_cache()
        return len([f for f in os.listdir(d) if f.endswith(".png")])
    except Exception:
        return 0


def _tts_cache():
    """(文件数, MB)"""
    try:
        import tts_client
        d = getattr(tts_client, "_TTS_CACHE_DIR", "")
        if not d or not os.path.isdir(d):
            return (0, 0.0)
        n, total = 0, 0
        for f in os.listdir(d):
            if f.endswith(".wav"):
                n += 1
                try:
                    total += os.path.getsize(os.path.join(d, f))
                except OSError:
                    pass
        return (n, round(total / 1024.0 / 1024.0, 2))
    except Exception:
        return (0, 0.0)


def _mem_entries():
    try:
        import memory
        return len((memory.runtime() or {}).get("entries") or [])
    except Exception:
        return 0


def snapshot():
    """当场量一次（不写文件）"""
    rss, peak = _mem_mb()
    gdi, user = _gui_objects()
    tts_n, tts_mb = _tts_cache()
    rounds = diag.rounds()
    lastr = rounds[-1] if rounds else {}
    usage = (lastr.get("usage") or {})
    return {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_s": diag.uptime_s(),
        "round": int((diag.counters() or {}).get("chat.round", 0)),
        "rss_mb": rss,
        "peak_mb": peak,
        "threads": threading.active_count(),
        "handles": _handles(),
        "gdi": gdi,
        "user": user,
        "text_cache": _text_cache_count(),
        "tts_files": tts_n,
        "tts_mb": tts_mb,
        "mem_entries": _mem_entries(),
        "chat_ms": int(lastr.get("ms") or 0),
        "chat_tokens": int(usage.get("tokens") or 0),
    }


# ------------------------------------------------------------------ 落盘

def _path(d=None):
    d = d or applog._log_dir()
    return os.path.join(d, "perf-%s.csv" % datetime.date.today().strftime("%Y-%m-%d"))


def sample(path=None):
    """量一次并追加一行；返回这次的数据（写失败返回数据、不抛）"""
    snap = snapshot()
    path = path or _path()
    line = ",".join(str(snap.get(c, "")) for c in COLUMNS)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        with _LOCK:
            with open(path, "a", encoding="utf-8") as f:
                if new:
                    f.write(",".join(COLUMNS) + "\n")
                f.write(line + "\n")
    except Exception as e:
        try:
            applog.log("性能采样写文件失败：%s" % e, "WARN")
        except Exception:
            pass
    snap["csv"] = path
    with _LOCK:
        _LAST.clear()
        _LAST.update(snap)
    return snap


def maybe(force=False):
    """每 N 轮触发一次；采样在后台线程里，不占主循环。返回是否触发了采样。"""
    global _N, _THREAD
    if not ENABLED and not force:
        return False
    _N += 1
    if not force and (_N % EVERY) != 0:
        return False
    if _THREAD is not None and _THREAD.is_alive():
        return False                  # 上一次还没写完，不排队
    _THREAD = threading.Thread(target=_safe_sample, name="perf-sample", daemon=True)
    _THREAD.start()
    return True


def _safe_sample():
    try:
        sample()
    except Exception as e:
        try:
            applog.log("性能采样失败（不影响运行）：%s" % e, "WARN")
        except Exception:
            pass


def last():
    with _LOCK:
        return dict(_LAST)


def reset():
    """自测用"""
    global _N, _THREAD
    with _LOCK:
        _LAST.clear()
    _N = 0
    _THREAD = None
