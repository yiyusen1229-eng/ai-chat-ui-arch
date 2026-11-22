# -*- coding: utf-8 -*-
"""一键更新（updater.py）的自测：不联网、不动真实仓库。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_update.py

验的是：
  1. detect()：有 .git + 有 git → 走 git；否则走 zip
  2. zip 模式：只覆盖代码/文档白名单，**角色/素材/存档一个都不动**
  3. zip 模式：上游删掉的文件本地也会删（不会留孤儿）
  4. zip 模式：覆盖前有备份，备份里有旧内容
  5. zip 模式：坏包（没有顶层目录）→ 报错，本地文件不变
  6. progress()：开始 → 运行中 → ok/percent 100
  7. 白名单常量不许把 build_env / res / logs 写进去（这是最容易出的错）
"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import updater  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with io.open(path, encoding="utf-8") as f:
        return f.read()


def make_zip(dst, files, top="ai-chat-ui-arch-master"):
    """files: {相对路径: 内容}"""
    with zipfile.ZipFile(dst, "w") as z:
        for rel, text in files.items():
            z.writestr("%s/%s" % (top, rel), text)
    return dst


def _rmtree(path):
    """删干净：Windows 下 git 会把 .git/objects 里的文件标成只读，直接 rmtree 会留半套"""

    def _retry(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass

    kw = {"onexc": _retry} if sys.version_info >= (3, 12) else {"onerror": _retry}
    try:
        shutil.rmtree(path, **kw)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_upd_test_")
    try:
        return _run(tmp)
    finally:
        # 以前不清理：跑一趟就在 TEMP 里留一套假仓库 + 裸仓，跑多了能堆几百份
        _rmtree(tmp)


def _run(tmp):
    root = os.path.join(tmp, "repo")
    prog = os.path.join(root, "program")
    work = os.path.join(prog, "work_scratch")

    # 假的"本机安装"
    write(os.path.join(root, "version.json"), json.dumps({"version": "2026-11-05.2"}))
    write(os.path.join(prog, "src", "old_mod.py"), "# 旧模块，上游已经删了\n")
    write(os.path.join(prog, "src", "keep.py"), "# 会被覆盖\n")
    write(os.path.join(prog, "src", "ui", "game.css"), "body{}\n")
    # 用户数据：一个都不许动
    write(os.path.join(prog, "build_env", "characters", "和纱", "name.txt"), "和纱")
    write(os.path.join(prog, "build_env", "sessions", "auto.json"), '{"a": 1}')
    write(os.path.join(prog, "res", "assets", "cg", "x.jpg"), "jpg")
    write(os.path.join(prog, "logs", "app.log"), "log")
    os.makedirs(work, exist_ok=True)

    # 指到假目录
    updater.ROOT = root
    updater.UP = prog
    updater.WORK = work
    updater.reset_progress()

    # ---------------- 1 detect
    updater.ROOT = root
    d = updater.detect()
    check("没有 .git → 走 zip 模式", d["mode"] == "zip" and d["can_auto"] is True, d)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    d2 = updater.detect()
    git = shutil.which("git") is not None
    check("有 .git → 走 git 模式（装了 git 时）", (d2["mode"] == "git") == git, d2)
    shutil.rmtree(os.path.join(root, ".git"), ignore_errors=True)

    # ---------------- 7 白名单
    banned = ("build_env", "res", "logs", "cache", "characters")
    hit = [p for p in list(updater.WL_DIRS) + list(updater.WL_FILES)
           if any(b in p.split("/") for b in banned)]
    check("白名单里没有用户数据目录", not hit, hit)

    # ---------------- 2/3/4 zip 正常
    zp = os.path.join(tmp, "good.zip")
    make_zip(zp, {
        "version.json": json.dumps({"version": "2026-11-06.1"}),
        "program/src/keep.py": "# 新版本\n",
        "program/src/new_mod.py": "# 新加的\n",
        "program/src/ui/game.css": "body{color:red}\n",
        "README.md": "# 新 README\n",
        # 上游 zip 里就算带了用户数据，也不该被复制
        "program/build_env/characters/和纱/name.txt": "被别人改过的名字",
    })
    url = "file:///" + zp.replace("\\", "/")
    p = updater.apply(zip_url=url, wait=True)
    time.sleep(0.05)
    p = updater.progress()
    check("更新成功", p.get("ok") is True, p)
    check("进度到 100", p.get("percent") == 100, p)
    check("报出新版本号", p.get("version") == "2026-11-06.1", p.get("version"))

    check("代码文件被覆盖", read(os.path.join(prog, "src", "keep.py")) == "# 新版本\n")
    check("新模块加进来了", os.path.exists(os.path.join(prog, "src", "new_mod.py")))
    check("根文件也更新", read(os.path.join(root, "README.md")) == "# 新 README\n")
    check("version.json 更新", json.loads(read(os.path.join(root, "version.json")))["version"] == "2026-11-06.1")
    check("上游已删的旧模块被清掉", not os.path.exists(os.path.join(prog, "src", "old_mod.py")))

    check("角色数据没动", read(os.path.join(prog, "build_env", "characters", "和纱", "name.txt")) == "和纱")
    check("存档没动", read(os.path.join(prog, "build_env", "sessions", "auto.json")) == '{"a": 1}')
    check("素材没动", read(os.path.join(prog, "res", "assets", "cg", "x.jpg")) == "jpg")
    check("日志没动", read(os.path.join(prog, "logs", "app.log")) == "log")

    baks = [d for d in os.listdir(work) if d.startswith("update_backup_")]
    check("留了备份目录", len(baks) == 1, baks)
    if baks:
        b = os.path.join(work, baks[0], "program", "src")
        check("备份里有覆盖前的旧内容", os.path.exists(os.path.join(b, "old_mod.py")), os.listdir(b))

    # ---------------- 5 坏包
    updater.reset_progress()
    bad = os.path.join(tmp, "bad.zip")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("no_top_dir.txt", "x")
    before = read(os.path.join(prog, "src", "keep.py"))
    try:
        updater.apply(zip_url="file:///" + bad.replace("\\", "/"), wait=True)
    except Exception:
        pass
    p2 = updater.progress()
    check("坏包报失败", p2.get("ok") is False and p2.get("error"), p2)
    check("坏包不动本地文件", read(os.path.join(prog, "src", "keep.py")) == before)

    # ---------------- 6 运行中状态
    updater.reset_progress()
    p3 = updater.apply(zip_url=url)
    check("apply 立刻返回（不等下载）", isinstance(p3, dict) and "percent" in p3, p3)
    t0 = time.time()
    while time.time() - t0 < 20:
        if not updater.progress().get("running"):
            break
        time.sleep(0.1)
    check("最终不再 running", updater.progress().get("running") is False, updater.progress())

    # ---------------- 7 git 模式（本地裸仓模拟远端，不联网）
    git = shutil.which("git")
    if git:
        bare = os.path.join(tmp, "upstream.git")
        clone = os.path.join(tmp, "clone")

        def sh(*args, cwd=None):
            return subprocess.run([git] + list(args), cwd=cwd or tmp,
                                  capture_output=True, text=True, encoding="utf-8")

        sh("init", "--bare", "-q", bare)
        sh("clone", "-q", bare, clone)
        # 远端先有一版
        write(os.path.join(clone, "program", "src", "keep.py"), "# v1\n")
        sh("add", "-A", cwd=clone)
        sh("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v1", cwd=clone)
        sh("push", "-q", "origin", "HEAD:master", cwd=clone)

        # 远端再来一版（相当于作者推送了新代码）
        other = os.path.join(tmp, "other_clone")
        sh("clone", "-q", bare, other)
        write(os.path.join(other, "program", "src", "keep.py"), "# v2 新代码\n")
        write(os.path.join(other, "program", "src", "brand_new.py"), "# 新文件\n")
        sh("add", "-A", cwd=other)
        sh("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "v2", cwd=other)
        sh("push", "-q", "origin", "HEAD:master", cwd=other)

        updater.ROOT = clone
        updater.UP = os.path.join(clone, "program")
        updater.WORK = os.path.join(clone, "program", "work_scratch")
        updater.reset_progress()
        d3 = updater.detect()
        check("git 仓库识别为 git 模式", d3["mode"] == "git", d3)
        updater.apply(wait=True)
        pg = updater.progress()
        check("git 模式更新成功", pg.get("ok") is True, pg)
        check("git 模式拉到新代码", read(os.path.join(clone, "program", "src", "keep.py")) == "# v2 新代码\n",
              read(os.path.join(clone, "program", "src", "keep.py")))
        check("git 模式新增文件也在", os.path.exists(os.path.join(clone, "program", "src", "brand_new.py")))
        check("git 模式也留了备份", any(x.startswith("update_backup_")
                                       for x in os.listdir(updater.WORK)))

        # 保护开发者：有未提交改动 → 拒绝更新，改动保住
        write(os.path.join(clone, "program", "src", "keep.py"), "# 本地没提交的改动\n")
        updater.reset_progress()
        updater.apply(wait=True)
        pg2 = updater.progress()
        check("有未提交改动 → 不更新", pg2.get("ok") is False, pg2)
        check("未提交改动被保住",
              "本地没提交的改动" in read(os.path.join(clone, "program", "src", "keep.py")),
              read(os.path.join(clone, "program", "src", "keep.py")))
        sh("checkout", "--", ".", cwd=clone)

        # 保护开发者：本地有未推送的提交 → 拒绝更新，提交保住
        sh("pull", "-q", "origin", "master", cwd=clone)
        write(os.path.join(clone, "program", "src", "local_only.py"), "# 只在本地\n")
        sh("add", "-A", cwd=clone)
        sh("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "local", cwd=clone)
        updater.reset_progress()
        updater.apply(wait=True)
        pg3 = updater.progress()
        check("有未推送提交 → 不更新", pg3.get("ok") is False, pg3)
        check("未推送的提交还在", os.path.exists(os.path.join(clone, "program", "src", "local_only.py")))
    else:
        print("  skip  git 模式（这台机器没装 git）")

    # ---------------- 8 版本号一致性（2026-11-22 加：11-22.1 只写日志、忘了 bump）
    import re as _re
    repo = os.path.dirname(PROGRAM)
    vj = os.path.join(repo, "version.json")
    vpy = os.path.join(PROGRAM, "src", "version.py")
    if os.path.isfile(vj) and os.path.isfile(vpy):
        try:
            jd = json.loads(read(vj))
        except Exception:
            jd = {}
        jv = str(jd.get("version") or "")
        m = _re.search(r'VERSION\s*=\s*["\']([^"\']+)["\']', read(vpy))
        pv = m.group(1) if m else ""
        check("version.json 与 version.py 版本号一致", bool(jv) and jv == pv, (jv, pv))
        check("版本号格式 YYYY-MM-DD.N", bool(_re.match(r"^\d{4}-\d{2}-\d{2}\.\d+$", jv)), jv)
        check("version.json 有 url / notes", bool(jd.get("url")) and bool(jd.get("notes")), jd)
    else:
        print("  skip  版本号一致性（找不到仓库根 version.json）")

    # ---------------- 9 打包版（exe）不许假装能更新
    # 真实踩到的坑：11-06 的便携版把「更新」写进了 exe 上一级目录（release/ 里那套
    # 源码副本），exe 本身一个字没变 → 每次重开都还在提示同一个更新。
    updater.reset_progress()
    orig_frozen = updater._frozen
    orig_exec = sys.executable
    updater._frozen = lambda: True
    try:
        d4 = updater.detect()
        check("打包版识别为 portable", d4.get("mode") == "portable", d4)
        check("打包版 can_auto=False", d4.get("can_auto") is False, d4)
        check("打包版给出拒绝原因", "打包版" in (d4.get("git_detail") or ""), d4)
        updater.apply(wait=True)
        p4 = updater.progress()
        check("打包版 apply 直接拒绝", p4.get("ok") is False and "打包版" in (p4.get("error") or ""), p4)
        check("打包版没动本地文件", read(os.path.join(prog, "src", "keep.py")) == "# 新版本\n")
        # ROOT 不许指到 exe 上一级
        fake_dir = os.path.join(tmp, "app")
        os.makedirs(fake_dir, exist_ok=True)
        sys.executable = os.path.join(fake_dir, "和纱.exe")
        u5, r5 = updater._dirs()
        same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
        check("打包版 ROOT = exe 所在目录（不是上一级）",
              same(u5, fake_dir) and same(r5, fake_dir), (u5, r5))
    finally:
        updater._frozen = orig_frozen
        sys.executable = orig_exec
        updater.reset_progress()

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
