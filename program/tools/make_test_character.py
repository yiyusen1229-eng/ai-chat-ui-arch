# -*- coding: utf-8 -*-
"""建一个「测试角色」：最小配置，用来验证多角色链路（无立绘、无音色）。

默认写到**程序运行目录**的 characters/（也就是 build_env/characters，和界面里看到的是同一份）；
要写到别处就设环境变量 WA2_USER_DIR。

    python tools/make_test_character.py
    python tools/make_test_character.py --user-dir D:\\somewhere
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if "--user-dir" in sys.argv:
    os.environ["WA2_USER_DIR"] = sys.argv[sys.argv.index("--user-dir") + 1]
if not os.environ.get("WA2_USER_DIR"):
    os.environ["WA2_USER_DIR"] = os.path.join(ROOT, "build_env")

sys.path.insert(0, os.path.join(ROOT, "src"))

import characters as chars  # noqa: E402

NAME = "测试角色"

DIRECTOR_CARD = """# 测试角色 · 导演卡

## 定位
用于验证多角色链路的最小角色。没有立绘、没有音色、没有世界书。

## 调度提示
- 说话短，一次一两句，不要长篇。
- 常用来当"第三个人"：不抢主角的戏，负责接话、提问、把话题往前推。
- 没有资源，出现时不要写动作描写（没有立绘可放）。

## 禁忌
- 不要替主角做决定。
- 不要重复别人刚说过的话。
"""

ACTOR_CARD = """# 测试角色 · 角色卡

你是「测试角色」，一个用来试功能的人。

## 说话方式
- 短句，口语，带一点点好奇。
- 不知道的事就直接问，不装懂。
- 一次只说一两句。

## 不要做的事
- 不要旁白式总结。
- 不要长篇大论。
"""

BASE_PROMPT = """你在扮演「测试角色」。

一个用来测试多角色功能的人：话不多，说话直白，遇到不懂的就问。
每次回复一到两句，不要写动作描写，不要旁白。
"""


def main():
    root = chars.root()
    path = os.path.join(root, NAME)
    if os.path.isdir(path):
        print("已存在：%s" % path)
    else:
        os.makedirs(path, exist_ok=True)
        for sub in ("prompt", "card", "memory", "portrait", "voice", "worldbook", "sessions"):
            os.makedirs(os.path.join(path, sub), exist_ok=True)
        print("已创建：%s" % path)

    io.open(os.path.join(path, "name.txt"), "w", encoding="utf-8").write(NAME)
    io.open(os.path.join(path, "prompt", "base.txt"), "w", encoding="utf-8").write(BASE_PROMPT)
    io.open(os.path.join(path, "card", "director.md"), "w", encoding="utf-8").write(DIRECTOR_CARD)
    io.open(os.path.join(path, "card", "actor.md"), "w", encoding="utf-8").write(ACTOR_CARD)

    mp = os.path.join(path, "model.json")
    if not os.path.isfile(mp):
        io.open(mp, "w", encoding="utf-8").write(json.dumps(
            {"chat": {"endpoint": "", "key": "", "model": "", "connected": False},
             "voice": {"endpoint": "", "key": "", "model": "", "connected": False}},
            ensure_ascii=False, indent=2))

    for f in ("name.txt", "prompt/base.txt", "card/director.md", "card/actor.md", "model.json"):
        print("  %-22s %s" % (f, os.path.isfile(os.path.join(path, f))))


if __name__ == "__main__":
    main()
