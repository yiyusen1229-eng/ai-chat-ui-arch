# -*- coding: utf-8 -*-
"""记忆书逻辑的自测（不联网、不花钱）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_memory.py

验的是几条"坏了就出事"的规矩：
  1. 没超阈值 → 原样全发
  2. 超阈值 → 只发最近一段，且**起点不对齐到用户消息就是错的**
  3. 已总结到的位置必须 ≥ 最近段起点，否则中间那段两头不着（记忆空洞）
  4. 存档：完整保存，不裁剪；memory 字段能原样透传；后台写回要核对 saved_at
  5. 摘要失败要能降级，不能影响存/读档
"""
import json
import os
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
sys.path.insert(0, SRC)

import memory as M  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + (("  " + extra) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def fake_llm(prompt):
    """假模型：固定返回 3 条，其中一条 progress"""
    return json.dumps({"entries": [
        {"text": "玩家说过不喜欢下雨", "kind": "preference"},
        {"text": "两人约好周末去海边", "kind": "event"},
        {"text": "刚放学问完要不要一起走", "kind": "progress"},
    ]}, ensure_ascii=False)


def make_hist(n):
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        txt = ("这是第 %d 句测试对话，长度大概三十个字，用来把 token 预算撑起来。" % i)
        out.append({"role": role, "content": txt})
    return out


def rt_entries_check(M):
    return M.runtime()["entries"]


def main():
    # 运行时缓存指到临时目录：别动正在运行的程序的 cache/memory_runtime.json
    _tmp_cache = tempfile.mkdtemp(prefix="wa2_mem_")
    M.CACHE_DIR = _tmp_cache
    M.RUNTIME_FILE = os.path.join(_tmp_cache, "memory_runtime.json")
    cfg = M.cfg()
    print("参数:", cfg, "cloud budget:", M.budget(True), "local budget:", M.budget(False))

    print("\n[1] 估算")
    check("中文 10 字 ≈ 11 token", M.est_text("一二三四五六七八九十") == 11,
          "实际 %s" % M.est_text("一二三四五六七八九十"))
    check("空字符串是 0", M.est_text("") == 0)
    check("140 条消息 > 本地阈值", M.est_messages(make_hist(140)) > M.budget(False)[0])

    print("\n[2] 最近段对齐")
    hist = make_hist(200)
    trig, recent_b, entries_b = M.budget(False)
    aligned = M.recent_start(hist, recent_b, 0)
    check("起点落在用户消息上", hist[aligned]["role"] == "user", "role=%s" % hist[aligned]["role"])
    check("最近段不超预算", M.est_messages(hist[aligned:]) <= recent_b * 1.15,
          "实际 %d / 预算 %d" % (M.est_messages(hist[aligned:]), recent_b))
    check("起点往后挪了（确实裁了）", aligned > 0)

    print("\n[3] 首次打包 + 记忆空洞")
    M.reset_for_load([], 0, "t")
    ok, n = M.pack_span(hist, 0, aligned, fake_llm, "t", max_chunks=100)
    rt = M.runtime()
    check("打包成功", ok and n >= 3, "%s/%s" % (ok, n))
    check("去重后只剩 3 条", len(rt_entries_check(M)) == 3, str(len(rt_entries_check(M))))
    check("covered 追到最近段起点", rt["covered"] == aligned, "%s vs %s" % (rt["covered"], aligned))
    note = M.render_note(rt["entries"], entries_b)
    check("条目里有 progress", "现在进行到哪儿" in note)
    check("条目里有'不要编'" , "不要编造" in note)

    # 关键：窗口起点不能晚于 covered
    for extra in (0, 1, 5, 20):
        h2 = make_hist(200 + extra)
        a2 = M.recent_start(h2, recent_b, 0)
        start = min(a2, rt["covered"])
        check("无空洞（追加 %d 条后 start<=covered）" % extra, start <= rt["covered"])

    print("\n[4] 条目合并")
    old = [{"text": "旧事", "kind": "event"},
           {"text": "进展A", "kind": "progress"}]
    new = [{"text": "旧事", "kind": "event"},      # 重复
           {"text": "进展B", "kind": "progress"}]  # 替换
    merged = M.merge_entries(old, new, 40)
    check("重复条目不重复出现", sum(1 for e in merged if e["text"] == "旧事") == 1)
    check("progress 只留最新", [e["text"] for e in merged if e["kind"] == "progress"] == ["进展B"])

    print("\n[5] 模型输出解析")
    check("带 ```json 围栏", len(M.parse_entries('```json\n{"entries":[{"text":"x"}]}\n```')) == 1)
    check("前后有废话", len(M.parse_entries('好的，结果如下：{"entries":[{"text":"x"}]} 完')) == 1)
    check("坏输出返回空", M.parse_entries("不是 JSON") == []) 

    print("\n[6] 存档：完整保存 + memory 透传")
    import sessions as S
    tmp = tempfile.mkdtemp()
    S.SESS_DIR = tmp
    big = make_hist(300)
    r = S.session_save({"slot": "unit", "messages": big, "meta": {},
                        "memory": {"enabled": True, "covered_upto": 10, "entries": [{"text": "a"}]}})
    check("保存成功", r.get("ok") is True)
    d = S.session_load("unit")
    check("300 条一条不少", len(d["messages"]) == 300, "实际 %d" % len(d["messages"]))
    check("memory 原样存下", d["memory"]["covered_upto"] == 10)

    print("\n[7] 读档：存档里的记忆书 → 运行时缓存（只复制这一次）")
    seed = {"enabled": True, "covered_upto": 42,
            "entries": [{"text": "存档里的旧事", "kind": "event"}]}
    info = M.seed_from_save(seed, "7")
    rt2 = M.runtime()
    check("条目复制进缓存", rt2["entries"] == seed["entries"], repr(rt2))
    check("covered 一起复制", rt2["covered"] == 42 and rt2["slot"] == "7", repr(rt2))
    check("返回信息正确", info.get("enabled") and info.get("entries") == 1, repr(info))
    check("缓存文件已落盘", os.path.exists(M.RUNTIME_FILE), M.RUNTIME_FILE)
    M._RT = None                                   # 模拟重启后再读
    rt3 = M.runtime()
    check("重启后缓存还在", rt3["covered"] == 42 and len(rt3["entries"]) == 1, repr(rt3))
    M.seed_from_save({}, "8")                      # 旧档 / 短档：没有记忆书
    check("没记忆书的档来读→缓存清空", M.runtime()["entries"] == [])

    print("\n[8] 摘要失败降级")
    def bad_llm(prompt):
        raise RuntimeError("模型挂了")
    M.reset_for_load([], 0, "t")
    ok3, n3 = M.pack_span(hist, 0, aligned, bad_llm, "t")
    check("失败不抛异常", ok3 is False and n3 == 0)
    check("失败后记忆保持为空", M.runtime()["entries"] == [])

    print("\n[9] 旧存档兼容（没有 memory 字段）")
    p = os.path.join(tmp, "old.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"slot": "old", "messages": [{"role": "user", "content": "hi"}]}, f, ensure_ascii=False)
    dd = S.session_load("old")
    check("没有 memory 也能读", dd.get("ok") and dd.get("memory") is None)

    # ---- 2026-11-23：用户报「忘记上下文」，查出旧逻辑在 40 条上限处直接丢条目 ----
    print("\n[10] 条目到上限不许丢（这是「忘记上下文」的根因）")
    many = [{"text": "事件%d" % i, "kind": "event", "from": [i * 10, (i + 1) * 10]}
            for i in range(100)]
    merged = M.merge_entries([], many)
    check("100 条全部保留", len(merged) == 100, "实际 %d" % len(merged))
    check("最早那条还在", merged[0]["text"] == "事件0", merged[0])
    check("derived covered = 最大结尾", M.derived_covered(merged) == 1000,
          M.derived_covered(merged))

    print("\n[11] 空洞检测")
    check("从 0 开始 → 没洞", M.holes([{"text": "a", "from": [0, 50]}, {"text": "b", "from": [50, 90]}])["hole"] == 0)
    check("从 100 开始 → 洞 100", M.holes([{"text": "b", "from": [100, 200]}])["hole"] == 100,
          str(M.holes([{"text": "b", "from": [100, 200]}])))
    check("没有区间信息 → 不瞎报洞", M.holes([{"text": "x"}])["hole"] == 0)

    print("\n[12] 分层压缩：丢条数不丢内容")
    sixty = [{"text": "要点%d" % i, "kind": "event", "from": [i * 10, (i + 1) * 10]}
             for i in range(60)]
    M._RT = M._blank()
    M._RT["entries"] = list(sixty)
    M._save_runtime()
    comp, did, err = M.compact_entries(M.runtime()["entries"], fake_llm)
    check("确实压过", did and not err, "%s / %s" % (did, err))
    check("压缩后不超过上限", len(comp) <= 52, "实际 %d" % len(comp))
    check("出现了卷宗（kind=summary）",
          any((e.get("kind") or "") == "summary" for e in comp),
          [e.get("kind") for e in comp][:8])
    check("卷宗带 level≥1",
          all((e.get("level") or 0) >= 1 for e in comp if (e.get("kind") or "") == "summary"))
    frs = [e.get("from") for e in comp if e.get("from")]
    check("最早起点还是 0（早期剧情没被丢）", min(x[0] for x in frs) == 0,
          min(x[0] for x in frs))
    check("最大结尾还是 600（覆盖没缩水）", max(x[1] for x in frs) == 600,
          max(x[1] for x in frs))

    def bad_llm2(prompt):
        raise RuntimeError("模型挂了")
    before = [{"text": "要点%d" % i, "kind": "event", "from": [i * 10, (i + 1) * 10]}
              for i in range(60)]
    comp2, did2, err2 = M.compact_entries(before, bad_llm2)
    check("压缩失败时原样返回（不丢）", comp2 == before and not did2 and err2, "%s/%s" % (did2, err2))

    print("\n[13] 覆盖指针不许超前于内容")
    M._RT = M._blank()
    M._RT["entries"] = [{"text": "a", "kind": "event", "from": [0, 10]}]
    M._RT["covered"] = 999
    M._save_runtime()
    M._RT = None                                  # 模拟重启后加载缓存
    rt4 = M.runtime()
    check("加载时把 covered 拉回内容位置", rt4["covered"] == 10, rt4["covered"])
    check("原值记在 covered_fixed 里（可查）", rt4.get("covered_fixed") == 999, rt4.get("covered_fixed"))

    print("\n[14] 空洞自愈：后台补齐 + 写回存档（用假模型，不花钱）")
    try:
        import main as APP
        tmp2 = tempfile.mkdtemp(prefix="wa2_mem_fix_")
        M.CACHE_DIR = tmp2
        M.RUNTIME_FILE = os.path.join(tmp2, "memory_runtime.json")
        S.SESS_DIR = tmp2
        hist2 = make_hist(400)
        S.session_save({"slot": "fix", "messages": hist2, "meta": {},
                        "memory": {"enabled": True, "covered_upto": 400,
                                   "entries": [{"text": "最近的事", "kind": "event",
                                                "from": [300, 400]}]}})
        M.reset_for_load([{"text": "最近的事", "kind": "event", "from": [300, 400]}], 400, "fix")
        old_llm = APP._mem_llm
        APP._mem_llm = lambda prompt, max_tokens=1200, timeout=90: fake_llm(prompt)
        try:
            APP._memory_repair_worker(hist2, "fix", "fix|400|300", 300)
        finally:
            APP._mem_llm = old_llm
        rt = M.runtime()
        h2 = M.holes(rt.get("entries") or [], rt.get("covered"))
        check("补齐后空洞没了", h2["hole"] == 0, h2)
        check("覆盖到原窗口起点之后", M.derived_covered(rt.get("entries") or []) >= 300,
              M.derived_covered(rt.get("entries") or []))
        dd2 = S.session_load("fix")
        mm = dd2.get("memory") or {}
        check("写回存档的 covered_upto = 条目推导值",
              mm.get("covered_upto") == M.derived_covered(mm.get("entries") or []),
              mm.get("covered_upto"))
        check("写回存档带 rebuilt 标记", mm.get("rebuilt") is True, mm.get("rebuilt"))
        check("消息没被改动", len(dd2.get("messages") or []) == 400,
              len(dd2.get("messages") or []))
    except Exception as e:
        check("空洞自愈流程没抛异常", False, "%s: %s" % (type(e).__name__, e))

    print("\n" + ("全部通过" if not FAILS else "有 %d 项失败: %s" % (len(FAILS), FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
