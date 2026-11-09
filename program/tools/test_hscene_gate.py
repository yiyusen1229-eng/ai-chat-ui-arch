# -*- coding: utf-8 -*-
"""验证场面判定：日常对话不该判成特殊，真正的特殊场面仍要判出来。"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build_env"))

import hscene

DAILY = [
    [{"role": "assistant", "content": "……まだ、そんなこと言ってるの", "zh": "……你还在说这种话啊"}],
    [{"role": "assistant", "content": "だめだよ、そんなの", "zh": "不行的啦，那种事"}],
    [{"role": "assistant", "content": "そこ、座ってていいから", "zh": "你在那儿坐着就好"}],
    [{"role": "assistant", "content": "もっと練習しないとね", "zh": "得多练练才行呢"}],
    [{"role": "assistant", "content": "……べつに。まだ時間あるし", "zh": "……没什么。还有时间"}],
    [{"role": "user", "content": "今天也一起回去吧", "zh": "今天也一起回去吧"},
     {"role": "assistant", "content": "……好きにすれば", "zh": "……随你便"}],
]

SPECIAL = [
    [{"role": "assistant", "content": "んっ…だめ、そこ…っ", "zh": "嗯…不行，那里…"}],
    [{"role": "assistant", "content": "はぁっ…まだ、いってる…っ", "zh": "哈…还在去…"}],
    [{"role": "user", "content": "腰を上げて", "zh": "抬起腰"},
     {"role": "assistant", "content": "むり…っ、うごけない…", "zh": "不行…动不了…"}],
]

bad = 0
print("— 日常对话（应判否）—")
for msgs in DAILY:
    r = hscene.is_intimate(msgs)
    tag = "ok " if not r else "误判"
    if r:
        bad += 1
    print("  %s %s" % (tag, msgs[-1]["content"][:24]))

print("— 特殊场面（应判是）—")
for msgs in SPECIAL:
    r = hscene.is_intimate(msgs)
    tag = "ok " if r else "漏判"
    if not r:
        bad += 1
    print("  %s %s" % (tag, msgs[-1]["content"][:24]))

print("问题数:", bad)
sys.exit(1 if bad else 0)
