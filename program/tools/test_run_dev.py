# -*- coding: utf-8 -*-
"""开发入口的自测（不开窗口、不联网）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_run_dev.py

验的是「改完自动重来」这条链路里最容易错的部分：
  1. _sig 只盯代码文件：素材、存档、缓存、立绘这些变了不该重启
  2. _changed 能认出改动 / 新增 / 删除
  3. 窗口被关掉时 watch 立刻退出（不会一直转下去）
  4. _stop 能把子进程收干净（先 terminate，超时才 kill）
"""
import os
import subprocess
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import run_dev as RD  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(path, text="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_dev_")
    fake_src = os.path.join(tmp, "src")
    write(os.path.join(fake_src, "ui", "a.js"))
    write(os.path.join(fake_src, "ui", "b.css"))
    write(os.path.join(fake_src, "main.py"))
    write(os.path.join(fake_src, "persona", "base.txt"))
    # 这些都不该参与重启判断
    write(os.path.join(fake_src, "ui", "cover.png"))
    write(os.path.join(fake_src, "ui", "__pycache__", "x.py"))
    write(os.path.join(fake_src, "characters", "和纱", "memory", "旧事.txt"))
    write(os.path.join(fake_src, "sessions", "slot1.json"))
    write(os.path.join(fake_src, "cache", "prices.json"))

    real_src = RD.SRC
    RD.SRC = fake_src
    try:
        print("[1] _sig 只盯代码")
        sig = RD._sig()
        keys = set(sig)
        check("认 .js / .css / .py / .txt", {"ui/a.js", "ui/b.css", "main.py", "persona/base.txt"} <= keys,
              sorted(keys))
        check("不认图片", not any(k.endswith(".png") for k in keys), sorted(keys))
        check("跳过 __pycache__", not any("__pycache__" in k for k in keys), sorted(keys))
        check("跳过 characters（立绘/记忆变了几十次也别重启）",
              not any(k.startswith("characters") for k in keys), sorted(keys))
        check("跳过 sessions / cache",
              not any(k.startswith(("sessions", "cache")) for k in keys), sorted(keys))

        print("\n[2] _changed：改 / 增 / 删")
        old = dict(sig)
        t = time.time()
        os.utime(os.path.join(fake_src, "ui", "a.js"), (t, t))
        new = RD._sig()
        check("改一个文件只报一个", RD._changed(old, new) == ["ui/a.js"], RD._changed(old, new))
        write(os.path.join(fake_src, "ui", "c.js"))
        new2 = RD._sig()
        check("新增文件认得出", RD._changed(new, new2) == ["ui/c.js"], RD._changed(new, new2))
        os.remove(os.path.join(fake_src, "ui", "c.js"))
        new3 = RD._sig()
        check("删除文件认得出", RD._changed(new2, new3) == ["ui/c.js"], RD._changed(new2, new3))
        check("没变化就是空", RD._changed(new3, RD._sig()) == [], RD._changed(new3, RD._sig()))

        print("\n[3] 窗口关了，watch 要自己退出")
        class _Dead(object):
            def poll(self):
                return 0

        t0 = time.time()
        code = RD.watch(_Dead())
        check("返回值 0", code == 0, code)
        check("不等满一轮也该很快退出", time.time() - t0 < 5, round(time.time() - t0, 1))
    finally:
        RD.SRC = real_src

    print("\n[4] _stop：先关再杀")
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    t0 = time.time()
    RD._stop(p, timeout=5)
    cost = time.time() - t0
    check("子进程真的没了", p.poll() is not None, p.poll())
    check("没有硬等满超时", cost < 5, round(cost, 1))
    RD._stop(None)
    RD._stop(p)          # 已经死了再调一次也不该炸
    check("重复调用安全", True)

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
