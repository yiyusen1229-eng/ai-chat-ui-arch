# -*- coding: utf-8 -*-
"""选择分支：让对话模型根据当前对话给出 2~5 个玩家可选行动（galgame 那种分歧）。

    POST /api/choice {"count": 5, "messages": [...], "have": ["放开手", "把脸背过去"]}
      → {"ok": true, "options": [5 条], "count": 5, "have": 2, "generated": 3}

为什么单独一个接口、而不是塞进 /api/chat：
  · 选项要「短、可点」，和正文回复是两种输出，混在一起容易把选项写进台词里；
  · 生成选项时不带人设大提示，只给最近几轮对话 —— 省 token，也不让模型开始演她。

**同一个对话点不重复生成**（2026-11-22 加）：
  · 缓存由前端按「对话点」持有（key = 历史条数 + 最后一条内容，见 wt-choices.js），
    面板关掉不选再打开 → 直接把上次那几条摆回来，**一个请求都不发**；
  · 用户把数量从 2 改成 5 时，前端把已有的 `have` 一起带上来，后端只补差额
    `need = count - len(have)`，并且把已有选项写进提示词让模型别重复 —— 5 条里
    前 2 条还是原来那 2 条；
  · 后端这一层只管「算差额 / 去重 / 拼接」，不自己存缓存（缓存跟着对话点走，
    对话一推进前端 key 就变了，服务端存反而容易留旧货）。
"""
import json
import re
import urllib.request

import applog

MAX_COUNT = 5
MIN_COUNT = 2
KEEP_MSGS = 8              # 只看最近 8 条，够判断语境
MAX_TOKENS = 400
TIMEOUT_S = 25

PROMPT = (
    "你是 galgame（视觉小说）的编剧。根据下面这段对话，给出 {n} 个玩家此刻可以做的选择，"
    "要能明显把故事带向不同方向。\n"
    "要求：\n"
    "1. 每行一个，只写选项本身（第一人称的动作或一句短台词），不要编号、不要解释；\n"
    "2. 每个 4~14 个汉字，像游戏里的选项那样短；\n"
    "3. 不要写成她的台词，写「玩家（北原春希）会做的事」；\n"
    "4. 风格要和上下文一致（亲密 / 日常 / 争执），不要突然转成别的气氛。\n"
    "只输出 {n} 行。"
)

# 行首可能带的编号/项目符号：1. 1、 1) - ・ ①
_BULLET = re.compile(r"^\s*(?:[0-9１-５]+[\.、\)．:：]|[-–—・*·]+|[①-⑤])\s*")
_QUOTES = "「」『』\"'“”‘’"


def clamp_count(n):
    """数量夹到 2~5（模型/设置/存档给什么都不许越界）"""
    try:
        return max(MIN_COUNT, min(MAX_COUNT, int(n or MIN_COUNT)))
    except Exception:
        return MIN_COUNT


def clean_option(s):
    """把一行收拾成纯选项文本"""
    t = (s or "").strip()
    t = _BULLET.sub("", t).strip()
    t = t.strip(_QUOTES).strip()
    t = re.sub(r"\s+", " ", t)
    return t


def _key(t):
    """判断「是不是同一条选项」用的归一化键（省略号/句号/空格不算差别）"""
    return (t or "").replace("…", "").replace("。", "").replace(" ", "").strip()


def parse_options(text, count=MIN_COUNT, avoid=None):
    """模型输出 → 选项列表（去编号、去空行、去重复、截到 count 个）

    avoid：已经有了的选项（增量生成时传进来），命中的直接跳过，不重复收。
    """
    n = clamp_count(count)
    out, seen = [], set()
    for a in (avoid or []):
        if isinstance(a, str):
            seen.add(_key(a))
    for line in (text or "").split("\n"):
        t = clean_option(line)
        if not t or len(t) < 2 or len(t) > 30:
            continue
        k = _key(t)
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= n:
            break
    return out


def _cloud_cfg():
    """复用主程序的模型路由（2026-11-23：跟聊天一致 → 角色配置优先，其次全局）

    以前这里直接拿 main.cloud_cfg()（全局 config.json），用户把聊天换到
    OpenCode 之后，选项分支还在按 DeepSeek 官网价烧。
    """
    try:
        import main
        cfg = main.chat_route() or {}
    except Exception:
        cfg = {}
    return {"key": cfg.get("api_key") or cfg.get("key") or "",
            "base": (cfg.get("base_url") or cfg.get("base") or "").rstrip("/"),
            "model": cfg.get("model") or ""}


def _recent(messages):
    """压成「角色：内容」的几行，只要正文"""
    rows = []
    for m in (messages or [])[-KEEP_MSGS:]:
        if not isinstance(m, dict):
            continue
        role = "玩家" if m.get("role") == "user" else "和纱"
        txt = (m.get("content") or "").strip()
        if not txt:
            continue
        rows.append("%s：%s" % (role, txt[:200]))
    return "\n".join(rows)


def prompt_for(need, avoid=None):
    """这一轮真正发给模型的 system：只要求 need 条，并点名别重复已有的"""
    p = PROMPT.format(n=need)
    keep = [a for a in (avoid or []) if isinstance(a, str) and a.strip()][:MAX_COUNT]
    if keep:
        p += ("\n注意：下面这些选择**已经有了**，不要重复、也不要换个说法，"
              "要给方向明显不同的新选择：" + "、".join(keep))
    return p


def _ask(ctx, need, avoid=None):
    """调云端模型；返回 (原始文本, 错误)。

    测试里把这个函数换掉就能验「增量生成」而不联网（见 tools/test_choice.py）。
    """
    cfg = _cloud_cfg()
    if not (cfg["key"] and cfg["base"] and cfg["model"]):
        return "", "没配云端模型"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": prompt_for(need, avoid)},
                     {"role": "user", "content": ctx}],
        "stream": False,
        "max_tokens": MAX_TOKENS,
        "reasoning_effort": "none",
    }
    try:
        rq = urllib.request.Request(
            cfg["base"] + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["key"]})
        with urllib.request.urlopen(rq, timeout=TIMEOUT_S) as r:
            d = json.loads(r.read().decode("utf-8"))
        return ((d["choices"][0].get("message") or {}).get("content") or "").strip(), ""
    except Exception as e:
        applog.log("选择分支：模型调用失败 %s" % e, "WARN")
        return "", str(e)[:80]


def generate(messages, count=MIN_COUNT, have=None):
    """返回 (完整选项列表, 错误)。

    have = 这个对话点已经生成过的那几条：
      · 已经够 count → 直接返回前 count 条，**不调模型**（这就是「关掉再打开不重新生成」）；
      · 不够 → 只让模型补 count - len(have) 条，并把已有的告诉它别重复；
      · 模型失败 → 把 have 原样返回（前端还能摆出来），错误照样带出去。
    """
    n = clamp_count(count)
    keep = [t for t in (have or []) if isinstance(t, str) and t.strip()][:n]
    need = n - len(keep)
    if need <= 0:
        return keep, ""
    ctx = _recent(messages)
    if not ctx:
        return keep, "没有可参考的对话"
    raw, err = _ask(ctx, need, keep)
    if err:
        return keep, err
    new = parse_options(raw, need, avoid=keep)
    if not new:
        return keep, ("模型没给出新选项" if keep else "模型没给出够多的选项")
    if len(new) < need:
        # 补不满也要把旧的留住：前端照样摆，只是提示「只补到 N 个」
        return keep + new, "只补到 %d 个（要 %d 个）" % (len(new), need)
    return keep + new, ""


class ChoiceApi:
    """见 main.Handler。"""

    def _choice(self):
        try:
            body = self._body()
        except Exception as e:
            return self._json({"ok": False, "error": "bad body: %s" % e}, 400)
        if not isinstance(body, dict):
            return self._json({"ok": False, "error": "bad body"}, 400)
        count = clamp_count(body.get("count") or MIN_COUNT)
        have = body.get("have")
        if not isinstance(have, list):
            have = []
        have = [h for h in have if isinstance(h, str) and h.strip()][:count]
        opts, err = generate(body.get("messages") or [], count, have)
        ok = len(opts) >= MIN_COUNT
        out = {
            "ok": ok,
            "options": opts,
            "count": len(opts),
            "have": len(have),
            "generated": max(0, len(opts) - len(have)) if ok else 0,
        }
        if err:
            out["error"] = err
        return self._json(out)
