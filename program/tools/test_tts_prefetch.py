# -*- coding: utf-8 -*-
"""预合成的自测（不联网、不起语音服务、不花 token）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_tts_prefetch.py

验的是「下一句的音频会不会在玩家读这一句的时候就准备好」这条链路：
  1. 开关：安全模式 / WA2_TTS_PREFETCH=0 / 语音没就绪 → 一条都不排
  2. 只排接下来 n 条，旁白和玩家（没有日语）不排
  3. 传给合成函数的参数和真播时一致（文本=日语原文、lang=ja、情绪、角色 id）
  4. 同样的句子不重复排；清空之后还能重排
  5. 合成失败不影响后面的任务，也不往外抛
  6. 队列有上限，不会越攒越多
  7. playqueue.pending 只看不取（取走的是 pop）
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import playqueue      # noqa: E402
import tts_prefetch   # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    calls = []
    state = {"ready": True, "fail": False}

    def fake_synth(text, lang="zh", timeout=0, emo="neutral", char=""):
        calls.append({"text": text, "lang": lang, "emo": emo, "char": char})
        if state["fail"]:
            raise RuntimeError("合成炸了")
        return b"RIFF"

    def fake_ready():
        return state["ready"]

    lines = [
        {"speaker": "雪菜", "cid": "雪菜", "type": "say", "text": "字幕一",
         "ja": "……その一。", "emotion": "问句"},
        {"speaker": "和纱", "cid": "和纱", "type": "say", "text": "字幕二",
         "ja": "……その二。", "emotion": "毒舌"},
        {"speaker": "", "type": "narr", "text": "旁白", "ja": ""},
        {"speaker": "你", "type": "say", "text": "玩家", "ja": ""},
        {"speaker": "雪菜", "cid": "雪菜", "type": "say", "text": "字幕三",
         "ja": "……その三。", "emotion": "calm"},
    ]

    print("[1] 开关")
    tts_prefetch.configure(enabled=True, synth=fake_synth, ready=fake_ready)
    check("默认开着", tts_prefetch.enabled() is True)
    os.environ["WA2_TTS_PREFETCH"] = "0"
    check("环境变量能关", tts_prefetch.enabled() is False)
    check("关了就不排", tts_prefetch.kick(lines, 2) == 0)
    os.environ.pop("WA2_TTS_PREFETCH", None)
    tts_prefetch.configure(enabled=False, synth=fake_synth, ready=fake_ready)
    check("configure 也能关", tts_prefetch.kick(lines, 2) == 0)
    tts_prefetch.configure(enabled=True, synth=fake_synth, ready=fake_ready)
    state["ready"] = False
    check("语音没就绪不排", tts_prefetch.kick(lines, 2) == 0)
    state["ready"] = True

    print("\n[2] 只排接下来两条，旁白/玩家跳过")
    calls.clear()
    added = tts_prefetch.kick(lines, 2)
    tts_prefetch.drain(5)
    check("排了两条", added == 2, added)
    check("只调了两次", len(calls) == 2, calls)
    check("顺序对", [c["text"] for c in calls] == ["……その一。", "……その二。"], calls)
    check("一口气把剩下的也排（n=5）时旁白/玩家被跳过",
          tts_prefetch.kick(lines, 5) == 1, calls)   # 只剩第三条日语
    tts_prefetch.drain(5)
    check("跳过的那些没有合成", all(c["text"].startswith("……") for c in calls), calls)

    print("\n[3] 参数和真播一致")
    first = calls[0]
    check("文本是日语原文", first["text"] == "……その一。", first)
    check("lang=ja", first["lang"] == "ja", first)
    check("情绪带上", first["emo"] == "问句", first)
    check("角色 id 带上", first["char"] == "雪菜", first)

    print("\n[4] 去重与清空")
    calls.clear()
    check("同样的句子不再排", tts_prefetch.kick(lines, 3) == 0)
    tts_prefetch.drain(2)
    check("没有重复合成", calls == [], calls)
    tts_prefetch.clear()
    check("清空后能重排", tts_prefetch.kick(lines, 1) == 1)
    tts_prefetch.drain(5)

    print("\n[5] 失败不影响别人")
    calls.clear()
    tts_prefetch.clear()
    state["fail"] = True
    added = tts_prefetch.kick(lines, 2)
    tts_prefetch.drain(5)
    check("照排不误", added == 2, added)
    check("两次都试过了", len(calls) == 2, calls)
    check("失败记进统计", tts_prefetch.stats()["last"]["ok"] is False,
          tts_prefetch.stats()["last"])
    state["fail"] = False
    tts_prefetch.clear()
    tts_prefetch.kick(lines, 1)
    tts_prefetch.drain(5)
    check("失败之后还能正常做", tts_prefetch.stats()["last"]["ok"] is True,
          tts_prefetch.stats()["last"])

    print("\n[6] 队列有上限")
    tts_prefetch.clear()
    many = [{"speaker": "雪菜", "cid": "雪菜", "type": "say", "text": "t%d" % i,
             "ja": "……%d。" % i, "emotion": "calm"} for i in range(20)]
    # ready 拉满但让合成卡住，好观察队列长度
    hold = {"on": True}
    real_synth = fake_synth

    def slow_synth(text, lang="zh", timeout=0, emo="neutral", char=""):
        while hold["on"]:
            time.sleep(0.01)
        return real_synth(text, lang, timeout, emo, char)

    tts_prefetch.configure(enabled=True, synth=slow_synth, ready=fake_ready)
    total = 0
    for _ in range(4):
        total += tts_prefetch.kick(many, 5)
    check("攒的条数不超过上限", tts_prefetch.stats()["pending"] <= tts_prefetch.QUEUE_MAX,
          tts_prefetch.stats())
    hold["on"] = False
    tts_prefetch.drain(10)
    check("跑完之后队列清空", tts_prefetch.stats()["pending"] == 0)

    print("\n[7] playqueue.pending 只看不取")
    playqueue.clear()
    playqueue.push([{"speaker": "雪菜", "cid": "雪菜", "text": "一", "type": "say", "ja": "……一。"},
                    {"speaker": "和纱", "cid": "和纱", "text": "二", "type": "say", "ja": "……二。"},
                    {"speaker": "和纱", "cid": "和纱", "text": "三", "type": "say", "ja": "……三。"}])
    p = playqueue.pending(2)
    check("拿到前两条", [x["text"] for x in p] == ["一", "二"], p)
    check("队列没被取走", playqueue.snapshot()["pending"] == 3, playqueue.snapshot())
    check("pending 带 cid", p[0].get("cid") == "雪菜", p[0])
    playqueue.clear()

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
