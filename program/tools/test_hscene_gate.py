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
    # 实测漏判过的一句：露骨词只在中文那侧（日语原文是拟声）
    [{"role": "user", "content": "嗯？（把手指伸入她的阴道浅浅抽插）"},
     {"role": "assistant", "content": "…ん、ぁ…っ、や、やだ…っ", "zh": "…嗯、啊…不、不要…"}],
    # 露骨词只在日语那侧（中文字幕被洗白）
    [{"role": "assistant", "content": "……", "zh": "……",
      "ja": "…ん、ぁ…っ、中出し、して…っ、精液、いっぱい…"}],
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

# ---------------- 逐句判定只认露骨词 ----------------
# score() 的强特征词为了"看上下文"故意放宽（腰/胸/感じ/だめ/むり…日常也有），
# 拿它做逐句判定会把普通台词判成 H —— 实测「正常语音变 H」就是这么来的。
print("— 逐句判定（is_explicit 只认露骨词）—")
EXPLICIT_TRUE = ["（把她按在墙上，手指探进她的阴道）", "嗯…高潮了…", "…ん、ぁ…っ、中出し、して…"]
EXPLICIT_FALSE = ["腰有点酸，坐太久了。", "今天课上老师的板书好难抄。", "胸口的纽扣掉了。",
                  "放学要不要一起去车站？", "だめ、むり、もう無理…"]  # 最后一句只是抱怨
for t in EXPLICIT_TRUE:
    if not hscene.is_explicit(t):
        print("  漏判: %s" % t); bad += 1
for t in EXPLICIT_FALSE:
    if hscene.is_explicit(t):
        print("  误判: %s" % t); bad += 1
print("  露骨 %d 句 / 正常 %d 句" % (len(EXPLICIT_TRUE), len(EXPLICIT_FALSE)))

# ---------------- 场面对配音的影响 ----------------
# 多角色的台词是导演/演员给的（calm / 毒舌 / 撒娇…），不会自己带 H。
# 以前只有"回想重放"会强制 H，于是同一条台词实时是平常语气、回想里才是 H 语气。
print("— 场面 → 情绪参考（effective_emotion）—")
hscene.reset()
if hscene.effective_emotion("calm") != "calm":
    print("  误判：还没判定场面就改了标签"); bad += 1
hscene.set_last(True)
for emo, want in (("calm", "H"), ("soft", "H"), ("毒舌", "H"), ("H", "H")):
    got = hscene.effective_emotion(emo)
    tag = "ok " if got == want else "错"
    if got != want:
        bad += 1
    print("  %s %s → %s" % (tag, emo, got))
for _ in range(hscene.EXIT_STREAK):     # 迟滞：要连续几次判否才真的退出
    hscene.set_last(False)
if hscene.effective_emotion("calm") != "calm":
    print("  错：退出成人场面后还按 H 念"); bad += 1
else:
    print("  ok  退出成人场面后 calm 保持 calm")

# 队列要把场面标记带下去（否则前端拿不到、预合成也不知道）
import playqueue
playqueue.clear()
playqueue.push([{"speaker": "和纱", "text": "……", "type": "say", "ja": "……", "hscene": True},
                {"speaker": "", "text": "雪停了。", "type": "narr"}])
head = playqueue.peek() or {}
playqueue.clear()
if not head.get("hscene"):
    print("  错：队列丢掉了 hscene 标记"); bad += 1
else:
    print("  ok  队列带着 hscene 标记")

print("问题数:", bad)
sys.exit(1 if bad else 0)
