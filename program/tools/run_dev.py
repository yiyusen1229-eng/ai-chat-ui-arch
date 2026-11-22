# -*- coding: utf-8 -*-
"""开发运行：把 src/ 同步到 build_env/ 再启动（改完代码立刻生效）

    python tools/run_dev.py                同步 + 启动
    python tools/run_dev.py --sync-only    只同步，不启动（自测 / 打包前刷新）
    python tools/run_dev.py --watch        同步 + 启动，然后盯着 src/：
                                           改了自动重新同步并重启程序

program/  = 实时程序（源码 + 这个入口）
release/  = 打包好的分发版本

为什么要有 --watch：前端脚本的缓存版本号（index.html 的 ?v=）是同步时换的，
WebView2 还认旧的；所以「改完必须重新同步 + 重启程序」这一步以前只能靠人记。
现在交给它盯：改完一两秒内自动重来。
"""
import io
import os
import re
import shutil
import subprocess
import sys
import time

try:
    # 被重定向到文件 / 管道时也能立刻看到进度（否则要等缓冲满了才出）
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "src")
RES = os.path.join(BASE, "res")
ENV = os.path.join(BASE, "build_env")
PY = os.path.join(ENV, "venv", "Scripts", "python.exe")

# 根目录的单文件
DATA = ("story_index.json", "fewshot.json")
# 各自一个文件夹的数据（人设 / 世界书）
DATA_DIRS = ("persona", "worldbook")

# 盯哪些文件：只盯「写代码会动」的；运行时数据变了不该重启（否则一直重启）
WATCH_EXT = (".py", ".js", ".css", ".html", ".json", ".txt", ".md")
WATCH_SKIP_DIRS = {"__pycache__", "characters", "sessions", "cache", "logs",
                   "text_cache", "release", "build_env", ".git"}
POLL_S = 1.0            # 多久看一次
SETTLE_S = 1.2          # 停下来多久才动手（正在保存 / 批量改时别打成筛子）


# ------------------------------------------------------------------ 同步
def sync(quiet=False):
    """src → build_env：后端 .py + 数据、前端 ui2、缓存版本号"""
    n = 0
    for f in os.listdir(SRC):
        if f.endswith(".py") or f in DATA:
            shutil.copy2(os.path.join(SRC, f), os.path.join(ENV, f))
            n += 1
    for d in DATA_DIRS:
        s = os.path.join(SRC, d)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(ENV, d), dirs_exist_ok=True)
            n += 1
    # 人设 / 世界书已经迁到子文件夹：删掉根目录的旧副本，免得两份同时存在
    for legacy in ("persona.txt", "persona_core.txt", "worldbook.json"):
        p = os.path.join(ENV, legacy)
        if os.path.exists(p):
            try:
                os.remove(p)
                if not quiet:
                    print("  清掉旧位置的 %s" % legacy)
            except OSError:
                pass
    if not quiet:
        print("  同步 %d 个后端文件 → build_env/" % n)

    # 组装前端（src/ui → build_env/ui2）
    ui2 = os.path.join(ENV, "ui2")
    shutil.rmtree(ui2, ignore_errors=True)
    os.makedirs(ui2, exist_ok=True)
    for f in os.listdir(os.path.join(SRC, "ui")):
        shutil.copy2(os.path.join(SRC, "ui", f), os.path.join(ui2, f))
    if not quiet:
        print("  组装 ui2 完成（素材不再复制，运行时直接读 res/assets）")

    # 换缓存版本号：不换的话 WebView2 会继续用旧脚本，改了代码看不到效果
    try:
        stamp = time.strftime("%Y%m%d%H%M%S")
        idx = os.path.join(ui2, "index.html")
        html = io.open(idx, encoding="utf-8").read()
        html, cnt = re.subn(r"\?v=[0-9A-Za-z]+", "?v=" + stamp, html)
        io.open(idx, "w", encoding="utf-8").write(html)
        if not quiet:
            print("  缓存版本号 → ?v=%s（%d 处）" % (stamp, cnt))
    except Exception as e:
        print("  换版本号失败（不影响运行）:", e)
    return n


# ------------------------------------------------------------------ 启动
def launch():
    env = os.environ.copy()
    env["WA2_ASSETS_DIR"] = os.path.join(RES, "assets")   # 界面素材直接读 res/
    return subprocess.Popen([PY, "main.py"], cwd=ENV, env=env)


def _stop(proc, timeout=8):
    """先好好关，关不掉再强杀（窗口里可能还有没存的东西）"""
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        for _ in range(int(timeout * 10)):
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        proc.kill()
    except Exception:
        pass


# ------------------------------------------------------------------ 监视
def _sig():
    """src 下代码文件的签名：{相对路径: (mtime_ns, 大小)}"""
    out = {}
    for dp, dn, fs in os.walk(SRC):
        dn[:] = [d for d in dn if d not in WATCH_SKIP_DIRS]
        for f in fs:
            if not f.endswith(WATCH_EXT):
                continue
            p = os.path.join(dp, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            rel = os.path.relpath(p, SRC).replace(os.sep, "/")   # 统一用 /，打印也好看
            out[rel] = (st.st_mtime_ns, st.st_size)
    return out


def _changed(old, new):
    out = [k for k in new if old.get(k) != new.get(k)]
    out += [k for k in old if k not in new]
    return sorted(out)


def _print_changed(files, limit=6):
    names = files[:limit]
    more = "" if len(files) <= limit else " 等 %d 个" % len(files)
    print("  改动：%s%s" % ("、".join(names), more))


def watch(proc):
    """盯着 src/：稳定后重新同步并重启；窗口被关掉就退出"""
    base = _sig()
    print("  自动重载已开：改完 src/ 里的文件会自动同步并重启（Ctrl+C 退出）")
    pend, pend_at = None, 0.0
    try:
        while True:
            time.sleep(POLL_S)
            if proc.poll() is not None:
                print("  窗口已关闭，停止监视")
                return 0
            sig = _sig()
            if sig == base:
                pend, pend_at = None, 0.0
                continue
            if pend is None or _changed(pend, sig):
                pend, pend_at = sig, time.time()     # 还在变，等它停下来
                continue
            if time.time() - pend_at < SETTLE_S:
                continue
            files = _changed(base, sig)
            print("\n  [%s] 检测到改动" % time.strftime("%H:%M:%S"))
            _print_changed(files)
            base = sig
            pend, pend_at = None, 0.0
            # 先关旧进程再同步：ui2 是整目录重建的，边跑边重建会让它在请求里读到空目录
            _stop(proc)
            sync(quiet=True)
            print("  同步完成，重启程序…")
            proc = launch()
    except KeyboardInterrupt:
        print("\n  退出：关掉程序")
        _stop(proc)
        return 0


# ------------------------------------------------------------------ 入口
def main():
    sync()
    if "--sync-only" in sys.argv:
        print("  --sync-only：同步完成，不启动")
        return 0
    print("  启动程序…")
    proc = launch()
    try:
        if "--watch" in sys.argv:
            return watch(proc)
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        _stop(proc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
