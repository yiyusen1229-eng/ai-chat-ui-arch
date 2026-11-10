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

# 一拍允许的最大输出（和 LENGTHS 里的 tokens 对齐，留一点余量）
MAX_TOKENS = {"small": 900, "medium": 1400, "large": 2200}


def _cfg():
    """导演模型配置：优先角色自己的 director 段，没有就用全局 chat"""
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


SYSTEM = """你是这出戏的导演。你不扮演任何角色，只决定「这一拍谁开口、说什么」。

规则：
1. 只输出 JSON，不要解释、不要 Markdown 代码块。
2. 每个角色都要按他自己的角色卡说话，不要替他说超出人设的话。
3. 台词要短；旁白只写必要的一两句。
4. speaker 必须是阵容里的名字；**叙述、环境、动作、心理描写一律用空字符串当旁白**，
   不要塞进某个角色的台词里（角色台词只写他说出口的话）。
5. 龙套（临时角色）只接话，不推动剧情。
6. 玩家也要有戏：每两三拍至少给玩家写一句（speaker 用玩家的名字），
   写短一点，像玩家会说的话；不要把玩家的台词写成旁白。
7. 在场有两个以上角色时，**这一拍至少让两个不同角色各说一句**，
   不要整拍只有一个人回话。"""


def _user_prompt(beat_index, total_beats):
    p = _state()
    spec = p.get("beats") or 7
    chars_per = p.get("chars_per_beat") or 350
    n_beats = 2 if beat_index + 2 <= total_beats else max(1, total_beats - beat_index)
    return """【本场阵容】
%s

【玩家给的走向】
%s

【现在的位置】
第 %d 拍，共 %d 拍；这一拍大约 %d 字。
只写 %d 条（不要多写），每条不超过 %d 字，保持自然接续。

【输出格式】
{"beats":[{"speaker":"角色名或空","type":"say 或 narr","text":"台词或旁白"}]}""" % (
        _cast_lines(), p.get("direction") or "（玩家没给，顺着当前气氛往下走）",
        beat_index + 1, total_beats, chars_per, n_beats, chars_per)


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


def _clean(beats, limit):
    """"校验说话人 + 截断长度；返回 (beats, 丢弃数)"""
    allow = _allowed()
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
                    "voice": bool(sp)})
    return out, dropped


def _render_beats(beats, scene=""):
    """把 say 类台词换成角色自己写的那一句（并发调用各角色模型）。"""
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
    jobs = []
    for i, b in enumerate(beats):
        sp = (b.get("speaker") or "").strip()
        if b.get("type") == "say" and sp in by_name:
            jobs.append((i, by_name[sp], b.get("text") or ""))
    if not jobs:
        return beats

    def run(job):
        _i, cid, intent = job
        return _i, actor.speak(cid, intent, scene=scene)

    done = {}
    try:
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as ex:
            for i, r in ex.map(run, jobs):
                if r:
                    done[i] = r
    except Exception:
        done = {}

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


def generate(n=2, timeout=45):
    """生成一拍。返回 dict：{ok, beats, dropped, raw, error}"""
    cfg = _cfg()
    if not (cfg.get("base_url") or cfg.get("api_key")):
        return {"ok": False, "error": "导演模型没配置", "beats": []}
    p = _state()
    planned = p.get("planned") or {}
    total = planned.get("beats") or 7
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
            {"role": "user", "content": _user_prompt(beat_index, total)}
        ],
        "temperature": 0.85,
        "max_tokens": MAX_TOKENS.get(planned.get("length") or "small", 500)
    }
    try:
        r = llm.generate(payload, cfg, timeout=timeout)
        text = r.text
    except Exception as e:
        msg = getattr(e, "user_message", "") or str(e)
        return {"ok": False, "error": msg, "beats": []}

    raw_beats = _parse(text)
    limit = int((planned.get("chars_per_beat") or 350) * 2)
    beats, dropped = _clean(raw_beats, limit)
    # 角色生成：say 类台词交给角色自己的模型写（日语原作，中文译文给字幕）
    try:
        beats = _render_beats(beats, p.get("direction") or "")
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
