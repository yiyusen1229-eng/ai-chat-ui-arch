# -*- coding: utf-8 -*-
"""记忆书：把长对话压成「印象」，让发给模型的上下文永远可控。

分工很清楚：

    日记 = 存档里的 messages      —— 完整保存，回想界面翻的是这份，永不裁剪
    印象 = memory.entries          —— 几条短句，只在「发给模型」时使用

什么时候启用：

  * 存档时：估算 token，超过阈值就在存档里打一个 memory.enabled 标记，
    并（后台）把这轮被"折叠"的旧对话压成条目写进同一个存档文件。
  * 读档时：看到标记就用「记忆书 + 最近一段原文」，没标记就照旧全量发送。
  * 游玩中途：每轮都估算，超过阈值就切到同样的策略；条目先放运行时缓存
    （cache/memory_runtime.json），关掉游戏就没了。

条目结构：{"text": "...", "kind": "preference|event|progress", "from": [起, 止]}

几条硬规矩（都是为了不把存档搞坏）：

  1. 摘要生成失败，绝不能影响存档 / 读档；失败就退回"不启用记忆书"。
  2. 已经总结到的位置 covered 不能超过"最近段起点"，否则中间那段两头不着 = 记忆空洞。
  3. 只增量总结新被折叠的那一段，老条目不重算（重算会漂移）。
"""
import json
import os
import time

import cache_io
import datadir

BASE = os.path.dirname(os.path.abspath(__file__))   # 程序目录（只读素材在这里）
# 运行时缓存目录（缓存记忆书就放这儿）；跟 sessions / 角色 / 日志同一个数据根
CACHE_DIR = datadir.env_or("WA2_CACHE_DIR", "cache")
RUNTIME_FILE = os.path.join(CACHE_DIR, "memory_runtime.json")
# 用户可改的参数放数据根（和 prefs.json / config.json 一个规矩）。
# 打包后 BASE 是临时解包目录，写进去程序一退出就没了。
USER_DIR = datadir.root()
CFG_FILE = os.path.join(USER_DIR, "memory.json")

# ------------------------------------------------------------------ 参数
# 都可以用 program/memory.json 覆盖（没有就用这里的默认值）
DEFAULTS = {
    # 模型能吃多少 token。云端取 32000（用户定的一档），本地 14B 那个小得多
    "cloud_ctx": 32000,
    "local_ctx": 4096,
    # 吃满 60% 就切换成记忆书模式（留 40% 给 人设 / 世界书 / 条目 / 回复）
    "trigger_ratio": 0.6,
    # 切换后，"最近一段原文"最多占上下文的多少
    "recent_ratio": 0.25,
    # 记忆条目总量最多占上下文的多少
    "entries_ratio": 0.12,
    # 条目数「软上限」：超过就把最老的一批**再压成一条更早的剧情**（不再直接丢弃）。
    # 2026-11-23 修：以前这里到 40 条就把老条目扔掉，而 covered 照旧往前推 → 静默记忆真空
    "max_entries": 40,
    # 分层压缩后，最多留几条「更早的剧情」卷宗（level>=1）
    "max_summary_entries": 12,
    # 一次压缩至少合并几条（太少会压得过碎）
    "compact_group": 6,
    # 安全上限：再怎么压也不许超过这个条数（防止坏模型输出把列表撑爆）
    "hard_max_entries": 400,
    # 切换后每多少轮重新打包一次
    "repack_turns": 6,
    # 一次打包最多产出几条
    "max_new_entries": 10,
    # 打包时每块多少 token（长历史要分块总结，不能一次塞给模型）
    "chunk_tokens": 4000,
    # 聊天中途同步打包最多做几块（多了会卡回复）
    "sync_chunks": 1,
    # 后台打包一次最多做几块
    "async_chunks": 6,
}


def cfg():
    c = dict(DEFAULTS)
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k, v in d.items():
                if k in c and isinstance(v, (int, float)):
                    c[k] = v
    except Exception:
        pass
    return c


def ctx_for(cloud):
    c = cfg()
    return int(c["cloud_ctx"] if cloud else c["local_ctx"])


def budget(cloud):
    """一次对话的三档预算：(触发阈值, 最近段上限, 条目上限)"""
    c = cfg()
    ctx = ctx_for(cloud)
    return (int(ctx * c["trigger_ratio"]),
            int(ctx * c["recent_ratio"]),
            int(ctx * c["entries_ratio"]))


# ------------------------------------------------------------------ token 估算
def est_text(s):
    """近似 token：中日文字 1 字约 1 token，其它字符 4 个约 1，再乘 1.05 余量。
    不装 tokenizer —— 阈值留了 40% 余量，这点误差无所谓。"""
    if not s:
        return 0
    cjk = 0
    for ch in s:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF or
                0x3400 <= o <= 0x4DBF or 0x3000 <= o <= 0x303F or
                0xFF00 <= o <= 0xFFEF):
            cjk += 1
    return int((cjk + (len(s) - cjk) / 4.0) * 1.05) + 1


def est_msg(m):
    return 4 + est_text((m or {}).get("content") or "")


def est_messages(msgs):
    return sum(est_msg(m) for m in (msgs or []))


# ------------------------------------------------------------------ 运行时记忆书
_RT = None


def _blank():
    return {"entries": [], "covered": 0, "turns": 0, "packs": 0,
            "slot": "", "window_start": 0, "updated_at": "",
            "covered_fixed": 0, "repair": {}}


def runtime():
    global _RT
    if _RT is None:
        _RT = _blank()
        try:
            # 坏缓存（0 字节 / 半截 JSON）当没有：cache_io 会删掉它
            d = cache_io.read_json(RUNTIME_FILE)
            if isinstance(d, dict):
                for k in _RT:
                    if k in d:
                        _RT[k] = d[k]
        except Exception:
            pass
        # 2026-11-23：指针不许超前于内容（老缓存里可能被推过头）
        # 注意：条目没有 from 区间（老档 / 手写测试）时推导不出内容位置，就不动 covered
        try:
            cov = derived_covered(_RT.get("entries") or [])
            if cov > 0 and int(_RT.get("covered") or 0) > cov:
                _RT["covered_fixed"] = int(_RT.get("covered") or 0)
                _RT["covered"] = cov
        except Exception:
            pass
    return _RT


def runtime_holes():
    """当前运行时记忆书的空洞信息（给日志 / 界面 / 自愈用）"""
    r = runtime()
    h = holes(r.get("entries") or [], r.get("covered"))
    h["entries"] = len(r.get("entries") or [])
    h["covered"] = int(r.get("covered") or 0)
    h["derived"] = derived_covered(r.get("entries") or [])
    return h


def _save_runtime():
    r = runtime()
    r["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = RUNTIME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
        os.replace(tmp, RUNTIME_FILE)
    except Exception:
        pass


def summary_text(max_chars=600):
    """把当前记忆书的条目拼成一段文字。

    多角色要用：读档之后只给导演/角色看最近几十条原话，早先的剧情就接不上了；
    记忆书是「这一段之前发生过什么」的摘要，和原话一起给才对得上「接着上文」。
    """
    try:
        r = runtime() or {}
        rows = [str(e.get("text") or "").strip() for e in (r.get("entries") or [])]
        text = "\n".join(x for x in rows if x)
    except Exception:
        return ""
    if max_chars and len(text) > max_chars:
        text = text[-max_chars:]
    return text


def runtime_set(**kw):
    r = runtime()
    r.update(kw)
    _save_runtime()
    return r


def clear_runtime():
    """程序启动时清空：记忆缓存只活这一次运行"""
    global _RT
    _RT = _blank()
    try:
        os.remove(RUNTIME_FILE)
    except Exception:
        pass


def reset_for_load(entries=None, covered=0, slot=""):
    """读档：把存档里的记忆书接上来（没有就是空的）"""
    global _RT
    _RT = _blank()
    _RT["entries"] = list(entries or [])
    _RT["covered"] = max(0, int(covered or 0))
    _RT["slot"] = slot or ""
    _save_runtime()
    return _RT


def seed_from_save(mem, slot=""):
    """读档：存档里的记忆书整份**复制**到运行时缓存，之后只读缓存。

    这是整个机制里唯一一次「存档 → 缓存」的搬运：
      · 存档里是记忆书形式（有 entries）→ 复制 entries / covered_upto 进缓存
      · 不是（旧档 / 短档）→ 清空缓存，按全量发送
    返回值给日志用。
    """
    mem = mem if isinstance(mem, dict) else {}
    entries = mem.get("entries")
    entries = list(entries) if isinstance(entries, list) else []
    covered = int(mem.get("covered_upto") or 0)
    reset_for_load(entries, covered, slot)
    return {"entries": len(entries), "covered": covered,
            "slot": slot, "enabled": bool(entries)}


# ------------------------------------------------------------------ 最近段
def recent_start(msgs, budget_tokens, covered=0):
    """从末尾往前数，数到预算用完为止；起点对齐到用户消息。

    关键：起点不能晚于 covered —— 否则 covered 到起点这段既不在原文里、
    也没被总结，就成了"记忆空洞"。
    """
    msgs = msgs or []
    n = len(msgs)
    total = 0
    start = n
    for i in range(n - 1, -1, -1):
        t = est_msg(msgs[i])
        if total + t > budget_tokens and start < n:
            break
        total += t
        start = i
    while start > 0 and (msgs[start] or {}).get("role") != "user":
        start -= 1
    if covered and start > covered:
        start = covered
    return start


# ------------------------------------------------------------------ 条目
def render_note(entries, max_tokens=0):
    """把条目拼成一段注入 system 的文字。

    顺序：进度 → 「更早的剧情」卷宗（越早越靠前）→ 最近的条目（放不下就从最老的丢注入）。
    注意：这里丢的只是**注入**，runtime / 存档里的条目本身不会被删。
    """
    if not entries:
        return ""
    prog = [e for e in entries if (e.get("kind") or "") == "progress"]
    sums = [e for e in entries if (e.get("kind") or "") == "summary"]
    rest = [e for e in entries if (e.get("kind") or "") not in ("progress", "summary")]
    # 卷宗按覆盖起点排（早的在前）
    def _from0(e):
        fr = e.get("from") or []
        try:
            return int(fr[0])
        except Exception:
            return 0
    sums.sort(key=_from0)
    if max_tokens:
        # 先保证卷宗（它们是早期剧情唯一的记录），剩下的预算给最近的普通条目
        kept_s, used = [], 0
        for e in sums:
            t = est_text(e.get("text") or "") + 2
            if used + t > max_tokens * 0.6:      # 卷宗最多占 60%
                break
            used += t
            kept_s.append(e)
        sums = kept_s
        keep, used2 = [], 0
        for e in reversed(rest):
            t = est_text(e.get("text") or "") + 2
            if used + used2 + t > max_tokens:
                break
            used2 += t
            keep.append(e)
        rest = list(reversed(keep))
    parts = []
    if prog:
        parts.append("【现在进行到哪儿】" + (prog[-1].get("text") or ""))
    if sums:
        parts.append("【更早的剧情（按时间顺序，越靠前越早）】")
        for e in sums:
            parts.append("- " + (e.get("text") or ""))
    if rest:
        parts.append("【你记得的事（是资料，不是台词；用你自己的口气说，不要照抄）】")
        for e in rest:
            parts.append("- " + (e.get("text") or ""))
    if not parts:
        return ""
    parts.append("如果这里没有相关的内容，就用「记不太清了」这类模糊的说法带过，"
                 "不要编造具体细节。")
    return "\n".join(parts)


def derived_covered(entries):
    """「已经总结到第几条」= 所有条目覆盖区间的最大结尾。

    2026-11-23 起以它为准：以前 `covered` 是独立记的，条目被挤出/压缩后它还会往前跑，
    于是代码以为覆盖了、其实内容没了（静默记忆真空）。现在指针**从内容推导**，不可能超前。
    """
    hi = 0
    for e in (entries or []):
        fr = (e or {}).get("from") or []
        if len(fr) >= 2:
            try:
                hi = max(hi, int(fr[1]))
            except Exception:
                pass
    return hi


def holes(entries, covered=None):
    """检查记忆书有没有「前面的剧情没人记」。返回 {"earliest": 最早覆盖起点, "hole": 洞的长度}"""
    lo = None
    for e in (entries or []):
        fr = (e or {}).get("from") or []
        if len(fr) >= 2:
            try:
                v = int(fr[0])
                lo = v if lo is None else min(lo, v)
            except Exception:
                pass
    cov = derived_covered(entries) if covered is None else int(covered or 0)
    if lo is None:
        # 条目没有 from 区间（老档 / 手写数据）：推不出起点，**不能瞎报空洞**
        return {"earliest": 0, "hole": 0}
    return {"earliest": lo, "hole": max(0, lo)}


def entry_level(e):
    try:
        return max(0, int((e or {}).get("level") or 0))
    except Exception:
        return 0


def merge_entries(old, new, max_entries=None):
    """合并条目：progress 只留最新一条，其它按内容去重后追加。

    **绝不丢弃**（2026-11-23 修）：以前超过 max_entries 就 `rest[-max_entries:]` 把老条目扔掉，
    而 covered 继续前进 → 早期剧情永久丢失（实测 2000 条存档只剩最近 70 条的摘要）。
    现在超上限交给 `compact_entries()` 压成「更早的剧情」卷宗；数据本身永远留着
    （只有注入时按 token 预算裁剪）。`max_entries` 参数保留只是为了兼容旧调用。
    """
    out = list(old or [])
    for e in (new or []):
        txt = (e.get("text") or "").strip()
        if not txt:
            continue
        kind = e.get("kind") or "event"
        item = {"text": txt, "kind": kind, "from": e.get("from") or []}
        if e.get("level"):
            item["level"] = int(e.get("level") or 0)
        if kind == "progress":
            out = [x for x in out if (x.get("kind") or "") != "progress"]
            out.append(item)
            continue
        if any((x.get("text") or "").strip() == txt for x in out):
            continue
        out.append(item)
    prog = [x for x in out if (x.get("kind") or "") == "progress"]
    rest = [x for x in out if (x.get("kind") or "") != "progress"]
    return rest + prog


COMPACT_SYSTEM = (
    "你是对话记忆整理器。把给的一批「剧情要点」合并压缩成更短的一批要点。"
    "只输出 JSON，不要解释。"
)


def build_compact_prompt(entries):
    lines = []
    for e in entries:
        lines.append("- " + (e.get("text") or "").strip())
    body = "\n".join(lines)
    return (
        "下面是一段对话的若干条记忆要点（越靠前越早）：\n\n" + body + "\n\n"
        "请把它们合并压缩成 2~4 条要点，要求：\n"
        "1. 只保留对后续剧情有用的事实：人物关系变化、约定、重要事件、明确的喜好。\n"
        "2. 合并同类项，删掉琐碎细节；保留具体名字/地点/时间点。\n"
        "3. 第三人称、一句话一条，不要写台词。\n"
        "4. JSON 格式：{\"entries\":[{\"text\":\"...\",\"kind\":\"event|preference\"}]}\n"
        "只输出这个 JSON。"
    )


def compact_entries(entries, llm, max_entries=None, group=None,
                    max_summaries=None):
    """把最老的一批条目压成一条「更早的剧情」卷宗（kind=summary，带 level）。

    返回 (新条目列表, 是否压过, 错误信息)。压不动（模型失败）就**原样返回**，绝不丢条目。
    """
    c = cfg()
    max_entries = max(4, int(max_entries if max_entries is not None else c["max_entries"]))
    group = max(2, int(group if group is not None else c["compact_group"]))
    max_summaries = max(1, int(max_summaries if max_summaries is not None
                               else c["max_summary_entries"]))
    cur = list(entries or [])
    done = False
    for _ in range(40):
        rest = [x for x in cur if (x.get("kind") or "") != "progress"]
        sums = [x for x in rest if (x.get("kind") or "") == "summary"]
        if len(rest) <= max_entries and len(sums) <= max_summaries:
            break
        # 先压最老卷宗（层级高的先被合掉），再压最老普通条目
        order = sorted(range(len(cur)),
                       key=lambda i: (entry_level(cur[i]),
                                      (cur[i].get("from") or [0])[0], i))
        idxs = []
        for i in order:
            if (cur[i].get("kind") or "") == "progress":
                continue
            idxs.append(i)
            if len(idxs) >= group:
                break
        if len(idxs) < 2:
            break
        picked = [cur[i] for i in sorted(idxs)]
        try:
            raw = llm(build_compact_prompt(picked))
        except Exception as e:
            return cur, done, "压缩调用失败: %s" % e
        new = parse_entries(raw)
        if not new:
            return cur, done, "压缩没有产出条目"
        fr = []
        for e in picked:
            fr.extend(e.get("from") or [])
        span = [min(fr), max(fr)] if fr else []
        level = max(entry_level(e) for e in picked) + 1
        merged = [{"text": (x.get("text") or "").strip(),
                   "kind": "summary", "from": span, "level": level} for x in new]
        cur = [x for x in cur if x not in picked] + merged
        done = True
    return cur, done, ""


# ------------------------------------------------------------------ 总结
SUM_SYSTEM = (
    "你是对话记忆整理器。把给你的角色扮演对话压成简短、准确、可复用的记忆条目。"
    "只输出 JSON，不要解释。"
)


def build_sum_prompt(span, progress_hint=""):
    lines = []
    for m in span:
        role = "玩家" if (m.get("role") == "user") else "和纱"
        txt = (m.get("content") or "").replace("\n", " ").strip()
        if len(txt) > 400:
            txt = txt[:400] + "…"
        lines.append("%s：%s" % (role, txt))
    body = "\n".join(lines)
    hint = ""
    if progress_hint:
        hint = "\n（上一版记的进展是：%s，如果有变化请按新的写）" % progress_hint
    return (
        "下面是需要整理的一段对话：\n\n" + body + "\n\n"
        "请输出 5~10 条记忆条目，要求：\n"
        "1. 每条一句话，第三人称，只写事实：谁说过什么、发生了什么、约定了什么。\n"
        "2. 保留具体细节（名字、地点、时间、喜好、承诺）；不要写台词，不要描写场景，不要评论。\n"
        "3. 另外单独给一条 kind 为 progress 的条目：用 2~3 句说明「现在进展到哪儿」，"
        "只写最近发生的事。" + hint + "\n"
        "4. JSON 格式：{\"entries\":[{\"text\":\"...\",\"kind\":\"preference|event|progress\"}]}\n"
        "只输出这个 JSON。"
    )


def parse_entries(raw):
    """从模型输出里抠出 JSON（允许 ```json 包裹、前后有废话）"""
    if not raw:
        return []
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return []
    try:
        d = json.loads(s[a:b + 1])
    except Exception:
        # 模型输出被 max_tokens 截断时 JSON 不完整，退而求其次：抓出完整的 text/kind 对
        out = []
        import re as _re
        for m in _re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', s):
            txt = m.group(1).replace('\\"', '"').strip()
            if not txt:
                continue
            tail = s[m.end():m.end() + 80]
            km = _re.search(r'"kind"\s*:\s*"([a-z]+)"', tail)
            kind = km.group(1) if km else "event"
            if kind not in ("preference", "event", "progress"):
                kind = "event"
            out.append({"text": txt, "kind": kind})
        return out
    arr = d.get("entries") if isinstance(d, dict) else None
    if not isinstance(arr, list):
        return []
    out = []
    for e in arr:
        if isinstance(e, dict) and (e.get("text") or "").strip():
            k = e.get("kind") or "event"
            if k not in ("preference", "event", "progress"):
                k = "event"
            out.append({"text": e["text"].strip(), "kind": k})
        elif isinstance(e, str) and e.strip():
            out.append({"text": e.strip(), "kind": "event"})
    return out


def _pack_one(msgs, start, end, llm, slot=""):
    """把 msgs[start:end] 这一块压成条目，并入运行时记忆书"""
    span = (msgs or [])[start:end]
    if not span:
        return False, 0
    r = runtime()
    prog = ""
    for e in r["entries"]:
        if (e.get("kind") or "") == "progress":
            prog = e.get("text") or ""
    try:
        raw = llm(build_sum_prompt(span, prog))
    except Exception:
        return False, 0
    new = parse_entries(raw)
    if not new:
        return False, 0
    # 标上来源区间，便于回溯是哪几条消息总结出来的
    for e in new:
        e["from"] = [start, end]
    merged = merge_entries(r["entries"], new)
    # 超软上限：把最老的压成「更早的剧情」卷宗（不再直接丢弃）
    try:
        merged, _compacted, _err = compact_entries(merged, llm)
    except Exception:
        _compacted = False
    # covered 从条目区间推导：指针不可能跑到内容前面
    cov = max(derived_covered(merged), int(r.get("covered") or 0))
    runtime_set(entries=merged, covered=cov,
                packs=int(r.get("packs", 0)) + 1, slot=slot or r.get("slot", ""))
    return True, len(new)


def pack_range(msgs, start, end, llm, slot="", max_chunks=4, chunk_tokens=None):
    """从 start 往 end 压（最多 max_chunks 块），返回 (做到第几条, 是否成功过, 新增条数)。

    和 pack_span 的区别：它把**游标**也返回，供"补齐记忆书空洞"这种需要知道进度的地方用。
    """
    c = cfg()
    chunk_tokens = int(chunk_tokens or c["chunk_tokens"])
    cur, total, ok_any, chunks = int(start), 0, False, 0
    while cur < end and chunks < max(1, int(max_chunks)):
        size, chunk_end = 0, cur
        while chunk_end < end:
            t = est_msg(msgs[chunk_end])
            if size + t > chunk_tokens and chunk_end > cur:
                break
            size += t
            chunk_end += 1
        ok, n = _pack_one(msgs, cur, chunk_end, llm, slot)
        if not ok:
            break
        ok_any, total, cur, chunks = True, total + n, chunk_end, chunks + 1
    return cur, ok_any, total


def pack_span(msgs, start, end, llm, slot="", max_chunks=4):
    """把 msgs[start:end] 压成条目。

    长历史分块总结（每块 chunk_tokens），从最老的未覆盖处往前推：
    一块一块来，做不完下次再继续 —— 这样第一次跨阈值不会因为要总结几百条
    而卡住，也不会一次给模型塞两万多 token。

    返回 (是否有进展, 新增条目数)。失败不抛，交给调用方降级。
    """
    _cur, ok_any, total = pack_range(msgs, start, end, llm, slot, max_chunks)
    return ok_any, total
