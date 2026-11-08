# -*- coding: utf-8 -*-
"""成人场面判定自测（H 场景要用到 H 参考音频）。

    cd program && .\\build_env\\venv\\Scripts\\python.exe tools\\test_hscene.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import hscene  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:200]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


# 用户真实日志里的三句 H 台词（都被模型标成了 calm）
H_LINES = [
    "んあぁっ、も、もう、むり…っ、いく、また、いく",
    "ひゃっ…！ た、たかい…っ、ん、この、体勢…っ",
    "はぁっ、あ、まだ…っ、もう、いってる、のに…っ",
]


def conv(replies, user="…"):
    msgs = []
    for r in replies:
        msgs.append({"role": "user", "content": user})
        msgs.append({"role": "assistant", "content": r})
    return msgs


def main():
    print("[1] 真实 H 台词要判成成人场面")
    for line in H_LINES:
        s, strong = hscene.score(line)
        check("单句有强特征：%s" % line[:12], strong >= 1, (s, strong))
    check("整段 H 对话判定为真", hscene.is_intimate(conv(H_LINES)), conv(H_LINES))

    print("[2] 普通场景不能误判")
    neg = {
        "日常": [{"role": "user", "content": "今天吃什么"},
                 {"role": "assistant", "content": "随便，你定"}],
        "亲密但不 H": [{"role": "user", "content": "抱一下可以吗"},
                       {"role": "assistant", "content": "…笨蛋，快点"}],
        "吵架": [{"role": "user", "content": "你到底想怎样"},
                 {"role": "assistant", "content": "别管我"}],
        "聊天气": [{"role": "user", "content": "今天好冷"},
                   {"role": "assistant", "content": "嗯，多穿点"}],
        "空对话": [],
        "只有一条": [{"role": "assistant", "content": "嗯"}],
    }
    for k, v in neg.items():
        check("%s 不误判" % k, not hscene.is_intimate(v), v)

    print("[3] 中文译文也算信号")
    zh_only = [{"role": "user", "content": "还要"},
               {"role": "assistant", "content": "嗯…", "zh": "不行了…里面太深了…要去了"}]
    check("只看中文也能判出来", hscene.is_intimate(zh_only), zh_only)

    print("[4] 提示词那句要有")
    h = hscene.hint()
    check("提示使用 H", "H" in h)
    check("明确说不要 calm/soft", "calm" in h and "soft" in h)


    print("[5] 场面状态跨请求（载入存档后的自动重播要用）")
    hscene.set_last(True)
    check("set_last(True) 读得到", hscene.last_scene() is True)
    # 有迟滞：一次判否不会立刻退出，要连续 EXIT_STREAK（5）次
    hscene.set_last(False)
    check("一次判否仍在场面里（迟滞）", hscene.last_scene() is True)
    for _ in range(hscene.EXIT_STREAK - 2):
        hscene.set_last(False)
    check("差一次时仍在场面里", hscene.last_scene() is True)
    hscene.set_last(False)
    check("连续五次判否才退出", hscene.last_scene() is False)


    print("[6] 迟滞：H 场面里的短句不能把语气拽回平静")
    hscene.reset()
    seq = [(True, True)] + [(False, True)] * (hscene.EXIT_STREAK - 1) + [(False, False)]
    for i, (judge, want) in enumerate(seq, 1):
        got = hscene.set_last(judge)
        check("第 %d 次判定 %s → 生效 %s" % (i, judge, got), got == want, (judge, got, want))
    hscene.reset()
    check("reset 后是 None", hscene.last_scene() is None)
    check("EXIT_STREAK 是 5", getattr(hscene, "EXIT_STREAK", 0) == 5, getattr(hscene, "EXIT_STREAK", None))

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
