# -*- coding: utf-8 -*-
"""把角色的聊天模型从本地切回云端（或反过来），一键恢复。

背景：2026-11-15 把三个角色的 chat 段指到了本机 abliterated 14B 做实验，
原云端配置备份在：
    characters/<角色>/model.json.cloud.bak        （逐角色的完整原文件）
    build_env/backup_cloud_models_20261115/       （整份拷贝 + 原始值汇总）

跑法（在 program 目录下）：
    build_env/venv/Scripts/python.exe tools/restore_cloud_models.py            # 只看，不动
    build_env/venv/Scripts/python.exe tools/restore_cloud_models.py --apply    # 真的恢复
    build_env/venv/Scripts/python.exe tools/restore_cloud_models.py --local    # 反过来：全部指向本地
"""
import io
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

BACKUP_NAME = "model.json.cloud.bak"
LOCAL_CHAT = {"endpoint": "http://127.0.0.1:11434", "key": "",
              "model": "richardyoung/qwen2.5-14b-instruct-abliterated", "connected": True}


def _characters_root():
    """角色目录：开发环境在 program/build_env/characters，打包版在 exe 旁边

    2026-11-22：去掉了最后那句 `return os.path.join(PROGRAM, "characters")` 的兜底 ——
    数据根只有一个（`datadir.root()`），`program/characters` 是 11-21 之前那个老 bug
    留下的副本，已经归档走人；再兜底回去只会让程序读到旧数据。
    """
    dev = os.path.join(PROGRAM, "build_env", "characters")
    if os.path.isdir(dev):
        return dev
    try:
        import datadir
        return os.path.join(datadir.root(), "characters")
    except Exception:
        return dev


def _dump(j):
    return json.dumps(j, ensure_ascii=False)


def main():
    apply = "--apply" in sys.argv
    to_local = "--local" in sys.argv
    root = _characters_root()
    print("角色目录:", root)
    if not os.path.isdir(root):
        print("找不到角色目录")
        return 1
    done = 0
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        cur = os.path.join(d, "model.json")
        bak = os.path.join(d, BACKUP_NAME)
        if not os.path.isdir(d) or not os.path.isfile(cur):
            continue
        if to_local:
            with io.open(cur, encoding="utf-8") as f:
                j = json.load(f) or {}
            j["chat"] = dict(LOCAL_CHAT)
        else:
            if not os.path.isfile(bak):
                print("  跳过 %s（没有备份）" % name)
                continue
            with io.open(bak, encoding="utf-8") as f:
                j = json.load(f) or {}
        print("  %s → chat%s" % (name, " = 本地" if to_local else " = 云端 " + _dump(j.get("chat") or {})))
        if apply:
            with io.open(cur, "w", encoding="utf-8", newline="\n") as f:
                json.dump(j, f, ensure_ascii=False, indent=2)
            done += 1
    if not apply:
        print("\n（只是预览；确认无误后加 --apply 真的写进去）")
    else:
        print("\n已写回 %d 个角色" % done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
