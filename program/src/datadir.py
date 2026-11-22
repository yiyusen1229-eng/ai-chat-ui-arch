# -*- coding: utf-8 -*-
"""数据目录：所有「运行时会变」的东西都放这一个根下面。

为什么单独一个模块（以前是各算各的）：

    characters   WA2_USER_DIR 或 __file__
    sessions     WA2_SESS_DIR 或 __file__/sessions
    memory       WA2_CACHE_DIR 或 __file__/cache
    pricing      __file__/cache                 ← 连环境变量都没有
    llm 预算     __file__/../cache              ← 跑到程序目录外面去了
    tts 缓存     __file__/../cache/tts          ← 同上
    日志         __file__/../logs               ← 同上

结果一台机器上有两个 cache、两个 logs，打包版还会写到 exe 外面去。
现在只有这里知道根在哪，别的模块一律 `datadir.sub("cache")`。

覆盖顺序（从高到低）：
  1. 具体项的环境变量：WA2_USER_DIR / WA2_SESS_DIR / WA2_CACHE_DIR / WA2_LOG_DIR
  2. WA2_DATA：整个根换掉（换机器只拷这一个文件夹）
  3. 默认：开发时是程序目录（program/build_env），打包版是 exe 同目录

`ensure()` 会把开发布局里老位置（program/cache、program/logs）的东西搬进来，
同盘移动，不复制；搬不动（文件被占用）就跳过，下次启动再试。
"""
import os
import shutil
import sys

# 老位置只认「开发布局」：数据根叫 build_env 时，它的上一级就是 program/
DEV_ROOT_NAME = "build_env"

_DONE = {"v": False}


def program_dir():
    """程序自己所在的目录（源码 / 素材 / 前端），只读"""
    return os.path.dirname(os.path.abspath(__file__))


def _frozen():
    """打包版：数据放 exe 旁边，不放 _MEIPASS 临时目录"""
    return bool(getattr(sys, "_MEIPASS", None))


def root():
    """数据根：WA2_DATA > 打包版 exe 同目录 > 程序目录（build_env）

    自测脚本会把 src 直接挂进 sys.path 再 import，这时程序目录就是源码树：
    日志 / 缓存不该写进去。源码树旁边有 build_env/ 就用它当数据根
    （和 tools/run_dev.py 起的一致），没有才退回上一级（program/）。
    """
    env = (os.environ.get("WA2_DATA") or "").strip()
    if env:
        return os.path.abspath(env)
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    d = program_dir()
    if (os.path.basename(d) == "src" and os.path.isfile(os.path.join(d, "main.py"))
            and os.path.isdir(os.path.join(os.path.dirname(d), "tools"))):
        prog = os.path.dirname(d)
        # 直接 `python src/main.py` 时也必须用 build_env 的数据！
        # 以前退回 program/，于是在 program/ 下长出一套**空数据**
        # （characters 只有模板和纱、没有雪菜），用户看到的是
        # 「我的雪菜角色文件夹怎么突然没了」——2026-11-21 实际踩过。
        env_root = os.path.join(prog, DEV_ROOT_NAME)
        if os.path.isdir(env_root):
            return env_root
        return prog
    return d


def sub(name):
    """数据根下的一级目录，只拼路径不建目录（谁写谁建）"""
    return os.path.join(root(), name)


# ------------------------------------------------------------------ 环境变量
def env_or(key, name):
    """具体项的环境变量优先，没有就用数据根下的 name"""
    v = (os.environ.get(key) or "").strip()
    return os.path.abspath(v) if v else sub(name)


def cache_dir():
    """缓存目录（记忆书缓存 / 价格表 / 语音 / 字模都在这儿）"""
    return env_or("WA2_CACHE_DIR", "cache")


def text_cache():
    """字模渲染缓存：程序自己写、perf 数条数、清理面板按「缓存」一起删

    以前放在程序的 res/assets/ui/ 里 —— 那是"程序素材"的位置，写进去等于让程序
    往自己的安装目录里拉屎；删素材目录时还会连缓存一起删掉。
    """
    return os.path.join(cache_dir(), "text_cache")


# ------------------------------------------------------------------ 迁移
def _dev_old_base():
    """开发布局里老位置的上一级（program/）；不是开发布局就返回空串"""
    if _frozen():
        return ""
    r = os.path.normcase(root())
    if os.path.basename(r) != DEV_ROOT_NAME:
        return ""
    return os.path.dirname(root())


def _merge_move(src, dst):
    """把 src 里的文件搬到 dst（dst 已有同名文件就跳过）。返回搬了几个。"""
    moved = 0
    for dirpath, _dirnames, files in os.walk(src):
        for fn in files:
            sp = os.path.join(dirpath, fn)
            rel = os.path.relpath(sp, src)
            dp = os.path.join(dst, rel)
            try:
                if os.path.exists(dp):
                    continue
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.move(sp, dp)
                moved += 1
            except OSError:
                continue                      # 被占用 / 只读：下次启动再试
    try:
        for dirpath, dirnames, files in os.walk(src, topdown=False):
            if not files and not dirnames:
                os.rmdir(dirpath)             # 空了才删得掉
    except OSError:
        pass
    return moved


def ensure():
    """启动时调一次：把老位置的缓存 / 日志搬进数据根。可重复调用，不抛异常。"""
    if _DONE["v"]:
        return {}
    _DONE["v"] = True
    made = {}
    base = _dev_old_base()
    if not base:
        return made
    for name in ("cache", "logs"):
        old = os.path.join(base, name)
        new = sub(name)
        if not os.path.isdir(old):
            continue
        if os.path.normcase(os.path.abspath(old)) == os.path.normcase(os.path.abspath(new)):
            continue
        n = _merge_move(old, new)
        if n:
            made[name] = n
            _log("数据目录：%s 的 %d 个文件搬进 %s" % (old, n, new))
    return made


def _log(msg):
    try:
        print("[datadir] " + msg, file=sys.stderr)
    except Exception:
        pass


# ------------------------------------------------------------------ 快照
def info():
    """给 /api/diag 和排查用：根在哪、各项落在哪、是环境变量还是默认"""
    def one(key, name):
        v = (os.environ.get(key) or "").strip()
        return {"path": env_or(key, name), "source": "env:" + key if v else "default"}

    return {
        "root": root(),
        "frozen": _frozen(),
        "data_env": bool((os.environ.get("WA2_DATA") or "").strip()),
        "characters": one("WA2_USER_DIR", "characters"),
        "sessions": one("WA2_SESS_DIR", "sessions"),
        "cache": one("WA2_CACHE_DIR", "cache"),
        "logs": one("WA2_LOG_DIR", "logs"),
    }
