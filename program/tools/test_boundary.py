# -*- coding: utf-8 -*-
"""边界类自测（#11：核心 / 边界 / 降级三类里的「边界」，不联网、不开界面）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_boundary.py

验的是「极端输入不炸、并且行为仍可预期」：
  1. 长会话：100 轮 / 500 轮——token 估算、预算、原文起点、条目渲染都不炸
  2. 缓存满：条目超过 max_entries 只留最近的 N 条；progress 永远只留一条
  3. 素材缺失：立绘找不到 → 空串（上层走兜底）；带 `..` 的路径被拒（防穿越）
  4. 场景目录缺失 / 坏 JSON：catalog 返回空目录，match 返回 {}，不抛
  5. 空输入：空消息列表 / 空条目 / 空 scene 串都不抛
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))
# 不读真实角色目录：立绘/语音都按"没有素材"来测
os.environ["WA2_USER_DIR"] = tempfile.mkdtemp(prefix="wa2_boundary_user_")

_TMP = tempfile.mkdtemp(prefix="wa2_boundary_test_")
os.environ["WA2_CACHE_DIR"] = _TMP
os.environ["WA2_SESS_DIR"] = os.path.join(_TMP, "sessions")

import characters   # noqa: E402
import memory       # noqa: E402
import scene_cg     # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:200]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def rounds(n):
    """造 n 轮对话（一问一答）"""
    out = []
    for i in range(n):
        out.append({"role": "user", "content": "第 %d 轮：今天也想听你弹琴。" % i})
        out.append({"role": "assistant", "content": "「第 %d 轮的回答，说点很长的内容来占 token。」" % i})
    return out


def main():
    # ---------------------------------------------------------------- 1 长会话
    for n in (100, 500):
        msgs = rounds(n)
        est = memory.est_messages(msgs)
        check("%d 轮：token 估算为正数" % n, est > 0, est)
        check("%d 轮：估算随轮数线性增长" % n, est > memory.est_messages(rounds(n // 2)), est)
        trig, recent_b, entries_b = memory.budget(True)
        check("%d 轮：预算三档都是正数" % n, trig > 0 and recent_b > 0 and entries_b > 0,
              (trig, recent_b, entries_b))
        start = memory.recent_start(msgs, recent_b, 0)
        check("%d 轮：原文起点在合法区间" % n, 0 <= start <= len(msgs), (start, len(msgs)))
        check("%d 轮：起点对齐到 user 消息" % n,
              start == 0 or (msgs[start] or {}).get("role") == "user", (start, msgs[start]))
        check("%d 轮：换算出来的原文不超过预算（留一点余量）" % n,
              memory.est_messages(msgs[start:]) <= recent_b * 1.3,
              (memory.est_messages(msgs[start:]), recent_b))
        notes = [{"text": "第 %d 条记忆，写一句比较长的话来占 token。" % i,
                  "kind": "event", "from": [i, i + 1]} for i in range(60)]
        note = memory.render_note(notes, entries_b)
        check("%d 轮：条目渲染不超预算" % n, memory.est_text(note) <= entries_b * 1.3,
              (memory.est_text(note), entries_b))
        check("%d 轮：条目渲染有标题" % n, "【你记得的事" in note, note[:40])

    # ---------------------------------------------------------------- 2 缓存满
    old = [{"text": "旧 %d" % i, "kind": "event"} for i in range(30)]
    new = [{"text": "新 %d" % i, "kind": "event"} for i in range(30)]
    merged = memory.merge_entries(old, new, max_entries=5)
    check("超出上限只留 5 条", len(merged) == 5, len(merged))
    check("留下的是最近的（新的）", [e["text"] for e in merged] == ["新 25", "新 26", "新 27", "新 28", "新 29"],
          [e["text"] for e in merged])
    merged2 = memory.merge_entries(merged, [{"text": "新 29", "kind": "event"}], max_entries=5)
    check("重复条目被去重（长度不变）", len(merged2) == 5, len(merged2))
    prog = memory.merge_entries(
        [{"text": "进度 A", "kind": "progress"}],
        [{"text": "进度 B", "kind": "progress"}, {"text": "事件 C", "kind": "event"}], max_entries=5)
    kinds = [e.get("kind") for e in prog]
    check("progress 只留最新一条", kinds.count("progress") == 1, prog)
    check("progress 内容是最新的", [e["text"] for e in prog if e.get("kind") == "progress"] == ["进度 B"], prog)
    check("空条目合并不炸", memory.merge_entries([], []) == [])
    check("空文本条目被丢掉", memory.merge_entries([], [{"text": "   "}]) == [])

    # ---------------------------------------------------------------- 3 素材缺失
    empty = tempfile.mkdtemp(prefix="wa2_no_assets_")
    check("立绘找不到 → 空串（上层走兜底）",
          characters.sprite_file(empty, "礼服/认真.png") == "")
    check("路径穿越被拒（..）", characters.sprite_file(empty, "../secret.png") == "")
    check("绝对路径也被当成相对路径查（不会越界）",
          characters.sprite_file(empty, "C:/Windows/win.ini") == "")
    check("空路径返回空串", characters.sprite_file(empty, "") == "")
    check("sprite_index 对空目录不炸",
          isinstance(characters.sprite_index(empty), dict), characters.sprite_index(empty))

    # ---------------------------------------------------------------- 4 场景目录
    scene_cg.set_assets_dir(empty)
    cat = scene_cg.catalog()
    check("没有 cg/index.json → 空目录", cat["items"] == [] and cat["places"] == [], cat.keys())
    check("空目录里 match 返回 {}", scene_cg.match("教室（白天）", "") == {})
    check("空串 match 返回 {}", scene_cg.match("", "教室（白天）") == {})

    bad = tempfile.mkdtemp(prefix="wa2_bad_cg_")
    os.makedirs(os.path.join(bad, "cg"), exist_ok=True)
    with open(os.path.join(bad, "cg", "index.json"), "w", encoding="utf-8") as f:
        f.write("{ 这不是 JSON ")
    scene_cg.set_assets_dir(bad)
    check("坏 index.json → 空目录不炸", scene_cg.catalog()["items"] == [])
    check("坏目录里 match 返回 {}", scene_cg.match("教室（白天）", "") == {})

    good = tempfile.mkdtemp(prefix="wa2_good_cg_")
    os.makedirs(os.path.join(good, "cg"), exist_ok=True)
    with open(os.path.join(good, "cg", "index.json"), "w", encoding="utf-8") as f:
        json.dump({"items": [
            {"id": "教室（白天）", "file": "classroom_day.png", "place": "教室", "time": "白天"},
            {"id": "教室（黄昏）", "file": "classroom_dusk.png", "place": "教室", "time": "黄昏"},
        ]}, f, ensure_ascii=False)
    scene_cg.set_assets_dir(good)
    hit = scene_cg.match("教室（黄昏）", "")
    check("正常目录能匹配到具体的那张", hit.get("id") == "教室（黄昏）", hit)
    check("没提时间就沿用上一张的时段",
          scene_cg.match("教室", "教室（黄昏）").get("time") == "黄昏",
          scene_cg.match("教室", "教室（黄昏）"))
    check("完全不相关的地点 → 不硬匹配", scene_cg.match("火星表面（中午）", "") == {},
          scene_cg.match("火星表面（中午）", ""))
    check("地点别名也认（classroom → 教室）",
          scene_cg.match("classroom", "").get("id", "") != "", scene_cg.match("classroom", ""))

    # ---------------------------------------------------------------- 5 空输入
    check("空消息列表：估算 0", memory.est_messages([]) == 0)
    check("空消息列表：起点 0", memory.recent_start([], 100) == 0)
    check("空条目：渲染空串", memory.render_note([], 100) == "")
    check("parse_name 空串不炸",
          scene_cg.parse_name("") == {"place": "", "time": "", "weather": "", "state": []},
          scene_cg.parse_name(""))

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
