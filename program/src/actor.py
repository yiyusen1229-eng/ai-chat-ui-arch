# -*- coding: utf-8 -*-
"""角色开口：导演只决定「谁、什么意图」，台词由角色自己的模型写。

上下文隔离（设计文档 6.1）：角色开口时只看得到这么多 ——

    她自己的人设（角色卡）+ 私有记忆书 + 对在场者的态度
    + 公共最近 K 轮原话 + 这一拍的任务（含红线）

没有全剧本，也没有别人的内心。谁的数据从哪来统一由 speaker.ctx 解析，
最近原话由 scenelog 给（播出去的才算），这里只负责拼提示词和调用。

返回 {ja, zh, emo}；任何一步失败就退回 None，让调用方用导演给的文本兜底。
"""
import re

import llm
import scenelog
import speaker
import usage

TIMEOUT = 40
# 一句话要不了 320 token，但 deepseek 这类「先想再说」的模型会把预算花在思考上：
# 给 160 时实测两次里有一次 content 直接是空的（思考用掉 160），角色就哑了。
MAX_TOKENS = 320

SYSTEM = """你在扮演「%s」。下面是这个角色的角色卡：

%s

现在按卡里的性格与口吻，把导演给你的意图说成一句话。
规则：
1. 第一行只写这个角色说出口的那一句；不要旁白、不要动作描写、不要括号、不要引号。
   需要时另起一行写 <emotion>情绪</emotion>，情绪只能从这些里选一个：
   calm、soft、毒舌、撒娇、哭腔、调侃吐槽、问句。拿不准就写 calm。
   例：
   ……別に。あんたが決めたことだろ。
   <emotion>毒舌</emotion>
2. **必须用日语写**（假名 + 日文汉字），不能出现简体中文的字词；
   口语，短（30 字以内），不要です・ます敬语。
3. 不要解释，不要输出多个方案，不要翻译。
4. 如果意图里有「（旁白）」之类的字样，忽略它，只说角色的话。
5. 「你记得的旧事」「你对他们现在怎么看」是你自己的记忆与心事，别人不知道，
   也不要在台词里念出来 —— 它们只影响你怎么说。
6. 「刚才发生的事」是场上公开发生的；只能据它反应，不要编造没发生的情节。
7. 「这一拍的红线」是绝对做不到的事：宁可只冷冷地回一句，也不要越线。"""

_EMO = re.compile(r"<emotion>([^<]*)</emotion>")

# 提示词里只推荐这几个；校验时也认前端 EMO_ALIAS 里的英文词，免得把挑得到的图丢掉
EMOTIONS = ("calm", "soft", "毒舌", "撒娇", "哭腔", "调侃吐槽", "问句",
            "cold", "sad", "angry", "smile", "沉重", "紧张")


def _emotion(raw):
    """只认约定的那几个词；别的宁可当没写，免得挑不到图"""
    e = (raw or "").strip().strip("「」<>")
    if not e:
        return ""
    for k in EMOTIONS:
        if e == k:
            return k
    low = e.lower()
    for k in EMOTIONS:
        if low == k.lower():
            return k
    return ""

# 最近原话给几条（可由调用方覆盖）：以前 6 条，读档后等于没有上文
RECENT_K = 20


def _present_names():
    """场上都有谁（关系只注入在场的人，不在场的不给）"""
    out = []
    try:
        import cast
        for m in cast.get_cast().get("members", []):
            n = m.get("name") or ""
            if not n and m.get("id"):
                n = speaker.name(m.get("id"))
            if n:
                out.append(n)
    except Exception:
        pass
    return out


def _story_memory(max_chars=400):
    """对话记忆书（这一段之前发生过什么）。读档后靠它接上更早的剧情。"""
    try:
        import memory
        return memory.summary_text(max_chars)
    except Exception:
        return ""


def _user_text(ctx, intent, scene, prev, ban, recent, direction=""):
    blocks = []
    if ctx.get("memory"):
        blocks.append("【你记得的旧事】\n" + ctx["memory"])
    rel = ctx.get("relations_text") or ""
    if rel:
        blocks.append("【你对他们现在怎么看】\n" + rel)
    older = _story_memory()
    if older:
        blocks.append("【更早的事】\n" + older)
    if recent:
        blocks.append("【刚才发生的事】\n" + recent)
    if scene:
        blocks.append("【场景】%s" % scene)
    if direction:
        # 玩家给的走向是「往哪儿演」，不是台词；写清楚，免得角色把它念出来
        blocks.append("【这一场往哪儿演】%s\n（这是玩家给的方向，不是台词；照它演，"
                      "但不要把它说出来）" % direction)
    if not scene and not direction:
        blocks.append("【场景】（没说，顺着当前气氛）")
    if ban:
        blocks.append("【这一拍的红线】%s" % ban)
    last = intent or ""
    if prev and prev.strip() and prev.strip() not in (recent or ""):
        last += "\n【对方上一句】%s" % prev.strip()[:80]
    blocks.append("【导演给的意图】%s" % last)
    return "\n\n".join(blocks)


def _clean(text):
    t = (text or "").strip()
    t = _EMO.sub("", t).strip()
    t = t.strip("「」『』\"'` \n　")
    t = t.split("\n")[0].strip()
    t = re.sub(r"^[（(【].*?[）)】]$", "", t).strip()
    return t


def _log(msg):
    """失败要说一声：不吭声的话，角色被导演代写也没人知道"""
    try:
        import applog
        applog.log(msg, "ACTOR")
    except Exception:
        pass


def _reasoning_model(cfg):
    """会「先想再说」的模型（主对话路径也是这么判的，别两套标准）"""
    s = ((cfg.get("base_url") or "") + " " + (cfg.get("model") or "")).lower()
    return "deepseek" in s or "pro" in s


def speak(cid, intent, scene="", prev="", timeout=TIMEOUT, ban="", recent=None,
          direction=""):
    """让角色自己说一句。返回 {ja, zh, emo}；失败返回 None。

    recent：最近原话（字符串）。不传就自己从 scenelog 取最近 RECENT_K 条，
            显式传（含空串）就以传入的为准 —— 自测不用真起队列。
    ban：这一拍的红线，导演给的；空串表示没有。
    direction：玩家给的走向（「往哪儿演」），是要求不是台词，单独标出来。
    """
    ctx = speaker.ctx(cid, present=_present_names())
    cfg = ctx.get("model") or {}
    if not ctx.get("card"):
        return None
    if not cfg.get("base_url") or not cfg.get("model"):
        _log("角色 %s 没有可用模型，这句只能让导演代写" % cid)
        return None
    if recent is None:
        recent = scenelog.as_text(RECENT_K)
    sys_msg = SYSTEM % (ctx.get("name") or cid, ctx["card"])
    user = _user_text(ctx, intent, scene, prev, ban, recent, direction)
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": user}],
        "stream": False,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.9,
    }
    if _reasoning_model(cfg):
        # 和主对话一致：不关掉思考，思考会把 max_tokens 吃光，content 是空的
        payload["thinking"] = {"type": "disabled"}
        payload["reasoning_effort"] = "none"
    try:
        r = llm.generate(payload, {"base_url": cfg.get("base_url") or "",
                                   "api_key": cfg.get("api_key") or "",
                                   "model": cfg.get("model") or ""},
                         timeout=timeout)
        out = r.text or ""
    except Exception as e:
        _log("角色 %s 没写出日语：%s" % (cid, getattr(e, "user_message", "") or e))
        return None
    # 记账：多角色这条路的每一句都要算进右下角的累计（以前这里根本没报）
    usage.record(getattr(r, "model", "") or cfg.get("model") or "",
                 getattr(r, "usage", {}) or {})
    ja = _clean(out)
    if not ja:
        _log("角色 %s 返回空台词，这句让导演代写" % cid)
        return None
    # 必须是日语：一个假名都没有，说明模型用中文答了，判失败
    if not re.search(r"[\u3040-\u30ff]", ja):
        _log("角色 %s 用中文作答，判失败：%s" % (cid, ja[:40]))
        return None
    emo = ""
    m = _EMO.search(out)
    if m:
        emo = _emotion(m.group(1))
    zh = ""
    try:
        import translate
        zh = translate.to_zh(ja) or ""
    except Exception:
        zh = ""
    return {"ja": ja, "zh": zh or ja, "emo": emo}

