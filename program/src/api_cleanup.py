# -*- coding: utf-8 -*-
"""数据清理入口（#17）：看得见体积 → 清缓存 / 导出数据 / 删角色。

    GET  /api/cleanup             概览：每块缓存 / 日志 / 临时文件多大、角色各占多少
    POST /api/cleanup/cache       {"items": ["logs", "perf", ...]}   清选中的那几块
    POST /api/cleanup/export      {"turns": 0}                       导出 zip（默认脱敏、不带对话）
    POST /api/cleanup/character   {"id": "...", "confirm": "..."}    挪到 characters/_deleted/

三条规矩：

1. **只删程序能自己再生成的东西**。存档、角色内容（人设 / 世界书 / 记忆书）、素材一律不碰。
2. **删角色不是真删**：整个文件夹改名挪到 `characters/_deleted/<名字>_<时间>/`，随时能搬回来。
3. **二次确认后端也要验**：`confirm` 必须等于角色名，前端那一下只是顺手。

体积/删除都走这里，界面只负责显示和勾选（`ui/cleanup.js`）。
"""

import json
import os
import shutil
import time
import zipfile

import characters

# 概览里固定列出的清理项：(key, 标题, 说明)
#   路径在调用时才解析 —— 自测要能指到临时目录，模块导入时不能定死。
KINDS = [
    ("runtime", "运行时缓存", "对话记忆书缓存 / 关系阶段 / 价格表，删了会重新学"),
    ("text_cache", "字模缓存", "中文→PNG 的渲染缓存，删了会重画（慢一点）"),
    ("tts_cache", "语音缓存", "合成过的语音 wav，删了会重新合成"),
    ("logs", "历史日志", "app-*.log；今天正在写的那份不会删"),
    ("perf", "性能采样", "perf-*.csv（每 20 轮一行）"),
    ("crash", "崩溃现场", "crash_*.log（强杀 / 异常退出时留的）"),
    ("asset_report", "素材自检报告", "启动时自动重扫"),
    ("scratch", "临时目录", "work_scratch（自测 / 探针留下的）"),
]

# 运行时缓存里「删了会重新学」的三份（只删这三份，cache/ 下别的东西不动）
RUNTIME_FILES = ("memory_runtime.json", "persona_stage.json", "prices.json")

# 导出时带上的角色内容（素材不进 zip：太大，走「角色包导出」）
CHAR_FILES = ("name.txt", "performance.json", "persona_slices.json")
CHAR_DIRS = {
    "prompt": (".txt",),
    "worldbook": (".json",),
    "memory": (".txt",),
}


# ---------------------------------------------------------------- 路径 / 体积

def _program_dir():
    """program/ 目录（main.py 在 program/build_env/ 或 program/src/ 下都认）"""
    here = os.path.dirname(os.path.abspath(__file__))
    up = os.path.dirname(here)
    return up if os.path.basename(up) in ("build_env", "src") else here


def _data_dir():
    """数据根：prefs / config 在这儿（和 main.EXE_DIR 同一个来源）"""
    try:
        import datadir
        return datadir.root()
    except Exception:
        return _program_dir()


def _log_dir(d=None):
    if d:
        return d
    try:
        import logpack
        return logpack.log_dir()
    except Exception:
        return os.path.join(_data_dir(), "logs")


def _cache_dir(d=None):
    if d:
        return d
    try:
        import memory
        return memory.CACHE_DIR
    except Exception:
        return os.path.join(_data_dir(), "cache")


def _text_cache_dir(d=None):
    if d:
        return d
    try:
        import gdi_text
        return gdi_text.CACHE_DIR
    except Exception:
        return ""


def _tts_cache_dir(d=None):
    if d:
        return d
    try:
        import tts_client
        return tts_client._TTS_CACHE_DIR
    except Exception:
        return ""


def _scratch_dir(d=None):
    return d or os.path.join(_program_dir(), "work_scratch")


def walk(path):
    """(文件数, 字节数)；不存在就 (0, 0)，不抛"""
    files = total = 0
    if not path or not os.path.isdir(path):
        return 0, 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.path.getsize(os.path.join(root, n))
                files += 1
            except OSError:
                pass
    return files, total


def _size_of_file(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _app_logs(d):
    """今天在写的那份 + 历史日志；返回 (今天的, [历史...])"""
    today = time.strftime("%Y%m%d")
    cur, old = "", []
    if not os.path.isdir(d):
        return cur, old
    for n in sorted(os.listdir(d)):
        if not (n.startswith("app-") and n.endswith(".log")):
            continue
        if today in n:
            cur = os.path.join(d, n)
        else:
            old.append(os.path.join(d, n))
    return cur, old


def _glob(d, prefix, suffix):
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.startswith(prefix) and n.endswith(suffix)]


def _display(cid, root):
    """角色显示名：读 <角色>/name.txt，没有就用 id（不依赖 characters 的缓存）"""
    p = os.path.join(root, cid, "name.txt")
    try:
        with open(p, encoding="utf-8") as f:
            n = f.read(80).strip()
        return n or cid
    except Exception:
        return cid


def _char_ids(root):
    try:
        return [n for n in sorted(os.listdir(root))
                if not n.startswith("_") and os.path.isdir(os.path.join(root, n))]
    except Exception:
        return []


# ---------------------------------------------------------------- 概览

def overview(log_dir=None, cache_dir=None, text_cache=None, tts_cache=None,
            scratch_dir=None, root=None, chars=None):
    """每块多大 + 角色列表；只读，不删任何东西"""
    ld = _log_dir(log_dir)
    cd = _cache_dir(cache_dir)
    tc = _text_cache_dir(text_cache)
    vc = _tts_cache_dir(tts_cache)
    sd = _scratch_dir(scratch_dir)

    paths = {
        "runtime": cd,
        "text_cache": tc,
        "tts_cache": vc,
        "logs": ld,
        "perf": ld,
        "crash": ld,
        "asset_report": ld,
        "scratch": sd,
    }
    items = []
    for key, label, note in KINDS:
        p = paths.get(key) or ""
        if key in ("logs", "perf", "crash", "asset_report"):
            files = _glob(ld, "app-", ".log") if key == "logs" else \
                    _glob(ld, "perf-", ".csv") if key == "perf" else \
                    _glob(ld, "crash_", ".log") if key == "crash" else \
                    ([os.path.join(ld, "asset_report.json")]
                     if os.path.exists(os.path.join(ld, "asset_report.json")) else [])
            n, size = len(files), sum(_size_of_file(f) for f in files)
        elif key == "runtime":
            # 只算 clean() 真会删的那三份，别把 cache/ 下的别的东西算进来
            files = [os.path.join(cd, n) for n in RUNTIME_FILES
                     if os.path.exists(os.path.join(cd, n))]
            n, size = len(files), sum(_size_of_file(f) for f in files)
        elif key == "scratch":
            n, size = walk(sd)
        else:
            n, size = walk(p)
        items.append({"key": key, "label": label, "note": note,
                      "path": p, "files": n, "bytes": size})

    out_chars = []
    total = sum(it["bytes"] for it in items)
    r = root or characters.root()
    ids = chars if chars is not None else _char_ids(r)
    try:
        active = characters.active_id()
    except Exception:
        active = ""
    for cid in ids:
        cdir = os.path.join(r, cid)
        n, size = walk(cdir)
        _sn, ssize = walk(os.path.join(cdir, "sessions"))
        out_chars.append({"id": cid, "name": _display(cid, r),
                          "files": n, "bytes": size, "sessions_bytes": ssize,
                          "active": cid == active})
        total += size  # 角色占的是大头，总量要算进去
    return {"ok": True, "items": items, "characters": out_chars,
            "cache_bytes": sum(it["bytes"] for it in items),
            "characters_bytes": sum(c["bytes"] for c in out_chars),
            "total_bytes": total}


# ---------------------------------------------------------------- 清缓存

def _rm_tree(path):
    """删目录，返回 (文件数, 字节数)；不存在算成功"""
    n, size = walk(path)
    shutil.rmtree(path, ignore_errors=True)
    return n, size


def clean(keys, log_dir=None, cache_dir=None, text_cache=None, tts_cache=None,
          scratch_dir=None):
    """按 key 删；返回每项的结果（删了多少、跳过了什么）"""
    keys = [k for k in (keys or []) if k]
    ld = _log_dir(log_dir)
    cd = _cache_dir(cache_dir)
    out = []
    done = set()
    for key, label, _note in KINDS:
        if key not in keys:
            continue
        done.add(key)
        res = {"key": key, "label": label, "removed": 0, "bytes": 0,
               "skipped": [], "error": ""}
        try:
            if key == "runtime":
                for name in RUNTIME_FILES:
                    p = os.path.join(cd, name)
                    if os.path.exists(p):
                        size = _size_of_file(p)
                        os.remove(p)
                        res["removed"] += 1
                        res["bytes"] += size
            elif key == "text_cache":
                n, size = _rm_tree(_text_cache_dir(text_cache))
                res["removed"], res["bytes"] = n, size
            elif key == "tts_cache":
                n, size = _rm_tree(_tts_cache_dir(tts_cache))
                res["removed"], res["bytes"] = n, size
            elif key == "logs":
                cur, old = _app_logs(ld)
                for p in old:
                    size = _size_of_file(p)
                    try:
                        os.remove(p)
                        res["removed"] += 1
                        res["bytes"] += size
                    except OSError:
                        res["skipped"].append(os.path.basename(p))
                if cur:
                    res["skipped"].append(os.path.basename(cur) + "（正在写）")
            elif key == "perf":
                for p in _glob(ld, "perf-", ".csv"):
                    size = _size_of_file(p)
                    try:
                        os.remove(p)
                        res["removed"] += 1
                        res["bytes"] += size
                    except OSError:
                        res["skipped"].append(os.path.basename(p))
            elif key == "crash":
                for p in _glob(ld, "crash_", ".log"):
                    size = _size_of_file(p)
                    try:
                        os.remove(p)
                        res["removed"] += 1
                        res["bytes"] += size
                    except OSError:
                        res["skipped"].append(os.path.basename(p))
            elif key == "asset_report":
                p = os.path.join(ld, "asset_report.json")
                if os.path.exists(p):
                    size = _size_of_file(p)
                    os.remove(p)
                    res["removed"], res["bytes"] = 1, size
            elif key == "scratch":
                # work_scratch 整个扫空（目录本身留着，程序还要往里面写 tmp）
                sd = _scratch_dir(scratch_dir)
                if os.path.isdir(sd):
                    for name in sorted(os.listdir(sd)):
                        p = os.path.join(sd, name)
                        if os.path.isdir(p):
                            n, size = _rm_tree(p)
                        else:
                            size = _size_of_file(p)
                            try:
                                os.remove(p)
                                n = 1
                            except OSError:
                                n = 0
                                res["skipped"].append(name)
                        res["removed"] += n
                        res["bytes"] += size
        except Exception as e:      # 单项失败不影响别的项
            res["error"] = str(e)
        out.append(res)
    unknown = [k for k in keys if k not in done]
    return {"ok": True, "cleaned": out,
            "freed_bytes": sum(r["bytes"] for r in out),
            "freed_files": sum(r["removed"] for r in out),
            "unknown": unknown}


# ---------------------------------------------------------------- 导出

def _zip_add(zf, arcname, text):
    try:
        zf.writestr(arcname, text)
        return True
    except Exception:
        return False


def _safe_read(path, limit=4 * 1024 * 1024):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def _scrub(text):
    """统一脱敏：密钥 / 用户名 / 绝对路径。没有 logpack 就只抹 sk-"""
    try:
        import logpack
        return logpack.scrub(text)
    except Exception:
        import re
        return re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-****", text or "")


def export(turns=0, out_dir="", log_dir=None, root=None, chars=None):
    """导出 zip：配置 + 角色内容；`turns>0` 才带存档（对话）"""
    ld = _log_dir(log_dir)
    out_dir = out_dir or ld
    os.makedirs(out_dir, exist_ok=True)
    croot = root or characters.root()
    ids = chars if chars is not None else _char_ids(croot)
    path = os.path.join(out_dir, "wa2_export_%s.zip" % time.strftime("%Y-%m-%d_%H-%M-%S"))
    include_dialogue = int(turns or 0) > 0
    files = 0
    manifest = {
        "app": "WA2-Kazusa-Bot",
        "version": "",
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "include_dialogue": include_dialogue,
        "note": "素材（立绘 / 语音 / CG）不在这里，用「特别模式 → 角色包导出」；"
                "所有文本都已脱敏（密钥 / 用户名 / 绝对路径）",
        "characters": [],
    }
    try:
        import version
        manifest["version"] = getattr(version, "VERSION", "")
    except Exception:
        pass

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 配置：prefs 原样脱敏；config.json 里的 api_key 打码
        try:
            import prefs as prefs_mod
            prefs_path = prefs_mod.path() if hasattr(prefs_mod, "path") else ""
        except Exception:
            prefs_path = ""
        if prefs_path and os.path.exists(prefs_path):
            if _zip_add(zf, "config/prefs.json", _scrub(_safe_read(prefs_path))):
                files += 1
        cfg_path = os.path.join(_data_dir(), "config.json")
        if os.path.exists(cfg_path):
            txt = _safe_read(cfg_path)
            try:
                cfg = json.loads(txt)
                if isinstance(cfg, dict) and cfg.get("api_key"):
                    cfg["api_key"] = _masked(cfg["api_key"])
                    txt = json.dumps(cfg, ensure_ascii=False, indent=1)
            except Exception:
                pass
            if _zip_add(zf, "config/config.json", _scrub(txt)):
                files += 1

        # 角色内容（素材不进 zip）
        for cid in ids:
            cdir = os.path.join(croot, cid)
            got = []
            for name in CHAR_FILES:
                p = os.path.join(cdir, name)
                if os.path.exists(p):
                    if _zip_add(zf, "characters/%s/%s" % (cid, name), _scrub(_safe_read(p))):
                        files += 1
                        got.append(name)
            for sub, exts in CHAR_DIRS.items():
                d = os.path.join(cdir, sub)
                if not os.path.isdir(d):
                    continue
                for n in sorted(os.listdir(d)):
                    if n.endswith(".bak") or not n.endswith(exts):
                        continue
                    p = os.path.join(d, n)
                    if not os.path.isfile(p):
                        continue
                    if _zip_add(zf, "characters/%s/%s/%s" % (cid, sub, n),
                                _scrub(_safe_read(p))):
                        files += 1
                        got.append("%s/%s" % (sub, n))
            if include_dialogue:
                sd = os.path.join(cdir, "sessions")
                for n in sorted(os.listdir(sd)) if os.path.isdir(sd) else []:
                    if not n.endswith(".json"):
                        continue
                    p = os.path.join(sd, n)
                    if _zip_add(zf, "characters/%s/sessions/%s" % (cid, n),
                                _scrub(_safe_read(p))):
                        files += 1
                        got.append("sessions/" + n)
            else:
                got.append("（对话存档未包含）")
            manifest["characters"].append({"id": cid, "name": _display(cid, croot),
                                           "files": got})
        _zip_add(zf, "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
        files += 1
        try:
            _zip_add(zf, "README.txt", _readme(include_dialogue))
            files += 1
        except Exception:
            pass
    return {"ok": True, "path": path, "bytes": _size_of_file(path),
            "files": files, "include_dialogue": include_dialogue}


def _masked(key):
    try:
        import secret
        return secret.mask(key)
    except Exception:
        k = str(key or "")
        return "sk-****" + k[-4:] if len(k) > 8 else "sk-****"


def _readme(include_dialogue):
    lines = [
        "WA2 数据导出（%s）" % time.strftime("%Y-%m-%d %H:%M:%S"),
        "",
        "config/     prefs.json（界面设置）、config.json（模型 / 连接，api_key 已打码）",
        "characters/ 每个角色的人设 / 世界书 / 记忆书 / 演出配置 / 名字",
        "",
        "对话存档：%s" % ("已包含（characters/<角色>/sessions/）" if include_dialogue else "未包含"),
        "",
        "素材（立绘 / 语音 / CG）不在这里；需要的话用「特别模式 → 角色包导出」。",
        "所有文本都过了脱敏：API key、用户名、绝对路径已经抹掉。",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 删角色

BAD_ID_CHARS = ("/", "\\", "..", ":", "*", "?", '"', "<", ">", "|")


def remove_character(cid, confirm, root=None, cache_dir=None, active=None):
    """把角色文件夹挪到 characters/_deleted/；confirm 必须是角色名"""
    cid = (cid or "").strip()
    if not cid or any(ch in cid for ch in BAD_ID_CHARS):
        return {"ok": False, "error": "角色 id 不对"}
    r = root or characters.root()
    cdir = os.path.join(r, cid)
    if not os.path.isdir(cdir):
        return {"ok": False, "error": "没有这个角色：%s" % cid}

    name = _display(cid, r)
    if (confirm or "").strip() not in (name, cid):
        return {"ok": False, "error": "确认名不对（要输入角色名「%s」）" % name}
    if active is None:
        try:
            active = characters.active_id()
        except Exception:
            active = ""
    if cid == active:
        return {"ok": False, "error": "这是当前正在用的角色，先在「特别模式」切到别的角色再删"}

    n, size = walk(cdir)
    trash = os.path.join(r, "_deleted")
    os.makedirs(trash, exist_ok=True)
    dst = os.path.join(trash, "%s_%s" % (cid, time.strftime("%Y-%m-%d_%H-%M-%S")))
    try:
        os.rename(cdir, dst)          # 同盘改名，瞬移；挪回来就恢复了
    except OSError as e:
        return {"ok": False, "error": "挪走失败：%s" % e}

    # 这个角色自己的缓存目录（roadmap：删角色同步清 cache/<角色>/）
    cleared = 0
    cbase = _cache_dir(cache_dir)
    own = os.path.join(cbase, cid)
    if os.path.isdir(own):
        shutil.rmtree(own, ignore_errors=True)
        cleared = 1
    return {"ok": True, "moved_to": dst, "files": n, "bytes": size,
            "cache_cleared": cleared,
            "note": "想撤回：把 %s 挪回 %s 即可" % (dst, r)}


# ---------------------------------------------------------------- HTTP

class CleanupApi:
    """见 main.Handler；从 main 注入运行时全局。"""

    def _cleanup_get(self):
        """GET /api/cleanup"""
        try:
            return self._json(overview())
        except Exception as e:
            return self._json({"ok": False, "error": "读体积失败：%s" % e}, 500)

    def _cleanup_cache(self):
        """POST /api/cleanup/cache  {"items": ["logs", ...]}"""
        try:
            body = self._body()
        except Exception as e:
            return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
        items = (body or {}).get("items")
        if not isinstance(items, list) or not items:
            return self._json({"ok": False, "error": "没选要清的东西"}, 400)
        try:
            return self._json(clean(items))
        except Exception as e:
            return self._json({"ok": False, "error": "清理失败：%s" % e}, 500)

    def _cleanup_export(self):
        """POST /api/cleanup/export  {"turns": 0}"""
        try:
            body = self._body()
        except Exception:
            body = {}
        try:
            turns = int((body or {}).get("turns") or 0)
        except Exception:
            turns = 0
        try:
            return self._json(export(turns=turns))
        except Exception as e:
            return self._json({"ok": False, "error": "导出失败：%s" % e}, 500)

    def _cleanup_character(self):
        """POST /api/cleanup/character  {"id": "...", "confirm": "..."}"""
        try:
            body = self._body()
        except Exception as e:
            return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "bad body"}, 400)
        res = remove_character(body.get("id") or "", body.get("confirm") or "")
        return self._json(res, 200 if res.get("ok") else 400)
