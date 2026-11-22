# -*- coding: utf-8 -*-
"""情绪迟滞（emotion.py）的自测：不联网、不碰数据。

为什么单独测：用户 2026-11-22 反馈「上一句平静、下一句突然哭腔、再下一句又平静」。
迟滞规则很容易被后来的人「顺手简化」回「模型给什么用什么」，这个测试盯着几条硬规则：
  · 单句离群要压回基线；同一个新情绪连续两次才切；
  · 不许卡死（连续 MAX_HOLD 句不同就认）；
  · 问句放行但不改基线；H 直接接管、退出立刻切回；
  · 没线索 / source=default 时沿用基线，别回落 calm。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_emotion.py
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import emotion as E  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:200]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def seq(*pairs):
    """按顺序喂 [(情绪, source), ...]，返回实际生效的情绪序列"""
    out = []
    for emo, src in pairs:
        out.append(E.settle(emo, source=src))
    return out


def main():
    # 1) 首句 / 播种
    E.reset()
    check("首句没有基线 → 直接用传入的情绪", E.settle("撒娇") == "撒娇", E.status())
    E.reset()
    check("prev_hint 播种：首句用上一句的情绪",
          E.settle("哭腔", prev_hint="calm") == "calm", E.status())
    check("播种那句本身算一次候选 → 再来一次哭腔才切",
          E.settle("哭腔") == "哭腔", E.status())

    # 2) 单句离群压掉
    E.reset()
    got = seq(("calm", "tag"), ("哭腔", "tag"), ("calm", "tag"))
    check("平静→哭腔→平静：中间的哭腔被压掉", got == ["calm", "calm", "calm"], got)

    # 3) 连续两次才切；切回同理
    E.reset()
    got = seq(("calm", "tag"), ("哭腔", "tag"), ("哭腔", "tag"),
              ("calm", "tag"), ("calm", "tag"))
    check("连续两次哭腔 → 第二句切过去", got[:3] == ["calm", "calm", "哭腔"], got)
    check("连续两次 calm → 第二句切回来", got[3:] == ["哭腔", "calm"], got)

    # 4) 同情绪确认会打断候选（calm 哭腔 calm 哭腔 不该切）
    E.reset()
    got = seq(("calm", "tag"), ("哭腔", "tag"), ("calm", "tag"), ("哭腔", "tag"))
    check("候选被打断就不切", got == ["calm", "calm", "calm", "calm"], got)

    # 5) 问句：放行但不改基线
    E.reset()
    got = seq(("撒娇", "tag"), ("问句", "tag"), ("撒娇", "tag"))
    check("问句直接放行", got[1] == "问句", got)
    check("问句不改基线（下一句回到原情绪）", got == ["撒娇", "问句", "撒娇"], got)

    # 6) 没线索：沿用基线，别回落 calm
    E.reset()
    E.settle("撒娇")
    got = [E.settle("calm", source="default"), E.settle("", source="default")]
    check("没写标签（default）→ 沿用撒娇", got == ["撒娇", "撒娇"], got)

    # 7) H：场面模式直接接管，退出立刻切回
    E.reset()
    check("H 立刻接管", E.settle("H") == "H", E.status())
    check("从 H 出来立刻切回（不拖一句）", E.settle("calm") == "calm", E.status())
    E.reset()
    check("scene_h=True 时即使标 calm 也压成 H",
          E.settle("calm", scene_h=True) == "H", E.status())

    # 8) 抖动稳住 vs 不许卡死
    E.reset()
    got = seq(("撒娇", "tag"), ("calm", "tag"), ("撒娇", "tag"), ("calm", "tag"))
    check("一句话一变的抖动 → 稳住基线，不跟着抖",
          got == ["撒娇", "撒娇", "撒娇", "撒娇"], got)
    E.reset()
    got = seq(("撒娇", "tag"), ("calm", "tag"), ("soft", "tag"), ("毒舌", "tag"))
    check("连续 3 句都不是基线 → 第 3 句认了（防卡死）",
          got == ["撒娇", "撒娇", "撒娇", "毒舌"], got)

    # 9) reset / status
    E.reset()
    E.settle("毒舌")
    E.reset()
    check("reset 后基线为空", E.last() == "", E.status())
    check("reset 计数记着（日志/调试能看到）", E.status().get("reset") >= 1, E.status())
    st = E.status()
    check("status 有 switched / kept 计数",
          "switched" in st and "kept" in st, st)

    # 10) 单句离群不会把计数算成切换
    E.reset()
    E.settle("calm")
    _before = E.status().get("switched", 0)
    E.settle("哭腔")
    check("被压回的离群不算切换", E.status().get("switched") == _before, E.status())
    check("被压回的离群记在 kept 里", E.status().get("kept") >= 1, E.status())

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
