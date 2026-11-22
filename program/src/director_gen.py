# -*- coding: utf-8 -*-
"""导演真正调用模型：拼提示词 → 发请求 → 解析 JSON → 返回一拍。

设计上只做三件事：
  1. _prompt()   把「角色卡 + 玩家走向 + 第几拍 + 输出契约」拼成两段消息；
  2. generate()  调 llm.generate，容错解析（JSON 坏了就按行切），校验说话人；
  3. 结果交给 playqueue（由 main.py 的接口入队），这一层不碰队列。
"""
import json
import re

import char_cards
import cast as cast_mod
import llm
import scenelog
import usage

# 一拍允许的最大输出（和 LENGTHS 里的 tokens 对齐，留一点余量）
MAX_TOKENS = {"small": 900, "medium": 1400, "large": 2200}


def _cfg():
    """导演模型配置：优先角色自己的 director 段，没有就用全局 chat"""
    # 场景级导演配置优先（和角色解耦）：characters/director.json
    try:
        import scene_model
        c = scene_model.cfg()
        if c.get("base_url") or c.get("api_key"):
            return c
    except Exception:
        pass
    try:
        import char_model
        raw = char_model._load_raw() or {}
        sec = raw.get("director") or {}
        if sec.get("endpoint") or sec.get("key"):
            return {"base_url": sec.get("endpoint") or "", "api_key": sec.get("key") or "",
                    "model": sec.get("model") or ""}
    except Exception:
        pass
    try:
        import main
        g = (main.model_cfg_all() or {}).get("chat") or {}
        return {"base_url": g.get("endpoint") or "", "api_key": g.get("key") or "",
                "model": g.get("model") or ""}
    except Exception:
        return {}


def _cast_lines():
    """阵容描述：每个角色一段压缩卡"""
    out = []
    for m in cast_mod.get_cast().get("members", []):
        if m.get("role") == "player":
            out.append("- 玩家「%s」：由玩家扮演；你**可以**给他写台词，用 speaker=\"%s\""
                       % (m.get("name"), m.get("name")))
            continue
        if m.get("role") == "extra":
            out.append("- 龙套「%s」：临时角色，只接话，不给内心" % m.get("name"))
            continue
        cid = m.get("id")
        info = char_cards.info(cid)
        card = char_cards.read_card("director", cid).strip()
        role = "（玩家扮演）" if m.get("role") == "protagonist" else ""
        out.append("- %s%s\n%s" % (info["name"], role, card or "（没有角色卡）"))
    return "\n".join(out) if out else "（阵容为空）"


# 输出结构的 schema：有它，本地模型（尤其 abliterated）就不会自由发挥成散文。
# 用 Ollama 的 format 字段做语法级约束；云端仍然走提示词。
BEATS_SCHEMA = {
    "type": "object",
    "properties": {
        "beats": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "speaker": {"type": "string"},
                    "type": {"type": "string", "enum": ["say", "narr"]},
                    "text": {"type": "string"},
                    "ban": {"type": "string"},
                    # 这一拍是不是成人场面：语音要据此换参考音频。
                    # 关键词判定经常漏（实测"嗯？（把手指伸入她的阴道浅浅抽插）"这种
                    # 只算 1 分），所以让导演自己标一次，比数词可靠。
                    "hscene": {"type": "boolean"}
                },
                "required": ["speaker", "type", "text"]
            }
        }
    },
    "required": ["beats"]
}


SYSTEM = """你是这出戏的导演。你不扮演任何角色，只决定「这一拍谁开口、说什么」。
规则：
1. 只输出 JSON，不要解释、不要 Markdown 代码块。
2. 每个角色都要按他自己的角色卡说话，不要替他说超出人设的话。
3. 台词要短；旁白只写必要的一两句。
4. speaker 必须是阵容里的名字；**叙述、环境、动作、心理描写一律用空字符串当旁白**，
   不要塞进某个角色的台词里（角色台词只写他说出口的话）。
   整场都是台词会「只有声音没有画面」：每两三拍至少给一条旁白。
   旁白里指玩家一律写「你」（不要写「他」「玩家」「那个人」）；指某个角色就直接写名字。
5. 龙套（临时角色）只接话，不推动剧情。
6. 玩家也有戏：提示里写【给玩家一句】时，替他写一句短台词（speaker 用玩家名字）；
   写【玩家自己在说话】或【玩家刚说过话】时**不要**替他写，只写别人的反应。
7. 在场有两个以上角色时，尽量让不同角色都开口；但**不要为了凑人头硬塞**：
   这一拍内容只够一个人说，就只写一个人。
8. 某句台词里如果有她**绝对不可能做**的事（主动示弱、道歉、说出越过人设的话），
   就在这一条里加 `"ban":"一句话写明不能做什么"`；角色模型会照它演。
   没有就省略这个字段，不要每条都写。
9. **不要重复**【刚才发生的事】里已经出现过的句子或同一个情节。上一拍写了敲门，
   这一拍必须写门开之后发生的事；上一拍写了"X 说 …"，这一拍就要有新的动作或新的话。
   重复 = 这一拍作废。
10. 如果【刚才发生的事】是**成人场面**（露骨的性描写、明确的身体接触），
    这一拍每条都要带 `"hscene":true`；否则省略这个字段。
    这个标记决定语音用哪套参考音频，标错了台词会变成平常语气。"""


def _active_name():
    """当前角色的显示名：老存档里 assistant 消息没有 speaker，那些话是**她**说的"""
    try:
        cid = cast_mod.chars.active_id()
        nm = cast_mod.chars.display_name(cid) if cid else ""
        if nm:
            return nm
    except Exception:
        pass
    try:
        for m in cast_mod.get_cast().get("members", []):
            if m.get("role") == "protagonist" and m.get("id"):
                return char_cards.info(m["id"]).get("name") or ""
    except Exception:
        pass
    return ""


def _rows(messages=None):
    """「刚才发生的事」从哪来：优先用前端带上来的 history（和 /api/chat 一个路子），
    没带才退回 scenelog（队列占位 / 脚本 / 老调用）。

    这样读档、跨场景、中途打开多角色都不用另做铺回：history 里有什么就看到什么。
    """
    if messages:
        try:
            return scenelog.rows_from(messages, _player_name(), 200, _active_name())
        except Exception:
            pass
    return scenelog.recent(200)


def _norm_text(s):
    """去掉标点和空白，用来判断"是不是同一句"（全角/半角标点不算区别）"""
    return re.sub(r"[\s，。、,.!！?？;；:：…—\-~～「」『』\"'“”‘’（）()]+", "",
                  (s or "").strip())


def _bigrams(s):
    """字符二元组：用来抓"换了几个词但还是一回事"的复读"""
    t = _norm_text(s)
    return set(t[i:i + 2] for i in range(len(t) - 1))


def _is_repeat(text, recent_texts, sim=0.55):
    """这句是不是【刚才发生的事】里已经有过的（一模一样 / 前 10 字相同 / 二元组高度重合）"""
    t = _norm_text(text)
    if not t:
        return True
    tb = _bigrams(text)
    for p_text in recent_texts or []:
        p = _norm_text(p_text)
        if not p:
            continue
        if t == p:
            return True
        # 前 10 个字一样 → 基本是同一句（只改了标点/语气词）
        if len(t) >= 8 and len(p) >= 8 and t[:10] == p[:10]:
            return True
        # 换了几个词但还是一回事：字符二元组重合度很高
        pb = _bigrams(p_text)
        if tb and pb:
            j = len(tb & pb) / float(len(tb | pb))
            if j >= sim:
                return True
    return False


def _drop_repeats(beats, rows, k=6):
    """把和最近几拍重复的台词/旁白剔掉：模型（尤其本地小模型）会把上一拍原样再来一遍"""
    prev = [(r.get("text") or "") for r in (rows or [])[-max(1, int(k or 1)):]]
    if not prev:
        return beats
    return [b for b in beats if not _is_repeat(b.get("text"), prev)]


def _needs_narr(rows=None, k=4):
    """最近几条里一条旁白都没有 → 这一拍要补一条画面

    全是台词会「只有声音没有画面」：谁站在哪、雪停了没有、手在抖没抖，都得有人写。
    场次开头也算需要（len < 2 时先给一条环境）。
    """
    rows = (rows if rows is not None else scenelog.recent(200))[-max(1, int(k or 1)):]
    if len(rows) < 2:
        return True
    return not any((r.get("type") or "") == "narr" for r in rows)


def _player_name():
    """阵容里的玩家叫什么（没有就"你"）"""
    try:
        for m in cast_mod.get_cast().get("members", []):
            if m.get("role") == "player" and m.get("name"):
                return m["name"]
    except Exception:
        pass
    return "你"


def _last_is_player(rows=None):
    """最后一条原话是不是玩家说的（是的话这一拍别再替他说话）"""
    try:
        rows = rows if rows is not None else scenelog.recent(1)
        return bool(rows) and (rows[-1].get("speaker") or "") == _player_name()
    except Exception:
        return False


def _has_player_in_cast():
    try:
        return any(m.get("role") == "player" and m.get("name")
                   for m in cast_mod.get_cast().get("members", []))
    except Exception:
        return False


def _player_lines_recent(rows, k=6):
    """最近 k 条里玩家说过几句"""
    try:
        nm = _player_name()
        return sum(1 for r in (rows or [])[-max(1, int(k or 1)):]
                   if (r.get("speaker") or "") == nm)
    except Exception:
        return 0


def _user_prompt(beat_index, total_beats, n=2, rows=None, auto=False):
    p = _state()
    spec = p.get("beats") or 7
    chars_per = p.get("chars_per_beat") or 350
    n_beats = max(1, int(n or 2))
    # 导演也得知道刚才发生了什么，否则每一拍都在从零开始编。
    # 读档后光是最近这一小段不够：还要把记忆书（更早的剧情摘要）给它。
    rows = rows if rows is not None else _rows()
    recent = scenelog.render(rows[-60:], 5000)
    older = ""
    try:
        import memory
        older = memory.summary_text(800)
    except Exception:
        older = ""
    older_block = ("【更早的事（摘要）】\n%s\n\n" % older) if older else ""
    narr_hint = ""
    if _needs_narr(rows):
        narr_hint = ("\n【这一拍要画，不要连着好几拍都没画面】\n"
                     "可以留一条旁白（speaker 留空、type=narr）：写环境、动作或某个人的小动作，"
                     "20~40 字；不要写心理分析，不要加引号。内容不需要画面就别硬加。\n")
    player_hint = ""
    if _last_is_player(rows):
        player_hint = ("\n【玩家刚说过话】\n最后一条是玩家亲口说的。这一拍写**别人对他的反应**，"
                       "不要再替他说话，也不要把他的话改写成旁白。\n")
    elif auto and _has_player_in_cast() and _player_lines_recent(rows) == 0:
        # 自动模式：玩家不会自己打字，戏份得导演给。每几拍至少给一句，
        # 否则「阵容里明明有人，整场却听不到他说话」。
        player_hint = ("\n【给玩家一句】\n玩家也在场上，但最近几拍一句话都没有。"
                       "这一拍给「%s」写一句短的（speaker=\"%s\"），像玩家会说的话；"
                       "不要写成旁白，也不要写成解说。\n" % (_player_name(), _player_name()))
    elif not auto and _has_player_in_cast():
        # 手动模式：玩家自己会说话，导演替他写只会重复、还容易替他做决定
        player_hint = ("\n【玩家自己在说话】\n这一场玩家亲自打字，**不要替他写台词**；"
                       "写别人对他的反应就行。\n")
    return """【本场阵容】
%s

%s【刚才发生的事】
%s

【玩家给的走向】
%s

【现在的位置】
第 %d 拍，共 %d 拍；这一拍大约 %d 字。
只写 %d 条（不要多写），每条不超过 %d 字，**紧接上一条往下接**，不要另起一段戏。
%s%s
【输出格式】
一个 JSON 对象，只有 "beats" 数组。每条含：
  "speaker" —— 阵容里的名字；旁白给空字符串 ""（不要写字面量"角色名"或"旁白"）
  "type"    —— "say" 或 "narr"
  "text"    —— 台词或旁白正文
  "ban"     —— 可选，她这一刻绝对不能做的事
不要写 JSON 以外的任何字。""" % (
        _cast_lines(), older_block,
        recent or "（还没有，这是本场开头）",
        p.get("direction") or "（玩家没给，顺着当前气氛往下走）",
        beat_index + 1, total_beats, chars_per, n_beats, chars_per,
        player_hint, narr_hint)


def _state():
    """从 director 模块拿当前计划（避免循环 import，运行时取）"""
    try:
        import director
        return {
            "planned": director._state.get("planned") or {},
            "beats": (director._state.get("planned") or {}).get("beats") or 7,
            "chars_per_beat": (director._state.get("planned") or {}).get("chars_per_beat") or 350,
            "direction": director._state.get("direction") or ""
        }
    except Exception:
        return {}


def _parse(text):
    """容错解析：先当 JSON，再找 {...}，最后按「名字：「…」」逐行切"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        d = json.loads(t)
        if isinstance(d, dict) and isinstance(d.get("beats"), list):
            return d["beats"]
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and isinstance(d.get("beats"), list):
                return d["beats"]
        except Exception:
            pass
    # 兜底 1：JSON 被截断时，把所有完整的 {"speaker":...} 对象抠出来
    objs = re.findall(r'\{[^{}]*"speaker"[^{}]*\}', t)
    if objs:
        got = []
        for s in objs:
            try:
                d = json.loads(s)
                if isinstance(d, dict):
                    got.append(d)
            except Exception:
                continue
        if got:
            return got
    # 兜底 2：逐行切
    beats = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        mm = re.match(r"^([^：:「」\s]{1,12})[：:]\s*[「『]?(.*?)[」』]?$", line)
        if mm:
            beats.append({"speaker": mm.group(1), "type": "say", "text": mm.group(2)})
        else:
            beats.append({"speaker": "", "type": "narr", "text": line})
    return beats


def _allowed():
    names = set()
    for m in cast_mod.get_cast().get("members", []):
        if m.get("role") in ("extra", "player"):
            names.add(m.get("name") or "")
        else:
            names.add(char_cards.info(m.get("id"))["name"])
    names.discard("")
    return names


def _name_to_cid():
    """显示名 → 角色 id（只有演员有 id；玩家 / 龙套没有，留空）

    后面的立绘、音色都按这个 id 找。以前整条链路只传显示名，
    名字和文件夹名不一致（改名 / 重名）就会摸到别人的素材。
    """
    out = {}
    for m in cast_mod.get_cast().get("members", []):
        if m.get("role") in ("extra", "player"):
            continue
        cid = m.get("id") or ""
        if not cid:
            continue
        try:
            nm = char_cards.info(cid).get("name") or cid
        except Exception:
            nm = cid
        if nm:
            out[nm] = cid
    return out


def _silent_names():
    """没有语音模型的说话人（玩家 / 龙套）：有台词，但不该送去合成"""
    out = set()
    try:
        for m in cast_mod.get_cast().get("members", []):
            if m.get("role") in ("player", "extra") and m.get("name"):
                out.add(m["name"])
    except Exception:
        pass
    return out


def _clean(beats, limit):
    """"校验说话人 + 截断长度；返回 (beats, 丢弃数)"""
    allow = _allowed()
    ids = _name_to_cid()
    silent = _silent_names()
    out, dropped = [], 0
    for b in beats or []:
        if not isinstance(b, dict):
            dropped += 1
            continue
        sp = (b.get("speaker") or "").strip()
        if sp and sp not in allow:
            dropped += 1
            continue
        text = (b.get("text") or "").strip()
        if not text:
            dropped += 1
            continue
        if len(text) > limit:
            cut = text[:limit]
            pos = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"), cut.rfind("…"))
            text = cut[:pos + 1] if pos > limit * 0.5 else cut
        typ = "narr" if (b.get("type") == "narr" or not sp) else "say"
        out.append({"speaker": sp, "text": text, "type": typ,
                    # 玩家 / 龙套没有语音模型：别把他的台词送去合成（否则会借用别人的音色）
                    "voice": bool(sp) and sp not in silent,
                    # 立绘 / 音色按 id 找（名字只用来显示）
                    "cid": ids.get(sp, ""),
                    # 红线：导演给的一句话，交给角色模型当"绝对不能做"
                    "ban": str(b.get("ban") or "").strip()[:120],
                    # 导演标的"这一拍是成人场面"：主流程用它决定语音参考，
                    # 关键词判定只是兜底（实测会漏）
                    "hscene": bool(b.get("hscene"))})
    return out, dropped


def _render_beats(beats, scene="", direction="", rows=None):
    """把 say 类台词换成角色自己写的那一句（并发调用各角色模型）。

    direction 是玩家给的走向：交给角色当「往哪儿演」，提示词里明确标成不是台词。
    """
    if not beats:
        return beats
    import cast as cast_mod
    by_name = {}
    for m in cast_mod.get_cast().get("members", []):
        if m.get("role") in ("player", "extra"):
            continue
        cid = m.get("id") or ""
        name = m.get("name") or ""
        try:
            name = cast_mod.chars.display_name(cid) or name
        except Exception:
            pass
        if name and cid:
            by_name[name] = cid
    if not by_name:
        return beats

    import actor
    from concurrent.futures import ThreadPoolExecutor
    # 「刚才发生的事」跟着这一拍走：前端带上来的 history 里就包含刚播的、玩家刚说的，
    # 角色看到的是同一份，不会各说各的
    recent_text = scenelog.render((rows or [])[-20:], 1500) if rows else None
    jobs = []
    for i, b in enumerate(beats):
        sp = (b.get("speaker") or "").strip()
        if b.get("type") == "say" and sp in by_name:
            jobs.append((i, by_name[sp], b.get("text") or "", b.get("ban") or ""))
    if not jobs:
        return beats

    def run(job):
        _i, cid, intent, ban = job
        return _i, actor.speak(cid, intent, scene=scene, ban=ban, direction=direction, recent=recent_text)

    done = {}
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
            for i, r in ex.map(run, jobs):
                if r:
                    done[i] = r
    except Exception:
        done = {}

    # 说明：以前这里会在「只有一个角色说话」时自动补一句另一个角色的反应。
    # 那条补丁给不出上下文，弱模型手里只会说空话（实测反复冒「请问在吗？」这类填充句），
    # 已经删掉 —— 谁开口交给导演自己判断。

    out = []
    for i, b in enumerate(beats):
        r = done.get(i)
        if r:
            b = dict(b)
            b["ja"] = r.get("ja") or ""
            b["text"] = r.get("zh") or r.get("ja") or b.get("text")
            b["voice"] = True
            if r.get("emo"):
                b["emotion"] = r["emo"]
        else:
            # 角色模型没给出日语（偶尔会中文作答）：把导演给的意图翻成日语，
            # 至少保证配音有日语原文可用。
            # 只给「场上的角色」翻：玩家和旁白不翻 —— 翻了就会被某个角色的音色念出来。
            sp = (b.get("speaker") or "").strip()
            if b.get("type") == "say" and sp in by_name:
                try:
                    import translate
                    ja = translate.to_ja(b.get("text") or "") or ""
                    if ja:
                        b = dict(b)
                        b["ja"] = ja
                        b["voice"] = True
                except Exception:
                    pass
        out.append(b)
    return out


def generate(n=2, timeout=45, messages=None, auto=False):
    """生成一拍。返回 dict：{ok, beats, dropped, raw, error}

    messages：这一段对话的完整历史（前端 window.__history）。和 /api/chat 一样，
    每次请求都带上，导演和角色看到的就是「从存档读到现在的全部」。
    """
    cfg = _cfg()
    if not (cfg.get("base_url") or cfg.get("api_key")):
        return {"ok": False, "error": "导演模型没配置", "beats": []}
    p = _state()
    planned = p.get("planned") or {}
    total = planned.get("beats") or 7
    rows = _rows(messages)
    beat_index = 0
    try:
        import director
        beat_index = director._state.get("beat") or 0
    except Exception:
        pass
    payload = {
        "model": cfg.get("model") or "",
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": _user_prompt(beat_index, total, n, rows, auto)}
        ],
        "temperature": 0.85,
        "max_tokens": MAX_TOKENS.get(planned.get("length") or "small", 500),
        # 本地模型（尤其 abliterated）常先写一段散文再给 JSON：
        # 让 Ollama 语法级约束成 JSON，光靠提示词镇不住
        "json": True,
        # 有 schema 就按 schema 约束（Qwen2.5 这类模型填字段比"自由发挥"稳得多）
        "schema": BEATS_SCHEMA
    }
    try:
        r = llm.generate(payload, cfg, timeout=timeout)
        text = r.text
    except Exception as e:
        msg = getattr(e, "user_message", "") or str(e)
        # 把"打到哪个端点、用的哪个模型"写进日志：报错信息本身经常看不出是哪边的问题
        try:
            import applog
            applog.log("导演调用失败: %s | endpoint=%s model=%s kind=%s" %
                       (msg, cfg.get("base_url") or "?", cfg.get("model") or "?",
                        getattr(e, "kind", "") or "?"), "DIRECTOR")
        except Exception:
            pass
        return {"ok": False, "error": msg, "beats": []}
    # 记账：导演这一拍也算钱（以前这条路径完全没报，界面上累计一直是 0）
    usage.record(getattr(r, "model", "") or cfg.get("model") or "",
                 getattr(r, "usage", {}) or {})

    raw_beats = _parse(text)
    limit = int((planned.get("chars_per_beat") or 350) * 2)
    beats, dropped = _clean(raw_beats, limit)
    # 防复读：和最近几拍重复的句子直接剔掉（弱模型会把上一拍原样再来一遍）
    beats = _drop_repeats(beats, rows)
    # 要么是"语法合法但空"，要么整拍都在复读：换温度重试一次，
    # 并在提示里明确要求"换一个新进展"
    if not beats:
        try:
            retry = dict(payload)
            # 重试要"换一个方向"：温度调高，别让同一个分布再吐一遍同样的句子
            retry["temperature"] = 0.95
            msgs = list(retry.get("messages") or [])
            if msgs:
                msgs[-1] = dict(msgs[-1])
                msgs[-1]["content"] = (msgs[-1].get("content") or "") + (
                    "\n【换进展】\n上一条你已经写过一模一样的话了（或者是空的）。"
                    "这一拍必须写**新的**动作或新的话，不要重复。\n")
                retry["messages"] = msgs
            r2 = llm.generate(retry, cfg, timeout=timeout)
            usage.record(getattr(r2, "model", "") or cfg.get("model") or "",
                         getattr(r2, "usage", {}) or {})
            beats, dropped2 = _clean(_parse(r2.text), limit)
            dropped += dropped2
            beats = _drop_repeats(beats, rows)
            text = r2.text
        except Exception:
            pass
    # 角色生成：say 类台词交给角色自己的模型写（日语原作，中文译文给字幕）
    try:
        beats = _render_beats(beats, direction=p.get("direction") or "", rows=rows)
    except Exception:
        pass
    if not beats:
        return {"ok": False, "error": "模型没给出可用的台词", "raw": text[:400], "beats": []}
    try:
        import director
        director._state["beat"] = (director._state.get("beat") or 0) + len(beats)
    except Exception:
        pass
    return {"ok": True, "beats": beats, "dropped": dropped, "raw": text[:400]}
