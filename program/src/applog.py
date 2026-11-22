# -*- coding: utf-8 -*-
"""运行日志

用途：程序"未响应"时能查到卡在哪。
记录内容：
  · 启动/关闭、各子系统初始化
  · 每个 HTTP 请求（路径、状态、耗时），慢请求标警告
  · 模型/语音调用耗时
  · 心跳：每 30 秒记线程数与内存
  · 线程栈快照（手动触发或检测到长时间无响应时）

日志位置: <项目>/logs/app-YYYYMMDD.log

写之前统一过一遍 `secret.redact()`：日志里不该出现 `sk-...` / `Bearer ...` 这类东西。
"""
import datetime
import os
import sys
import threading
import time
import traceback

import datadir

try:
    import secret as _secret
except Exception:                      # 极端情况下（单文件跑）也不能让日志挂掉
    _secret = None

_LOCK = threading.Lock()
_FILE = None
_T0 = time.time()
_HEARTBEAT_STOP = False
_SLOW_REQUEST_MS = 3000


def _log_dir():
    # 日志跟其它运行数据一起放数据根（开发是 build_env，打包是 exe 同目录）
    for cand in (datadir.env_or("WA2_LOG_DIR", "logs"),
                 os.path.join(os.environ.get("TEMP", "."), "wa2_logs")):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except Exception:
            continue
    return "."


def _open():
    global _FILE
    if _FILE is not None:
        return _FILE
    d = _log_dir()
    name = "app-%s.log" % datetime.datetime.now().strftime("%Y%m%d")
    path = os.path.join(d, name)
    try:
        _FILE = open(path, "a", encoding="utf-8", buffering=1)
    except Exception:
        _FILE = sys.stderr
        path = "<stderr>"
    _FILE.write("\n===== 启动 %s  pid=%d =====\n"
                % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
    return _FILE


def path():
    try:
        return _open().name
    except Exception:
        return ""


def log(msg, level="INFO"):
    f = _open()
    if _secret is not None:
        try:
            msg = _secret.redact(msg)
        except Exception:
            pass
    t = time.time() - _T0
    line = "[%s] [+%7.2fs] [%-5s] [%s] %s\n" % (
        datetime.datetime.now().strftime("%H:%M:%S"), t, level,
        threading.current_thread().name, msg)
    with _LOCK:
        try:
            f.write(line)
            f.flush()
        except Exception:
            pass


def error(msg, exc=None):
    if exc is not None:
        msg = "%s\n%s" % (msg, "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    log(msg, "ERROR")


def dump_stacks(reason=""):
    """把所有线程的调用栈写进日志——卡死时最有用"""
    log("===== 线程栈快照 %s =====" % reason, "DUMP")
    frames = sys._current_frames()
    for th in threading.enumerate():
        fr = frames.get(th.ident)
        if fr is None:
            continue
        stack = traceback.format_stack(fr)
        log("线程 %s (daemon=%s)" % (th.name, th.daemon), "DUMP")
        for s in stack[-12:]:
            for line in s.rstrip().splitlines():
                log("    " + line, "DUMP")
    log("===== 快照结束 =====", "DUMP")


def start_heartbeat(interval=30):
    """后台心跳：记录线程数/内存；若主线程长时间不响应会写栈"""
    def _loop():
        last = time.time()
        while not _HEARTBEAT_STOP:
            time.sleep(interval)
            try:
                mem = 0
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

                    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
                    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    _k32.GetCurrentProcess.restype = wintypes.HANDLE
                    _psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
                    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
                    pmc = PMC()
                    pmc.cb = ctypes.sizeof(PMC)
                    if _psapi.GetProcessMemoryInfo(_k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
                        mem = pmc.WorkingSetSize // (1024 * 1024)
                except Exception:
                    pass
                log("心跳: 线程 %d 个, 内存 %d MB" % (threading.active_count(), mem), "BEAT")
            except Exception:
                pass

    t = threading.Thread(target=_loop, name="heartbeat", daemon=True)
    t.start()
    return t


def stop():
    global _HEARTBEAT_STOP
    _HEARTBEAT_STOP = True
