# -*- coding: utf-8 -*-
"""日志保留 + 诊断包导出（#15）。

两件事：
  1. **按天滚动**：`logs/app-YYYYMMDD.log` 只留最近 N 天（`WA2_LOG_KEEP_DAYS`，默认 14）；
     崩溃现场（`crash_*.log` / `crash_*_auto.json`）只留最新 10 份。
     启动时跑一次，不占运行时间。
  2. **诊断包**：把「日志 + 脱敏配置 + 素材报告 + 版本信息 + 调试状态」打成一个 zip，
     交给开发者就能查问题，而**默认不含对话原文**。

脱敏（导出前统一过一遍 `scrub()`，不是靠日志本身干净）：
  · 密钥：交给 `secret.redact()`（`sk-...` / `Bearer ...` / `api_key: ...`）
  · 带文字的查询串：`/api/text?text=...`、`/api/tts?text=...` → `<已隐藏>`
    （日志里记的是请求路径，这两个接口的路径本身就带对话 / 朗读文本）
  · 绝对路径：`C:\\Users\\<用户名>\\...` → `%USERPROFILE%`；其它盘符路径只留文件名
  · `prefs.json` / `config.json` 只带「非敏感字段 + 掩码」

对外：
    KEEP_DAYS / CRASH_KEEP
    scrub(text)                  → 脱敏后的字符串
    prune(keep_days, dry)        → {"removed": [...], "freed": bytes, ...}
    bundle(include_turns=0)      → {"ok", "path", "bytes", "items", ...}
"""
import datetime
import json
import os
import platform
import re
import sys
import time
import zipfile

import applog
import datadir
import diag
import secret

KEEP_DAYS = max(1, int(os.environ.get("WA2_LOG_KEEP_DAYS") or 14))
CRASH_KEEP = 10
MAX_LOG_CHARS = 400000          # 单个日志在包里最多留 40 万字（尾部）

_RE_APP = re.compile(r"^app-(\d{8})\.log$")
_RE_PERF = re.compile(r"^perf-(\d{4}-\d{2}-\d{2})\.csv$")
_RE_CRASH = re.compile(r"^crash-?(\d{4}-\d{2}-\d{2})[_-](\d{2}-\d{2}-\d{2})")
_RE_QUERY = re.compile(r"(/api/(?:text|tts)\?)[^\s\"'<>]*")
_RE_WINUSER = re.compile(r"[A-Za-z]:\\Users\\[^\\/:*?\"'<>|\r\n]+", re.I)
_RE_WINPATH = re.compile(r"[A-Za-z]:\\[^\s\"'<>|]*")
_RE_UNCPATH = re.compile(r"\\\\[^\s\"'<>|]+")
_RE_POSIX = re.compile(r"/(?:home|Users)/[^\s\"'<>|]+")


# ------------------------------------------------------------------ 路径

def _exe_dir():
    """配置目录 = 数据根（和 main.py 的 EXE_DIR 同一个来源，见 datadir.py）。

    这里不 import main：main 一导入就会起预热线程（字模 / 语音），导出日志这种事不该有副作用。
    """
    return datadir.root()


def log_dir():
    try:
        return applog._log_dir()
    except Exception:
        return os.environ.get("TEMP", ".")


# ------------------------------------------------------------------ 脱敏

def _scrub_paths(s):
    home = os.path.expanduser("~")
    if home and home in s:
        s = s.replace(home, "%USERPROFILE%")
    s = s.replace(home.replace("\\", "/"), "%USERPROFILE%")
    s = _RE_WINUSER.sub("%USERPROFILE%", s)
    s = _RE_POSIX.sub("%USERPROFILE%", s)

    def _short(m):
        raw = m.group(0)
        name = re.split(r"[\\/]", raw.rstrip("\\/"))[-1]
        return "…\\" + name if name else "…"

    s = _RE_WINPATH.sub(_short, s)
    s = _RE_UNCPATH.sub(_short, s)
    return s


def scrub(text):
    """导出前的统一脱敏：密钥 → 查询串 → 绝对路径 / 用户名。"""
    if text is None:
        return ""
    s = str(text)
    try:
        s = secret.redact(s)
    except Exception:
        pass
    s = _RE_QUERY.sub(lambda m: m.group(1) + "<已隐藏>", s)
    s = _scrub_paths(s)
    return s


def _tail_text(s, max_chars=MAX_LOG_CHARS):
    """太长的日志只留尾巴（报错通常在最后）；先截再脱敏也没问题，这里顺序是先脱敏后截。"""
    if len(s) <= max_chars:
        return s
    return "…（前面省略 %d 字）…\n" % (len(s) - max_chars) + s[-max_chars:]


def _scrub_obj(obj):
    """递归脱敏 dict / list 里的字符串值。

    JSON 要先结构脱敏再 dumps：直接对 dumps 出来的文本做正则，会把 `\\\\` 这类
    转义序列改坏（Windows 路径在 JSON 里就是 `D:\\\\games`）。
    """
    if isinstance(obj, dict):
        return {k: _scrub_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    if isinstance(obj, str):
        return scrub(obj)
    return obj


# ------------------------------------------------------------------ 按天清理

def _app_logs(d=None):
    """[(日期, 路径)]，按日期升序"""
    d = d or log_dir()
    out = []
    try:
        for fn in os.listdir(d):
            m = _RE_APP.match(fn)
            if m:
                out.append((m.group(1), os.path.join(d, fn)))
    except OSError:
        pass
    return sorted(out)


def _perf_files(d=None):
    """[(日期, 路径)]，性能采样 csv（#14），按日期升序"""
    d = d or log_dir()
    out = []
    try:
        for fn in os.listdir(d):
            m = _RE_PERF.match(fn)
            if m:
                out.append((m.group(1), os.path.join(d, fn)))
    except OSError:
        pass
    return sorted(out)


def _crash_logs(d=None):
    """[(时间串, 路径)]，按时间升序"""
    d = d or log_dir()
    out = []
    try:
        for fn in os.listdir(d):
            if fn.startswith("crash_") and (fn.endswith(".log") or fn.endswith("_auto.json")):
                m = _RE_CRASH.match(fn)
                key = (m.group(1) + "_" + m.group(2)) if m else fn
                out.append((key, os.path.join(d, fn)))
    except OSError:
        pass
    return sorted(out)


def prune(keep_days=KEEP_DAYS, dry=False, d=None):
    """删掉过期日志。返回 {"removed": [...], "freed": 字节, "kept": n, "dry": bool}"""
    d = d or log_dir()
    keep_days = max(1, int(keep_days or KEEP_DAYS))
    cutoff = datetime.date.today() - datetime.timedelta(days=keep_days - 1)
    active = ""
    try:
        active = os.path.abspath(applog.path() or "")
    except Exception:
        pass

    removed, freed, kept = [], 0, 0
    for day, path in _app_logs(d):
        try:
            when = datetime.datetime.strptime(day, "%Y%m%d").date()
        except ValueError:
            continue
        if os.path.abspath(path) == active:      # 正在写的那个永远不删
            kept += 1
            continue
        if when < cutoff:
            try:
                size = os.path.getsize(path)
                if not dry:
                    os.remove(path)
                removed.append(os.path.basename(path))
                freed += size
            except OSError:
                pass
        else:
            kept += 1

    crashes = _crash_logs(d)
    if len(crashes) > CRASH_KEEP:
        for _key, path in crashes[:-CRASH_KEEP]:
            try:
                size = os.path.getsize(path)
                if not dry:
                    os.remove(path)
                removed.append(os.path.basename(path))
                freed += size
            except OSError:
                pass

    # 性能采样 csv 也按同一个保留天数滚（#14）
    for day, path in _perf_files(d):
        try:
            when = datetime.datetime.strptime(day, "%Y-%m-%d").date()
        except ValueError:
            continue
        if when < cutoff:
            try:
                size = os.path.getsize(path)
                if not dry:
                    os.remove(path)
                removed.append(os.path.basename(path))
                freed += size
            except OSError:
                pass

    if removed:
        try:
            applog.log("日志清理：删掉 %d 个旧文件（%d 天保留），释放 %.1f KB"
                       % (len(removed), keep_days, freed / 1024.0), "WARN")
        except Exception:
            pass
    return {"removed": removed, "freed": freed, "kept": kept, "dry": dry,
            "keep_days": keep_days}


# ------------------------------------------------------------------ 诊断包

def _read_text(path, limit=2 * 1024 * 1024):
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
        return raw.decode("utf-8", "replace")
    except Exception:
        return ""


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _version_info():
    info = {"app": "WHITE ALBUM2 Extension", "version": "", "at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "pid": os.getpid(), "python": sys.version.split()[0],
            "os": platform.platform(), "log_keep_days": KEEP_DAYS}
    try:
        import version
        info["version"] = version.VERSION
    except Exception:
        pass
    try:
        info["uptime_s"] = diag.uptime_s()
    except Exception:
        pass
    return info


def _config_summary(exe_dir=""):
    """配置摘要：只带「不该怕被人看到」的字段，key 一律掩码。"""
    exe_dir = exe_dir or _exe_dir()
    out = {"prefs_file": "", "config_file": "", "key_source": ""}
    try:
        out["key_source"] = secret.source()
    except Exception:
        pass
    prefs = _read_json(os.path.join(exe_dir, "prefs.json"))
    if isinstance(prefs, dict):
        out["prefs_file"] = os.path.basename("prefs.json")
        safe = {k: v for k, v in prefs.items()
                if k not in ("apiKey", "api_key", "key", "token")}
        out["prefs"] = safe
    cfg = _read_json(os.path.join(exe_dir, "config.json"))
    if isinstance(cfg, dict):
        out["config"] = {
            "base_url": cfg.get("base_url") or "",
            "model": cfg.get("model") or "",
            "api_key": secret.mask(cfg.get("api_key") or "") or ("(环境变量)" if out["key_source"] == "env" else ""),
        }
        voice = cfg.get("voice")
        if isinstance(voice, dict):
            out["config"]["voice"] = {k: v for k, v in voice.items()
                                      if k in ("api_url", "endpoint", "model", "enabled")}
    return out


def _conversation(turns):
    """最近 N 轮对话（来自当前角色的自动存档）；每段都过 scrub。"""
    if turns <= 0:
        return None
    try:
        import sessions
        path = os.path.join(sessions.SESS_DIR, "auto.json")
    except Exception:
        return None
    d = _read_json(path)
    if not isinstance(d, dict):
        return None
    msgs = d.get("messages") or []
    keep = msgs[-turns * 2:]
    return {
        "note": "这是你选择包含的最近 %d 轮对话；发送给任何人之前请先看一遍。" % turns,
        "slot": d.get("slot") or "auto",
        "time": d.get("time") or "",
        "messages": [{"role": m.get("role"), "content": scrub(m.get("content") or ""),
                      **({"zh": scrub(m.get("zh"))} if m.get("zh") else {})}
                     for m in keep],
    }


def bundle(include_turns=0, out_dir="", d=None, exe_dir=""):
    """打一个诊断包（zip）。默认**不含对话原文**。"""
    d = d or log_dir()
    include_turns = max(0, min(200, int(include_turns or 0)))
    pruned = prune(d=d)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = out_dir or d
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        out_dir = d
    path = os.path.join(out_dir, "wa2_diag_%s.zip" % ts)

    items = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        def put(name, text):
            """text 必须是**已经脱敏好**的：JSON 走 _scrub_obj，纯文本走 scrub。"""
            z.writestr(name, text)
            items.append(name)

        def put_json(name, obj):
            put(name, json.dumps(_scrub_obj(obj), ensure_ascii=False, indent=1))

        # 版本 / 环境
        put_json("version.json", _version_info())
        env_lines = ["WA2_* 环境变量（值已脱敏，只说明有没有设）："]
        for k in sorted(os.environ):
            if k.startswith("WA2_"):
                v = os.environ[k]
                env_lines.append("  %s = %s" % (k, secret.mask(v) if "KEY" in k.upper() else scrub(v)))
        put("env.txt", "\n".join(env_lines) + "\n")

        # 日志（最近 N 天）
        for _day, lp in _app_logs(d):
            put("logs/" + os.path.basename(lp), _tail_text(scrub(_read_text(lp))))
        crash = _crash_logs(d)[-3:]
        for _k, cp in crash:
            put("logs/" + os.path.basename(cp), _tail_text(scrub(_read_text(cp))))
        # 性能采样 csv（#14）：纯数字，原样带上
        for _pday, pp in _perf_files(d)[-KEEP_DAYS:]:
            put("logs/" + os.path.basename(pp), _read_text(pp, 512 * 1024))

        # 素材报告 / 运行状态 / 调试状态
        rep = _read_json(os.path.join(d, "asset_report.json"))
        if rep is not None:
            put_json("asset_report.json", rep)
        st = _read_json(os.path.join(d, "run_state.json"))
        if st is not None:
            put_json("run_state.json", st)
        try:
            put_json("diag_state.json", diag.snapshot())
        except Exception:
            pass

        # 配置摘要（脱敏）
        put_json("config_summary.json", _config_summary(exe_dir))

        # 可选：最近 N 轮对话
        conv = _conversation(include_turns)
        if conv is not None:
            put_json("conversation.json", conv)

        readme = [
            "WHITE ALBUM2 Extension 诊断包",
            "生成时间: %s" % time.strftime("%Y-%m-%d %H:%M:%S"),
            "",
            "包含：",
            "  version.json          版本 / Python / 系统 / 运行时长",
            "  env.txt               设置了哪些 WA2_* 环境变量（值已脱敏）",
            "  logs/                 最近 %d 天的运行日志 + 最近 3 份崩溃现场" % KEEP_DAYS,
            "  asset_report.json     素材自检（缺图 / 0 字节）",
            "  run_state.json        上次是不是正常退出",
            "  diag_state.json       提示词分段 / 缓存命中 / 关系阶段等只读指标",
            "  config_summary.json   配置摘要（key 只有掩码）",
            "",
            "脱敏：",
            "  · API 密钥（key / Bearer token）已抹掉，只剩掩码",
            "  · /api/text 与 /api/tts 的查询串（对话 / 朗读文本）已替换成 <已隐藏>",
            "  · 用户目录替换成 %USERPROFILE%，其它绝对路径只留文件名",
            "",
        ]
        if conv is not None:
            readme.append("注意：这个包**包含最近 %d 轮对话原文**（conversation.json）。" % include_turns)
            readme.append("      发给别人之前先自己看一遍，确认没有不想外传的内容。")
        else:
            readme.append("默认不含对话原文；确实需要时用 /api/log/bundle?turns=20 生成。")
        put("README.txt", "\n".join(readme) + "\n")

    size = os.path.getsize(path)
    try:
        applog.log("诊断包：%s（%d 个条目，%.1f KB，对话 %d 轮）"
                   % (os.path.basename(path), len(items), size / 1024.0, include_turns), "WARN")
    except Exception:
        pass
    return {"ok": True, "path": path, "bytes": size, "items": items,
            "include_turns": include_turns, "pruned": len(pruned["removed"]),
            "note": ("含最近 %d 轮对话，发出前先看一遍" % include_turns) if include_turns else "不含对话原文"}
