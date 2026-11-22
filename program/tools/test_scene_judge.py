# -*- coding: utf-8 -*-
"""场景判定器（_judge_scene）的自测：不联网、不花 token。

钉住用户提的那几条「大致匹配」规则：
  1. 标签对不上时，让模型从**完整场景名**里挑最接近的（卫生间洗漱 → 自己的公寓（洗手台））；
  2. 模型回答「不变」→ 不换场景；
  3. 模型胡答（不在素材里）→ 认不出就不换，不能抛异常；
  4. 会"先思考"的模型必须关思考，否则 max_tokens 全被 reasoning 吃掉、content 为空
     （线上真的踩过：`bad_response: content 为空`）。

跑法（在 program/ 下）：
    build_env/venv/Scripts/python.exe tools/test_scene_judge.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "build_env"))
sys.path.insert(0, os.path.join(HERE, "build_env", "src"))

import api_chat    # noqa: E402
import scene_cg    # noqa: E402

bad = 0
CALLS = []


def check(ok, msg):
    global bad
    if not ok:
        bad += 1
        print("  FAIL %s" % msg)
    else:
        print("  ok   %s" % msg)


class _R:
    def __init__(self, text):
        self.text = text


def fake_generate(payload, cfg, timeout=30, fallback_cfg=None):
    CALLS.append(payload)
    return _R(fake_generate.answer)


def main():
    global bad
    tmp = tempfile.mkdtemp(prefix="wa2_sj_")
    assets = os.path.join(tmp, "assets")
    os.makedirs(os.path.join(assets, "cg"))
    items = [
        {"id": "和纱家别墅（客厅）（晚上）", "file": "a.webp", "place": "和纱家别墅",
         "kind": "place", "time": "晚上", "weather": "", "state": ["客厅"]},
        {"id": "自己的公寓（洗手台）", "file": "b.webp", "place": "自己的公寓",
         "kind": "place", "time": "", "weather": "", "state": ["洗手台"]},
        {"id": "回家的路（晚上）（路灯亮着）", "file": "c.webp", "place": "回家的路",
         "kind": "place", "time": "晚上", "weather": "", "state": ["路灯亮着"]},
    ]
    with open(os.path.join(assets, "cg", "index.json"), "w", encoding="utf-8") as f:
        json.dump({"version": 1, "generated": "", "items": items}, f, ensure_ascii=False)
    scene_cg.set_assets_dir(assets)
    scene_cg._CACHE["data"] = None

    cfg = {"api_key": "k", "base_url": "https://opencode.ai/inference/go/openai/v1",
           "model": "deepseek-v4.1-flash"}
    logs = []
    log = lambda m, *a: logs.append(m)   # noqa: E731

    saved = api_chat.llm.generate
    api_chat.llm.generate = fake_generate
    try:
        print("[1] 大致匹配：卫生间洗漱 → 洗手台")
        fake_generate.answer = "自己的公寓（洗手台）"
        hit, ans = api_chat._judge_scene("和纱家别墅（客厅）（晚上）",
                                         "我在和纱家别墅的卫生间里洗漱。", "", "", cfg, log)
        check(hit.get("id") == "自己的公寓（洗手台）", "命中 %r（回答 %r）" % (hit.get("id"), ans))

        print("[2] 回答「不变」→ 不换")
        fake_generate.answer = "不变"
        hit, ans = api_chat._judge_scene("和纱家别墅（客厅）（晚上）", "今天好累啊", "", "", cfg, log)
        check(hit == {} and ans == "不变", "不换（回答 %r）" % (ans,))

        print("[3] 模型胡答 → 认不出就不换，不抛异常")
        fake_generate.answer = "火星环形山"
        hit, ans = api_chat._judge_scene("和纱家别墅（客厅）（晚上）", "在火星上", "", "", cfg, log)
        check(hit == {}, "没匹配上（回答 %r）" % (ans,))

        print("[4] 会思考的模型要关思考（否则 content 空）")
        fake_generate.answer = "回家的路"
        CALLS.clear()
        api_chat._judge_scene("学校大门口（黄昏）", "放学了，我正在回家的路上。", "", "", cfg, log)
        p = CALLS[-1] if CALLS else {}
        check(p.get("thinking") == {"type": "disabled"} and p.get("reasoning_effort") == "none",
              "payload 关了思考")
        check(p.get("max_tokens") and p["max_tokens"] >= 32, "max_tokens 够（%r）" % p.get("max_tokens"))

        print("[5] 给模型的是完整场景名（带房间细节）")
        sys_txt = ((p.get("messages") or [{}])[0].get("content") or "")
        check("自己的公寓（洗手台）" in sys_txt, "system 里带了房间细节")
        check("卫生间" in sys_txt or "差不多" in sys_txt or "最接近" in sys_txt, "system 里说明了允许近似匹配")
    finally:
        api_chat.llm.generate = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n问题数: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
