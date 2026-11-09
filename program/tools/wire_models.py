# -*- coding: utf-8 -*-
"""把导演和测试角色接上模型：用系统设置里那套 DeepSeek 配置，实测连通后写回。

步骤：
 1. 读全局配置（config.json 的 models.chat）拿端点 / key / 模型名
 2. 临时切到「测试角色」→ 调 char_model.connect('chat') 真连一次 → 切回「和纱」
 3. 在「和纱」上调 char_model.connect('director') 真连一次
 4. 两个目录（build_env / src）都写一份
"""
import json
import os
import sys

PROGRAM = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV = os.path.join(PROGRAM, "build_env")
SRC = os.path.join(PROGRAM, "src")
sys.path.insert(0, ENV)

import char_model            # noqa: E402
import characters as chars   # noqa: E402


def global_chat():
    p = os.path.join(ENV, "config.json")
    d = json.loads(open(p, encoding="utf-8").read())
    return d["models"]["chat"]


def active():
    return chars.active_id()


def switch(cid):
    r = chars.switch(cid)
    return r


def main():
    g = global_chat()
    endpoint = g.get("endpoint") or ""
    key = g.get("key") or ""
    model = g.get("model") or ""
    print("全局配置：", endpoint, "模型", model, "key", key[:6] + "…")

    orig = active()
    print("当前角色：", orig)

    # ---------- 测试角色 ----------
    switch("测试角色")
    char_model.save_fields("chat", endpoint, key)
    r = char_model.connect("chat")
    print("测试角色 连接：", {k: r.get(k) for k in ("ok", "model", "error")})

    # ---------- 导演（写在当前角色 和纱 身上） ----------
    switch("和纱")
    char_model.save_fields("director", endpoint, key)
    r2 = char_model.connect("director")
    print("导演 连接：", {k: r2.get(k) for k in ("ok", "model", "error")})

    # 切回原来的角色
    if orig and orig != active():
        switch(orig)

    # ---------- 复制到 src 一份 ----------
    for name in ("测试角色", "和纱"):
        s = os.path.join(ENV, "characters", name, "model.json")
        d = os.path.join(SRC, "characters", name, "model.json")
        if os.path.isfile(s):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            open(d, "w", encoding="utf-8").write(open(s, encoding="utf-8").read())
            print("已同步：", name)


if __name__ == "__main__":
    main()
