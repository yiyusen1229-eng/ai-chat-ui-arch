# -*- coding: utf-8 -*-
"""场景 CG 匹配的自测（不联网、不花钱）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_scene_cg.py

验的是几条「挑错了就穿帮」的规矩：
  1. 文件名标签要按括号拆对：时间 / 天气 / 地点细节各归各位
  2. 说全了地点+时间 → 挑中唯一那张
  3. 只说地点 → 别挑「圣诞节」「下雪」「空无一人」这种特例
  4. 没说时间 → 沿用当前背景的时段（晚上出门还是晚上）
  5. 说不清的地点 → 宁可不换图（返回空），也不能乱挑一张
  6. 天空图只在明确提到天空/云/霞时才出现
  7. 提示词里的地点表来自素材目录，不是写死的枚举
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import scene_cg  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def main():
    assets = os.environ.get("WA2_ASSETS_DIR") or os.path.join(PROGRAM, "res", "assets")
    scene_cg.set_assets_dir(assets)
    cat = scene_cg.catalog()

    print("[1] 目录")
    check("读到了 index.json", bool(cat["items"]), "items=%d" % len(cat["items"]))
    if not cat["items"]:
        print("\n素材没导入：先跑 tools/import_cg.py --src <场景原图目录>")
        return 1
    check("地点表非空", len(cat["places"]) >= 20, "places=%d" % len(cat["places"]))
    check("天空图单独归类", len(cat["sky"]) >= 1, "sky=%d" % len(cat["sky"]))

    print("\n[2] 文件名标签解析")
    t = scene_cg.parse_name("和纱家别墅（客厅）（黑夜）")
    check("地点", t["place"] == "和纱家别墅", repr(t))
    check("时间", t["time"] == "黑夜", repr(t))
    check("细节", t["state"] == ["客厅"], repr(t))
    t = scene_cg.parse_name("商业区的大路口（黄昏）（圣诞节）")
    check("天气/季节", t["weather"] == "圣诞节" and t["time"] == "黄昏", repr(t))
    t = scene_cg.parse_name("ktv包厢内")
    check("没有括号", t["place"] == "ktv包厢内" and not t["time"] and not t["state"], repr(t))

    print("\n[3] 地点+时间 → 唯一那张")
    exact = [
        ("咖啡厅（黄昏）", "咖啡厅（黄昏）"),
        ("大学图书馆（黄昏）", "大学图书馆（黄昏）"),
        ("教室（黄昏）", "教室（黄昏）（空无一人）"),
        ("和纱家别墅的客厅", "和纱家别墅（客厅）（白天）"),
        ("雪菜家（客厅）", "雪菜家（客厅）（白天）"),
        ("商业区的大路口（圣诞节）", "商业区的大路口（白天）（圣诞节）"),
        ("温泉旅馆（房间内）（白天）", "温泉旅馆（房间内）（白天）（冬天）"),
        ("晚上在街上", "街上（晚上）（人行道）"),
    ]
    for q, want in exact:
        got = scene_cg.match(q).get("id", "")
        check("%s → %s" % (q, want), got == want, "拿到 %r" % got)

    print("\n[4] 只说地点 → 不挑特例")
    plain = [
        ("咖啡厅", "咖啡厅（白天）"),
        ("教室", "教室（白天）（人来人往）"),
        ("学校", "学校大门口（白天）（人来人往）"),
        ("音乐室", "第二音乐室（和纱专用）（白天）"),
    ]
    for q, want_place_or_id in plain:
        hit = scene_cg.match(q)
        got = hit.get("id", "")
        check("%s → %s" % (q, want_place_or_id), got == want_place_or_id, "拿到 %r" % got)
    for q in ("咖啡厅", "教室", "走廊", "温泉旅馆"):
        hit = scene_cg.match(q)
        check("%s 不带特例标签" % q,
              not hit.get("weather") and "圣诞节" not in hit.get("id", ""), repr(hit))

    print("\n[5] 时段连续")
    hit = scene_cg.match("街上", "咖啡厅（晚上）")
    check("晚上→街上仍是晚上", hit.get("time") == "晚上", repr(hit))
    hit = scene_cg.match("教室", "教室（黄昏）（空无一人）")
    check("同地点没说时间→保持黄昏", hit.get("id") == "教室（黄昏）（空无一人）", repr(hit))
    hit = scene_cg.match("咖啡厅", "街上（晚上）（人行道）")
    check("街道→咖啡厅沿用晚上", hit.get("id") == "咖啡厅（晚上）", repr(hit))

    print("\n[6] 说不清 → 不换图")
    for q in ("完全不存在的地方", "asdfghjkl", "……"):
        check("%r 不乱挑" % q, not scene_cg.match(q))

    print("\n[7] 天空图")
    hit = scene_cg.match("夜晚天空")
    check("明确说夜晚天空", hit.get("id") == "夜晚天空", repr(hit))
    hit = scene_cg.match("大学图书馆（黄昏）")
    check("正常场景不会被天空图抢走", hit.get("place") == "大学图书馆", repr(hit))

    print("\n[8] 旧枚举兼容")
    for old, want in (("classroom", "教室"), ("cafe", "咖啡厅"), ("music", "第二音乐室")):
        hit = scene_cg.match(old)
        check("%s → %s" % (old, want), hit.get("place") == want, repr(hit))

    print("\n[8.5] 单字「夜」也要认成晚上（2026-11-06 实测模型会写（夜））")
    hit = scene_cg.match("和纱家别墅（夜）")
    check("（夜）→ 晚上，不是白天", hit.get("time") == "晚上", repr(hit))
    hit = scene_cg.match("公园（天黑）")
    check("（天黑）→ 晚上", hit.get("time") == "晚上", repr(hit))
    hit = scene_cg.match("咖啡厅（夜晚）")
    check("（夜晚）→ 晚上", hit.get("id") == "咖啡厅（晚上）", repr(hit))

    print("\n[8.8] 这一轮说了时段 → 按它挑变体（2026-11-22：「白天去学校」接成晚上校门口）")
    check("time_hint 早上去学校", scene_cg.time_hint("早上去学校") == "白天",
          scene_cg.time_hint("早上去学校"))
    check("time_hint 天黑了", scene_cg.time_hint("天黑了才回家") == "晚上",
          scene_cg.time_hint("天黑了才回家"))
    check("time_hint 取最靠后的提及",
          scene_cg.time_hint("昨天夜里下的雪，今天白天化了") == "白天",
          scene_cg.time_hint("昨天夜里下的雪，今天白天化 了"))
    m_night = scene_cg.match("学校大门口", "站台（晚上）") or {}
    check("没有时段线索 → 沿用上一张（晚上）", "晚上" in (m_night.get("id") or ""),
          m_night.get("id"))
    m_day = scene_cg.match("学校大门口", "站台（晚上）", time_hint="白天") or {}
    check("有时段线索=白天 → 挑白天的校门口", "白天" in (m_day.get("id") or ""),
          m_day.get("id"))

    print("\n[8.9] 天气 / 地点细节线索（2026-11-22：雪停了还挂着雪景）")
    check("下雪 → 白雪皑皑", scene_cg.weather_hint("外面下雪了") == "白雪皑皑",
          scene_cg.weather_hint("外面下雪了"))
    check("薄雪", scene_cg.weather_hint("地上只剩一层薄雪") == "薄雪",
          scene_cg.weather_hint("地上只剩一层薄雪"))
    check("天晴 → 晴天", scene_cg.weather_hint("第二天天晴了") == "晴天",
          scene_cg.weather_hint("第二天天晴了"))
    check("圣诞 → 圣诞节", scene_cg.weather_hint("圣诞节那天") == "圣诞节",
          scene_cg.weather_hint("圣诞节那天"))
    check("没提天气 → 空", scene_cg.weather_hint("我们去吃饭吧") == "",
          scene_cg.weather_hint("我们去吃饭吧"))
    m_w = scene_cg.match("街上（晚上）（人行道）", "街上（白天）（人行道）",
                         weather_hint="白雪皑皑") or {}
    check("天气线索生效 → 挑雪景那张", "白雪皑皑" in (m_w.get("id") or ""), m_w.get("id"))
    check("细节线索：空无一人", "空无一人" in scene_cg.detail_hint("走廊里空无一人"),
          scene_cg.detail_hint("走廊里空无一人"))
    m_s = scene_cg.match("温泉旅馆（房间内）（晚上）", "温泉旅馆（房间内）（晚上）（开灯）",
                         detail_hints=["熄灯"]) or {}
    check("细节线索生效 → 熄灯那张", "熄灯" in (m_s.get("id") or ""), m_s.get("id"))

    print("\n[9] 提示词")
    hint = scene_cg.prompt_hint()
    check("带上了地名表", "咖啡厅" in hint and "大学图书馆" in hint)
    check("没有旧英文枚举", "classroom" not in hint)
    check("长度可控", len(hint) < 1600, "len=%d" % len(hint))

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
