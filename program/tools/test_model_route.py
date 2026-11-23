# -*- coding: utf-8 -*-
"""模型路由自测：聊天的模型到底从哪来（请求 / 角色配置 / 全局）。

背景（2026-11-23）：用户报「配的是 OpenCode，DeepSeek 官网还在扣钱」。
查下来聊天请求从来没带 model/base_url，角色配置（characters/<角色>/model.json 的 chat）
从没接到聊天上；辅助调用（记忆书/关系阶段/选项/翻译）直接读全局 config.json。
这里钉住新的路由约定：

    1. 请求显式指定 > 角色配置 > 全局 config.json
    2. aux_cfg()（辅助调用）和聊天同一套路由
    3. 路由缺 key/缺端点时按「空」处理（不能拿半个配置去请求）
    4. OpenCode 这种网关要带 User-Agent + x-opencode-session，llm.headers 必须给
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import llm  # noqa: E402
import main  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + str(extra)) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


GLOBAL = {"base_url": "https://api.deepseek.com", "api_key": "sk-global",
          "model": "deepseek-flash"}
CHAR = {"base_url": "https://opencode.ai/inference/go/openai/v1",
        "api_key": "oc_sk_char", "model": "deepseek-v4.1-flash"}


def main_test():
    _old_cc = main.cloud_cfg
    _old_ch = main.char_chat_route
    try:
        print("[1] 只有全局配置")
        main.cloud_cfg = lambda: dict(GLOBAL)
        main.char_chat_route = lambda: {}
        r = main.chat_route()
        check("用全局模型", r["model"] == "deepseek-flash" and r["source"] == "全局设置", r)

        print("\n[2] 角色配置优先于全局")
        main.char_chat_route = lambda: dict(CHAR)
        r = main.chat_route()
        check("用角色模型", r["model"] == "deepseek-v4.1-flash", r)
        check("用角色端点", "opencode.ai" in r["base_url"], r)
        check("来源标成角色配置", r["source"] == "角色配置", r)
        check("aux_cfg 跟随角色配置", main.aux_cfg()["model"] == CHAR["model"])

        print("\n[3] 请求显式指定最优先")
        r = main.chat_route("http://127.0.0.1:11434", "qwen-local", "")
        check("请求的 base_url 生效", r["base_url"] == "http://127.0.0.1:11434", r)
        check("请求的 model 生效", r["model"] == "qwen-local", r)
        check("来源标成请求指定", r["source"] == "请求指定", r)

        print("\n[4] 角色配置不完整时不拿它")
        main.char_chat_route = lambda: {"base_url": "", "api_key": "", "model": ""}
        r = main.chat_route()
        check("回落全局", r["model"] == "deepseek-flash", r)

        print("\n[5] 全局也没有 → 空路由")
        main.cloud_cfg = lambda: {}
        main.char_chat_route = lambda: {}
        r = main.chat_route()
        check("没有模型/没 key", r["model"] == "" and r["api_key"] == "", r)
    finally:
        main.cloud_cfg = _old_cc
        main.char_chat_route = _old_ch

    print("\n[6] OpenCode 网关头")
    h = llm.headers({"base_url": CHAR["base_url"], "api_key": CHAR["api_key"], "model": CHAR["model"]})
    check("带 Authorization", h.get("Authorization") == "Bearer " + CHAR["api_key"], h)
    check("带 User-Agent（Cloudflare 会拦 python-urllib 默认 UA）",
          bool(h.get("User-Agent")) and "python" not in h.get("User-Agent", "").lower(), h)
    check("带 x-opencode-session", bool(h.get("x-opencode-session")), h)

    print("\n[7] 端点拼装")
    check("已带 /v1 → 只补 /chat/completions",
          llm.endpoint(CHAR["base_url"]).endswith("/v1/chat/completions"),
          llm.endpoint(CHAR["base_url"]))
    check("不带 /v1 → 补 /v1/chat/completions",
          llm.endpoint("https://api.deepseek.com").endswith("/v1/chat/completions"),
          llm.endpoint("https://api.deepseek.com"))

    print("\n" + ("全部通过" if not FAILS else "有 %d 项失败: %s" % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main_test())
