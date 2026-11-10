# -*- coding: utf-8 -*-
"""角色开口：导演只决定「谁、什么意图」，台词由角色自己的模型写。

设计（四态矩阵里的「角色生成」）：
  导演产出 beats：{speaker, type, text}，其中 say 的 text 当成「意图」；
  这里拿那个角色的角色卡当 system，让**他自己的模型**用他的口吻说一句日语；
  再翻成中文给字幕用，日语原文给配音用。

返回 {ja, zh, emo}；任何一步失败就退回 None，让调用方用导演给的文本兜底。
"""
import json
import os
import re
import urllib.request

TIMEOUT = 40

SYSTEM = """你在扮演「%s」。下面是这个角色的角色卡：

%s

现在按卡里的性格与口吻，把导演给你的意图说成一句话。
规则：
1. 只输出这个角色说出口的那一句；不要旁白、不要动作描写、不要括号、不要引号。
2. **必须用日语写**（假名 + 日文汉字），不能出现简体中文的字词；
   口语，短（30 字以内），不要です・ます敬语。
   例：……別に。あんたが決めたことだろ。
3. 不要解释，不要输出多个方案，不要翻译。
4. 如果意图里有「（旁白）」之类的字样，忽略它，只说角色的话。"""

_EMO = re.compile(r"<emotion>([^<]*)</emotion>")


def _char_dir(cid):
    try:
        import characters
        root = characters.root()
        if not cid:
            return ""
        d = os.path.join(root, cid)
        if os.path.isdir(d):
            return d
        for name in os.listdir(root):
            try:
                if characters.display_name(name) == cid:
                    return os.path.join(root, name)
            except Exception:
                continue
    except Exception:
        pass
    return ""


def _card(cid):
    d = _char_dir(cid)
    p = os.path.join(d, "card", "director.md") if d else ""
    try:
        if p and os.path.isfile(p):
            return open(p, encoding="utf-8").read().strip()[:1200]
    except Exception:
        pass
    return ""


def _cfg(cid):
    """这个角色的聊天模型；没配就用全局。"""
    d = _char_dir(cid)
    try:
        p = os.path.join(d, "model.json") if d else ""
        if p and os.path.isfile(p):
            raw = json.loads(open(p, encoding="utf-8").read())
            sec = raw.get("chat") or {}
            if sec.get("key") or sec.get("endpoint"):
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


def _clean(text):
    t = (text or "").strip()
    t = _EMO.sub("", t).strip()
    t = t.strip("「」『』\"'` \n　")
    t = t.split("\n")[0].strip()
    t = re.sub(r"^[（(【].*?[）)】]$", "", t).strip()
    return t


def speak(cid, intent, scene="", prev="", timeout=TIMEOUT):
    """让角色自己说一句。返回 {ja, zh, emo}；失败返回 None。"""
    card = _card(cid)
    cfg = _cfg(cid)
    if not card or not cfg.get("base_url") or not cfg.get("model"):
        return None
    sys_msg = SYSTEM % (cid, card)
    user = "【场景】%s\n【导演给的意图】%s" % (scene or "（没说，顺着当前气氛）", intent or "")
    if prev:
        user += "\n【对方上一句】%s" % prev[:80]
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": sys_msg},
                     {"role": "user", "content": user}],
        "stream": False,
        "max_tokens": 160,
        "temperature": 0.9,
    }
    base = cfg["base_url"].rstrip("/")
    url = base + ("/v1/chat/completions" if not base.endswith("/v1") else "/chat/completions")
    try:
        rq = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                    headers={"Content-Type": "application/json",
                                             "Authorization": "Bearer " + cfg["api_key"]})
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = ((d["choices"][0].get("message") or {}).get("content") or "")
        ja = _clean(out)
        if not ja:
            return None
        # 必须是日语：一个假名都没有，说明模型用中文答了，判失败
        import re as _re
        if not _re.search(r"[\u3040-\u30ff]", ja):
            return None
        emo = ""
        m = _EMO.search(out)
        if m:
            emo = (m.group(1) or "").strip()
        zh = ""
        try:
            import translate
            zh = translate.to_zh(ja) or ""
        except Exception:
            zh = ""
        return {"ja": ja, "zh": zh or ja, "emo": emo}
    except Exception:
        return None
