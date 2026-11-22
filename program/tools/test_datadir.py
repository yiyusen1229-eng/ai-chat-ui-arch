# -*- coding: utf-8 -*-
"""数据根归属自测：程序数据只许落在 `program/build_env/` 一个地方。

为什么要有这个（2026-11-22）：
    用户看到「program 文件夹里怎么也有个 characters 文件夹」—— 查下来是 2026-11-21
    统一数据根（`datadir.py`）之前留下的副本：
      · `program/src/characters/`（240 MB，864 个文件，还带着旧存档）
      · `program/characters/`、`program/logs/`、`program/cache/`
    它们已经归档到 `other/dev/_archive/data_roots_2026-11-22/`，这个测试盯着别再长出来：
    代码树里出现数据根副本 = 有代码又按 `__file__` 算路径了，那是要修的 bug，不是"顺手"。

跑：
    cd program
    build_env/venv/Scripts/python.exe tools/test_datadir.py
"""
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import datadir  # noqa: E402
import characters  # noqa: E402
import persona_ctx  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    root = datadir.root()
    print("[1] 数据根 = build_env（源码树运行时）")
    check("数据根存在", os.path.isdir(root), root)
    check("数据根是 program/build_env", os.path.basename(root) == "build_env", root)
    check("数据根不在 program/ 顶层", os.path.normcase(root) != os.path.normcase(PROGRAM), root)
    check("数据根不在 program/src 下",
          not os.path.normcase(root).startswith(os.path.normcase(os.path.join(PROGRAM, "src"))), root)
    check("打包版判定函数在（这里不是打包版）", datadir._frozen() is False, datadir._frozen())

    print("[2] 角色 / 缓存 / 日志都在数据根下")
    check("characters 根在数据根下",
          os.path.normcase(characters.root()) ==
          os.path.normcase(os.path.join(root, "characters")), characters.root())
    check("characters 根存在", os.path.isdir(characters.root()), characters.root())
    check("缓存目录在数据根下",
          os.path.normcase(datadir.cache_dir()) ==
          os.path.normcase(os.path.join(root, "cache")), datadir.cache_dir())
    try:
        import applog
        ld = applog._log_dir()
        check("日志目录在数据根下",
              os.path.normcase(os.path.abspath(ld)) ==
              os.path.normcase(os.path.join(root, "logs")), ld)
    except Exception as e:
        check("日志目录在数据根下", False, "%s: %s" % (type(e).__name__, e))

    print("[3] 代码树里不许有数据根副本（2026-11-22 清过一遍）")
    ghosts = [
        os.path.join(PROGRAM, "src", "characters"),
        os.path.join(PROGRAM, "src", "logs"),
        os.path.join(PROGRAM, "src", "cache"),
        os.path.join(PROGRAM, "characters"),
        os.path.join(PROGRAM, "logs"),
        os.path.join(PROGRAM, "cache"),
    ]
    for g in ghosts:
        check("不存在 %s" % os.path.relpath(g, PROGRAM), not os.path.exists(g), g)

    print("[4] 模板不是数据根：src/persona、src/worldbook 只当新建角色的种子")
    check("persona_ctx.PERSONA_DIR 指向 src/persona",
          os.path.normcase(os.path.abspath(persona_ctx.PERSONA_DIR)) ==
          os.path.normcase(os.path.join(PROGRAM, "src", "persona")),
          persona_ctx.PERSONA_DIR)
    check("模板目录不叫 characters",
          os.path.basename(persona_ctx.PERSONA_DIR) != "characters", persona_ctx.PERSONA_DIR)

    print("[5] WA2_DATA 能整个换掉数据根")
    tmp = tempfile.mkdtemp(prefix="wa2_data_root_")
    old = os.environ.get("WA2_DATA")
    try:
        os.environ["WA2_DATA"] = tmp
        check("WA2_DATA 生效", os.path.normcase(datadir.root()) ==
              os.path.normcase(os.path.abspath(tmp)), datadir.root())
        check("换了数据根后 characters 也跟着换",
              os.path.normcase(characters.root()) ==
              os.path.normcase(os.path.join(os.path.abspath(tmp), "characters")),
              characters.root())
    finally:
        if old is None:
            os.environ.pop("WA2_DATA", None)
        else:
            os.environ["WA2_DATA"] = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
