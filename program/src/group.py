# -*- coding: utf-8 -*-
"""多角色同场（Group Chat）—— 最小可用版

设计（对应 2026-11 路线图第 14 条，P3 提前做）：
  · 同场角色 = characters/ 下任意几个角色文件夹，当前角色必在场；
  · 发言调度：不单独训练仲裁器，交给模型自己挑下一位（提示词里写明「不要同一个人连说两次」），
    解析出来的 speaker 再由前端决定配音/停留；
  · 公共上下文：同场的对话都写进同一份历史（这就是"共享场景"）；
    各自的记忆书仍是私有的（/api/chat 只注入当前角色的），不做跨角色记忆混流；
  · 关系网：先用 group.json 里每个角色的一句话"此刻态度"注入，不建图。

输出约定（与自动续写一致）：
    名字：「台词」    → 该角色说话
    其它（无引号）    → 旁白
"""
import io
import json
import os

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", "characters"))


def cast_root():
    """角色根目录（用户数据目录下的 characters/）"""
    try:
        import characters
        fn = getattr(characters, "root", None)
        if callable(fn):
            p = fn() or ""
            if p:
                return p
    except Exception:
        pass
    return _ROOT


def list_cast():
    """可选角色（有 name.txt 的文件夹）"""
    out = []
    root = cast_root()
    try:
        names = sorted(os.listdir(root))
    except Exception:
        return out
    for n in names:
        p = os.path.join(root, n)
        if not os.path.isdir(p):
            continue
        name_file = os.path.join(p, "name.txt")
        label = n
        try:
            if os.path.isfile(name_file):
                t = io.open(name_file, encoding="utf-8").read().strip()
                if t:
                    label = t
        except Exception:
            pass
        out.append({"id": n, "name": label,
                    "voice": os.path.isdir(os.path.join(p, "voice"))})
    return out


def _cast_line(c):
    extra = c.get("note") or ""
    return "· %s%s" % (c.get("name") or c.get("id"), ("（" + extra + "）") if extra else "")


def build_block(cast, story="", current=""):
    """拼一段"同场 + 剧情"的 system 说明；cast 为空时返回空串"""
    cast = [c for c in (cast or []) if c]
    if not cast:
        return ""
    names = [c.get("name") or c.get("id") for c in cast]
    cur = current or (names[0] if names else "")
    parts = ["\n【同场角色】现在同一个场景里有：%s。" % "、".join(names)]
    parts.append("· 当前主视角角色：%s（台词默认给她，除非剧情需要别人接话）。" % cur)
    parts.append("· 一次只让**一个**角色说话；不要同一个人连着说两次以上，也不要每句都换人。")
    parts.append("· 台词写成 名字：「台词」；旁白/动作/环境直接写，不加引号。")
    parts.append("· 其他角色的性格与说话方式按他们自己的设定来，不要写成%s的口气。" % cur)
    if story:
        parts.append("\n【剧情把控】%s" % story)
    return "\n".join(parts) + "\n"


def parse_line(text, cast=None, current=""):
    """把一行解析成 {speaker, line_type, text}

    line_type: her（当前角色，可配音）/ other（其他角色）/ narr（旁白）
    """
    s = (text or "").strip()
    if not s:
        return {"speaker": "", "line_type": "narr", "text": ""}
    names = set()
    for c in (cast or []):
        if c.get("name"):
            names.add(c["name"])
        if c.get("id"):
            names.add(c["id"])
    if current:
        names.add(current)
    # 名字：「台词」
    import re
    m = re.match(r"^([^：:「『\n]{1,8})[：:]\s*[「『](.*?)[」』]\s*$", s, re.S)
    if m:
        who = m.group(1).strip()
        body = m.group(2).strip()
        if who in names:
            return {"speaker": who,
                    "line_type": "her" if (current and who == current) else "other",
                    "text": body}
        return {"speaker": who, "line_type": "other", "text": body}
    # 纯台词（没写名字）：默认当前角色
    if s[:1] in ("「", "『"):
        body = s.strip("「」『』").strip()
        return {"speaker": current or "", "line_type": "her" if current else "other", "text": body}
    # 旁白
    return {"speaker": "", "line_type": "narr", "text": s}


def load_prefs():
    """同场角色 / 剧情方向（右键菜单里设的），存在用户目录 group.json"""
    p = os.path.join(cast_root(), "..", "group.json")
    try:
        d = json.load(io.open(p, encoding="utf-8"))
        return {"cast": d.get("cast") or [], "story": d.get("story") or ""}
    except Exception:
        return {"cast": [], "story": ""}


def save_prefs(cast=None, story=None):
    p = os.path.normpath(os.path.join(cast_root(), "..", "group.json"))
    d = load_prefs()
    if cast is not None:
        d["cast"] = [str(x) for x in cast]
    if story is not None:
        d["story"] = str(story)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        io.open(p, "w", encoding="utf-8").write(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass
    return d
