# -*- coding: utf-8 -*-
"""一键更新：把本机代码更新到 GitHub 上 master 的最新版。

为什么不用补丁串：补丁是「对某个基线」的 diff，用户连着漏两版就没法按顺序打。
这里直接取最新整份代码：

    git 模式：仓库本来就是 clone 下来的 → fetch + reset --hard origin/master
    zip 模式：不是 git 仓库 → 下载 codeload 的 master 快照，只覆盖**代码/文档**白名单

两种情况都**不碰用户数据**：`program/build_env/`（角色、存档、缓存、venv）、
`program/res/`（素材）、`program/logs/` 都不在白名单里。
覆盖前先把要动的目录备份到 `program/work_scratch/update_backup_<时间>/`，
失败就还原。

对外：
    detect()     → 用哪种模式、能不能自动更新
    apply()      → 真正执行（后台线程里跑，进度问 progress()）
    progress()   → {"running", "stage", "percent", "message", "ok", "error", ...}
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

import version as _ver

def _frozen():
    """打包版（PyInstaller 的 exe）：代码冻在 exe 里，磁盘上没有可覆盖的源码树"""
    return bool(getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None))


def _dirs():
    """(UP, ROOT)。源码安装：UP=program/、ROOT=仓库根；打包版：两个都=exe 目录。

    打包版**不能**用 `__file__` 算根：冻结后 `__file__` 落在 `_internal/` 里，
    再往上两层会指到 exe 的**上一级目录**。2026-11-22 实测：11-06 的便携版因此把
    「更新」写进了 `release/` 里另一套源码副本，exe 本身一个字没变 —— 用户看到的现象
    就是「点了更新，每次重新打开又要再点一遍」。
    """
    if _frozen():
        app = os.path.dirname(os.path.abspath(sys.executable))
        return app, app
    up = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # program/
    return up, os.path.dirname(up)                                     # 仓库根（可能没有 .git）


UP, ROOT = _dirs()
WORK = os.path.join(UP, "work_scratch")

# 只覆盖这些（代码 + 文档）；用户数据一个都不在里面
WL_DIRS = ("program/src", "program/tools", "program/tests", "other/docs")
WL_FILES = ("README.md", "AGENTS.md", "LICENSE", "NOTICE.md", "version.json",
            "config.example.json", ".gitignore", "other/.gitignore",
            "other/项目说明（中文）.txt", "program/启动程序.bat")
# zip 里没了的旧文件也要删（否则改名/删模块会留下孤儿），只删这些后缀
CLEAN_EXT = (".py", ".js", ".css", ".html", ".md", ".json", ".bat", ".txt", ".patch")

ZIP_URL = "https://codeload.github.com/%s/zip/refs/heads/master" % _ver.REPO

_PROGRESS = {"running": False, "stage": "", "percent": 0, "message": "",
             "ok": None, "error": "", "version": "", "mode": "", "started": 0.0}
_LOCK = threading.Lock()
_THREAD = None


# ---------------------------------------------------------------- 检测
def _git_ok():
    """仓库是不是 git clone 出来的，且 git 能用"""
    exe = shutil.which("git")
    if not exe:
        return False, "没装 git"
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        return False, "不是 git 仓库（zip 安装）"
    return True, exe


def detect():
    """能不能自动更新、走哪条路"""
    whitelist = list(WL_DIRS) + [f for f in WL_FILES]
    if _frozen():
        # 打包版：exe 里的代码没法原地替换，只能整包换新的 exe / 压缩包
        return {"ok": True, "mode": "portable", "can_auto": False, "git": False,
                "git_detail": "打包版（exe）：代码在 exe 内部，只能整包替换",
                "root": ROOT, "zip_url": ZIP_URL, "whitelist": whitelist}
    ok, detail = _git_ok()
    mode = "git" if ok else "zip"
    return {"ok": True, "mode": mode, "can_auto": True,
            "git": ok, "git_detail": "" if ok else detail,
            "root": ROOT, "zip_url": ZIP_URL, "whitelist": whitelist}


# ---------------------------------------------------------------- 进度
def progress():
    with _LOCK:
        return dict(_PROGRESS)


def _set(**kw):
    with _LOCK:
        _PROGRESS.update(kw)


# ---------------------------------------------------------------- 备份 / 还原
def _backup_paths(paths):
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = os.path.join(WORK, "update_backup_%s" % ts)
    os.makedirs(dst, exist_ok=True)
    made = []
    for rel in paths:
        src = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(src):
            continue
        out = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, out)
            else:
                shutil.copy2(src, out)
            made.append(rel)
        except Exception:
            pass
    return dst, made


def _restore(dst, made):
    for rel in made:
        src = os.path.join(dst, rel.replace("/", os.sep))
        out = os.path.join(ROOT, rel.replace("/", os.sep))
        try:
            if os.path.isdir(src):
                shutil.rmtree(out, ignore_errors=True)
                shutil.copytree(src, out)
            else:
                shutil.copy2(src, out)
        except Exception:
            pass


# ---------------------------------------------------------------- zip 模式
def _download_zip(url, dst_dir):
    import urllib.request
    os.makedirs(dst_dir, exist_ok=True)
    zp = os.path.join(dst_dir, "master.zip")
    req = urllib.request.Request(url, headers={"User-Agent": "WA2-Kazusa-Bot"})
    with urllib.request.urlopen(req, timeout=120) as r, io.open(zp, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            buf = r.read(64 * 1024)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if total:
                _set(percent=min(60, int(got * 60 / total)),
                     message="下载中 %.1f / %.1f MB" % (got / 1048576.0, total / 1048576.0))
    return zp


def _apply_zip(zip_url=None):
    if _frozen():
        raise RuntimeError("打包版（exe）不能原地更新：请下载新的压缩包")
    tmp = tempfile.mkdtemp(prefix="wa2_upd_")
    try:
        zp = _download_zip(zip_url or ZIP_URL, tmp)
        _set(stage="解压", percent=62, message="解压中")
        with zipfile.ZipFile(zp) as z:
            z.extractall(os.path.join(tmp, "x"))
        tops = [d for d in os.listdir(os.path.join(tmp, "x")) if
                os.path.isdir(os.path.join(tmp, "x", d))]
        if not tops:
            raise RuntimeError("zip 里没有目录")
        new_root = os.path.join(tmp, "x", tops[0])
        _set(stage="备份", percent=68, message="备份现有代码")
        dst, made = _backup_paths(list(WL_DIRS) + [f for f in WL_FILES if os.path.exists(
            os.path.join(ROOT, f.replace("/", os.sep)))])
        _set(stage="覆盖", percent=74, message="覆盖代码与文档（不动角色/素材/存档）")
        try:
            for rel in WL_DIRS:
                src = os.path.join(new_root, rel.replace("/", os.sep))
                out = os.path.join(ROOT, rel.replace("/", os.sep))
                if not os.path.isdir(src):
                    continue
                if os.path.isdir(out):
                    shutil.copytree(src, out, dirs_exist_ok=True)
                    # 删掉上游已经没有的文件（只删代码/文档后缀）
                    keep = set()
                    for dp, _dn, fns in os.walk(src):
                        for fn in fns:
                            keep.add(os.path.relpath(os.path.join(dp, fn), src).lower())
                    for dp, _dn, fns in os.walk(out):
                        for fn in fns:
                            rp = os.path.relpath(os.path.join(dp, fn), out).lower()
                            if rp in keep or not fn.lower().endswith(CLEAN_EXT):
                                continue
                            try:
                                os.remove(os.path.join(dp, fn))
                            except Exception:
                                pass
                else:
                    shutil.copytree(src, out)
            for rel in WL_FILES:
                src = os.path.join(new_root, rel.replace("/", os.sep))
                if os.path.isfile(src):
                    out = os.path.join(ROOT, rel.replace("/", os.sep))
                    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                    shutil.copy2(src, out)
        except Exception:
            _set(stage="还原", percent=80, message="覆盖失败，正在还原")
            _restore(dst, made)
            raise
        ver = _local_version()
        _set(stage="完成", percent=100, message="已更新到 %s，重启程序生效" % ver)
        return {"ok": True, "mode": "zip", "version": ver, "backup": dst}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- git 模式
def _git_out(exe, *args):
    r = subprocess.run([exe, "-C", ROOT] + list(args), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    return (r.stdout or "").strip()


def _apply_git():
    if _frozen():
        raise RuntimeError("打包版（exe）不能原地更新：请下载新的压缩包")
    _ok, exe = _git_ok()          # 注意顺序：_git_ok() 返回 (能不能用, git 路径)
    # 保护开发者：有未提交改动 / 未推送提交时不 reset --hard（那会把人家的工作抹掉）
    # 只算「被跟踪文件的改动」，未跟踪文件（日志/临时文件）不算
    try:
        dirty = _git_out(exe, "status", "--porcelain", "--untracked-files=no")
    except Exception:
        dirty = ""
    if dirty:
        _set(stage="停止", percent=100, message="本机有未提交的改动，未更新")
        raise RuntimeError("本机有未提交的改动，先提交/撤销再更新（或用 zip 安装版）")
    _set(stage="备份", percent=20, message="备份现有代码")
    # 备份范围比 zip 模式再宽一点：reset --hard 会丢掉本地未提交的改动
    dst, made = _backup_paths(list(WL_DIRS) + [f for f in WL_FILES
                                               if os.path.exists(os.path.join(ROOT, f.replace("/", os.sep)))])
    try:
        _set(stage="拉取", percent=40, message="git fetch origin master")
        # 不用 --depth=1：浅克隆会切断历史，「本地有没有未推送提交」就判断不出来了
        subprocess.run([exe, "-C", ROOT, "fetch", "origin", "master"],
                       check=True, capture_output=True, timeout=300)
        # 本地有没有没推上去的提交？有就不动它
        try:
            ahead = int(_git_out(exe, "rev-list", "--count", "FETCH_HEAD..HEAD") or "0")
        except Exception:
            ahead = 0
        if ahead > 0:
            _set(stage="停止", percent=100, message="本机有 %d 个未推送的提交，未更新" % ahead)
            raise RuntimeError("本机有 %d 个未推送的提交，先推送再更新（或用 zip 安装版）" % ahead)
        _set(stage="切换", percent=70, message="git reset --hard origin/master")
        subprocess.run([exe, "-C", ROOT, "reset", "--hard", "FETCH_HEAD"],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:
        _set(stage="还原", percent=80, message="git 更新失败，正在还原")
        _restore(dst, made)
        raise RuntimeError("git 更新失败：%s" % str(e)[:200])
    ver = _local_version()
    _set(stage="完成", percent=100, message="已更新到 %s，重启程序生效" % ver)
    return {"ok": True, "mode": "git", "version": ver, "backup": dst}


def _local_version():
    if _frozen():
        return _ver.VERSION          # 打包版没有 version.json，版本号就在 exe 里
    try:
        # utf-8-sig：记事本改过的 version.json 带 BOM，按 utf-8 读会直接抛异常
        with io.open(os.path.join(ROOT, "version.json"), encoding="utf-8-sig") as f:
            return str(json.load(f).get("version") or "")
    except Exception:
        return ""


# ---------------------------------------------------------------- 入口
def apply(zip_url=None, wait=False):
    """开始更新（默认后台线程）。返回进度快照。"""
    global _THREAD
    if _frozen():
        # 打包版没有源码树：绝不假装更新（以前会往 exe 上一级目录乱写一套 program/src）
        _set(running=False, stage="停止", percent=0, ok=False,
             message="请下载新版本压缩包",
             error="打包版（exe）不能原地更新：请下载新的压缩包，解压后替换整个文件夹")
        return progress()
    if progress().get("running"):
        return progress()
    _set(running=True, stage="准备", percent=2, message="准备更新",
         ok=None, error="", version="", mode="", started=time.time())
    ok, _detail = _git_ok()
    _set(mode="git" if ok else "zip")

    def run():
        try:
            res = _apply_git() if ok else _apply_zip(zip_url)
            _set(running=False, ok=True, version=res.get("version", ""),
                 percent=100, message=res.get("message") or "完成")
        except Exception as e:
            _set(running=False, ok=False, error="%s: %s" % (type(e).__name__, e),
                 message="更新失败")
            try:
                import applog
                applog.warn("一键更新失败: %s" % e)
            except Exception:
                pass

    _THREAD = threading.Thread(target=run, daemon=True)
    _THREAD.start()
    if wait:
        _THREAD.join(timeout=600)
    return progress()


def reset_progress():
    """自测 / 重试用"""
    global _THREAD
    _THREAD = None
    _set(running=False, stage="", percent=0, message="", ok=None, error="",
         version="", mode="", started=0.0)
