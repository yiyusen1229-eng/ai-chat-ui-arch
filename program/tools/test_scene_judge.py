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

        # ---- 2026-11-22：用户报「还在教室里，CG 突然跳到公寓」----
        # 那次是「模型没给 <scene> → 按动作里的移动动词模糊推断」这条路误判的：
        # 动作里一个「抽出来」（_MOVE_WORDS 有「出来」）拿整段文字去匹配，
        # 以 0.17 的低分撞上「自己的公寓（房间）」，然后就直接换了图。
        print("[6] 按文字推断要过三道门（地点词 / 地点相似度 / 真的提到了）")
        prev = "自己的公寓（洗手台）"
        check(api_chat._infer_scene_by_text("（把她的手从桌子下面抽出来）", prev) == {},
              "只有「出来」不算移动换场")
        check(api_chat._infer_scene_by_text("嗯 （想到了什么，笑出来）", prev) == {},
              "「想到了/笑出来」也不算")
        check(not api_chat._scene_score_ok({"place_score": 0.17}), "低地点相似度不换")
        check(api_chat._scene_score_ok({"place_score": 0.72}), "正常地点相似度可以换")
        check(api_chat._scene_score_ok({"place_score": 0.72, "score": -0.16}) is True,
              "只认 place_score，不看变体分 score")

        _real_match = api_chat.scene_cg.match
        try:
            # 模糊匹配撞到一个句子里根本没提到的地点 → 不算
            api_chat.scene_cg.match = lambda *a, **k: {
                "id": "和纱家别墅（客厅）（晚上）", "place": "和纱家别墅",
                "place_score": 0.9, "score": 0.4}
            _r = api_chat._infer_scene_by_text("（走进教室，坐回座位）", prev) or {}
            check(_r.get("id") is None, "匹配到句子里没提的地点 → 不换（%r）" % (_r,))
            # 句子里真提到了 → 才认
            hit = api_chat._infer_scene_by_text("（走出教室，回了和纱家别墅）", prev)
            check(hit.get("id") == "和纱家别墅（客厅）（晚上）",
                  "句子里真提到了才换（%r）" % (hit,))
        finally:
            api_chat.scene_cg.match = _real_match
        check(api_chat._place_mentioned("我们回家吧", "和纱家别墅"), "「家里」这种近义词也算提到了")
        check(not api_chat._place_mentioned("（把手抽出来）", "自己的公寓"),
              "「抽出来」不算提到 公寓")
    finally:
        api_chat.llm.generate = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n问题数: %d" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
