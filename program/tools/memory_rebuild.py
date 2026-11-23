# -*- coding: utf-8 -*-
"""离线重建记忆书：把存档里"没被记住"的历史重新摘要补回来。

背景（2026-11-23）：旧版 memory.py 在 40 条上限处会把最老的条目**直接丢弃**，
而「覆盖到第几条」的指针照旧往前推 → 老存档里有一大段剧情既不在原文窗口、也不在记忆书里
（实测 2000 条的存档只剩最近 70 条的摘要）。原文 messages 是完整的，所以可以重建。

现在程序自己也会在**读档时**发现空洞并后台补齐（main._memory_check_repair）；
这个工具用于：程序关着的时候一次性修好某个存档、或先看看要花多少次模型调用。

跑法（在 program 目录下）：
    build_env\\venv\\Scripts\\python.exe tools\\memory_rebuild.py --slot auto --dry
    build_env\\venv\\Scripts\\python.exe tools\\memory_rebuild.py --slot auto
    build_env\\venv\\Scripts\\python.exe tools\\memory_rebuild.py --slot 18 --max-chunks 999

说明：
  · 需要云端模型可用（复用程序的 _mem_llm，即 config.json 里的模型配置）。
  · 只更新存档里的 `memory` 字段（原子写），不动 messages / meta；条数变了会拒绝写回。
  · 建议在程序关闭时跑（开着的话，下一次存档会用运行时的旧记忆书覆盖回去）。
"""
import argparse
import json
import os
import sys
import tempfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))
os.chdir(PROGRAM)

import memory as M  # noqa: E402
import sessions as S  # noqa: E402


def app_running(port=8771, timeout=1.5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/ping" % port, timeout=timeout) as r:
            return bool(r.status == 200 and b'"ok"' in r.read())
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser(description="重建存档的记忆书（补齐空洞）")
    ap.add_argument("--slot", default="auto", help="存档槽位（默认 auto）")
    ap.add_argument("--dry", action="store_true", help="只算要压多少块，不调用模型")
    ap.add_argument("--max-chunks", type=int, default=999, help="最多压几块（每块约 4000 token）")
    ap.add_argument("--chunk-tokens", type=int, default=0, help="覆盖每块 token 数（默认用配置）")
    ap.add_argument("--keep", action="store_true", help="保留已有条目，只补空洞那段")
    ap.add_argument("--sess-dir", default="", help="存档目录（默认用程序给当前角色算出来的）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # 存档目录默认在「当前角色的 characters/<角色>/sessions」下，要问 main 要（不能只看 datadir）
    import main as APP      # 只为拿 _mem_llm / 存档目录 / _memory_check_repair
    if args.sess_dir:
        S.SESS_DIR = os.path.abspath(args.sess_dir)
    else:
        try:
            APP._set_character_sessions()
        except Exception as e:
            print("解析存档目录失败：%s" % e)
    print("存档目录：%s" % S.SESS_DIR)

    d = S.session_load(args.slot)
    if not d.get("ok"):
        print("读不到存档 %r：%s" % (args.slot, d.get("error")))
        return 2
    msgs = d.get("messages") or []
    old_mem = d.get("memory") or {}
    old_entries = list(old_mem.get("entries") or [])
    print("存档 %s：%d 条消息，已有记忆 %d 条（覆盖到第 %s 条）"
          % (args.slot, len(msgs), len(old_entries), old_mem.get("covered_upto")))

    cfg = M.cfg()
    trig, recent_b, entries_b = M.budget(True)
    aligned = M.recent_start(msgs, recent_b, 0)
    print("预算：触发 %d / 最近段 %d token（约 %d 条）/ 条目 %d token"
          % (trig, recent_b, len(msgs) - aligned, entries_b))

    # 要补的范围：从 0 到最近窗口起点（这段现在应该全在记忆书里）
    target = aligned
    if args.keep and old_entries:
        h = M.holes(old_entries)
        start = int(h.get("earliest") or 0)
        print("保留模式：只补第 0~%d 条（已有条目从第 %d 条起）" % (start, start))
        target = start
        if target <= 0:
            print("没有空洞，不需要重建。")
            return 0
    else:
        start = 0

    # 估算块数
    chunk_tokens = args.chunk_tokens or int(cfg["chunk_tokens"])
    chunks, cur, size = 0, start, 0
    while cur < target:
        t = M.est_msg(msgs[cur])
        if size + t > chunk_tokens and cur > start and size > 0:
            chunks += 1
            size = 0
        size += t
        cur += 1
    if size > 0:
        chunks += 1
    print("要压 %d 条正文 → 约 %d 块（每块 %d token），之后还会做分层压缩"
          % (target - start, chunks, chunk_tokens))
    if args.dry:
        print("--dry：只估算，不调用模型。")
        return 0

    if app_running():
        print("!! 程序正在运行（8771 有响应）。重建结果可能被下一次存档覆盖，建议先关闭程序。")

    # 离线用临时 runtime，别覆盖正在运行的 cache/memory_runtime.json
    tmp = tempfile.mkdtemp(prefix="wa2_mem_rebuild_")
    M.CACHE_DIR = tmp
    M.RUNTIME_FILE = os.path.join(tmp, "memory_runtime.json")
    M.reset_for_load([] if not args.keep else old_entries, 0, args.slot)

    llm = APP._mem_llm
    done_chunks, ok_any = 0, False
    cur = start
    while cur < target and done_chunks < max(1, args.max_chunks):
        cur2, ok, n = M.pack_range(msgs, cur, target, llm, slot=args.slot,
                                   max_chunks=1, chunk_tokens=chunk_tokens)
        if not ok:
            print("第 %d 块失败（模型没返回可解析条目），停止。" % (done_chunks + 1))
            break
        ok_any = True
        done_chunks += 1
        cur = cur2
        print("  块 %d：压到第 %d 条（本块 %d 条记忆）" % (done_chunks, cur, n))
    if not ok_any:
        print("一块都没成功，放弃（存档未改）。")
        return 1

    entries = list(M.runtime().get("entries") or [])
    try:
        entries, compacted, err = M.compact_entries(entries, llm)
        if compacted:
            print("分层压缩完成 → %d 条（%s）" % (len(entries), err or "ok"))
    except Exception as e:
        print("分层压缩失败（保留原条目）：%s" % e)
    cov = M.derived_covered(entries)
    h = M.holes(entries, cov)
    print("重建成：%d 条记忆，覆盖第 %s~%d 条，最早起点 %d（空洞 %d）"
          % (len(entries), (entries[0].get("from") or ["?"])[0] if entries else "?",
             cov, h["earliest"], h["hole"]))

    mem = {"enabled": True, "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
           "covered_upto": cov, "entries": entries, "rebuilt": True}
    res = S.session_memory_update(args.slot, mem, expect_count=len(msgs))
    print("写回存档：", res)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
