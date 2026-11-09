# -*- coding: utf-8 -*-
"""翻译一层：术语表 + 云端 / 本地两种引擎 + 兜底链。

为什么单独拆一层（原来 ja→zh 只有本地 Hy-MT2 一条路，`api_chat` 直接调
`tts_client.translate_via_service`）：

1. **人名一律按音译错**：`はるき` → 「春树」（应该是「春希」）。本地和云端**都会错**，
   所以光换引擎没用，必须有**术语表**定死译名。
2. **云端小 token 会把预算烧在 reasoning 上**：实测 `max_tokens=150` 时
   `finish_reason=length`、`content` 是空串（三句里一句）。所以走云端必须
   `reasoning_effort=none` + `max_tokens=300`（实测出词 7~9 token，又对又便宜）。
3. 谷歌免费端点（`translate.googleapis.com/gtx`）**实测 429**，不可用；
   官方 Cloud Translation API 要计费 key，不是「接上就能用」的东西。

引擎怎么选（`provider()`）：
    WA2_MT_PROVIDER=cloud|local  →  config.json 的 `translate.provider`
    →  自动：有云端 key 就用云端，没有就本地。

`to_zh()` / `to_ja()` 是给外面用的两个入口，内部保证：云端失败回落本地，
本地再失败才回空串（调用方照旧处理空串）。
"""

import json
import os
import re
import urllib.request

CACHE = {}
CACHE_MAX = 500

# 云端翻译的 token 预算：实测 reasoning_effort=none 时一句只要 7~9 token，
# 300 是给长台词留的余量；给太小会出现 finish_reason=length + 空 content。
CLOUD_MAX_TOKENS = 300
CLOUD_TIMEOUT_S = 20

PROMPT = {
    "zh": ("把下面这句日文台词翻成中文。要求：只输出中文译文；口语、贴近角色语气；"
           "不解释、不加引号、不输出原文；人名按术语表给的字写。"),
    "ja": ("把下面这句中文台词翻成日语。要求：只输出日语译文；用角色自己的口气"
           "（あたし、〜だ／〜だろ、常省略主语），不要です・ます敬语；"
           "不加引号、不解释。"),
}

_JA_KANA = re.compile(r"[\u3040-\u30ff]")


# ---------------------------------------------------------------- 配置

def _src_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _cloud_cfg():
    """云端配置：环境变量里的 key 优先（和 main.cloud_cfg 一个口径）。"""
    p = os.path.join(_src_dir(), "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}
    key = (os.environ.get("WA2_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
           or d.get("api_key") or "")
    return {"key": key, "base": (d.get("base_url") or "").rstrip("/"),
            "model": d.get("model") or ""}


def provider():
    """cloud / local"""
    env = (os.environ.get("WA2_MT_PROVIDER") or "").strip().lower()
    if env in ("cloud", "local", "mt"):
        return env
    p = os.path.join(_src_dir(), "config.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        v = ((d.get("translate") or {}).get("provider") or "").strip().lower()
        if v in ("cloud", "local", "mt"):
            return v
    except Exception:
        pass
    return "cloud" if _cloud_cfg()["key"] else "local"


def status():
    """这一轮翻译实际怎么走（给设置界面显示，别让面板说假话）。

    引擎三种：
      cloud  云端（复用对话那套 base_url / key / model）
      local  本地 Hy-MT2（tts_client 里的服务）
      mt     「模型管理 → 翻译」那块单独配的端点（**默认不启用**，要显式选才走）

    只在 `translate.provider == "mt"` 时才用 mt；否则那个板块只是备用配置。
    """
    prov = provider()
    cc = _cloud_cfg()
    cloud_ready = bool(cc["key"] and cc["base"])
    local_ready = False
    local_detail = "未检测"
    try:
        import tts_client
        local_ready = bool(tts_client.available())
        local_detail = "可用" if local_ready else "没检测到本地 Hy-MT2 服务"
    except Exception as e:
        local_detail = "检测失败：%s" % e
    mt = {}
    try:
        with open(os.path.join(_src_dir(), "config.json"), encoding="utf-8") as f:
            d = json.load(f)
        mt = ((d.get("models") or {}).get("mt") or {})
    except Exception:
        mt = {}
    mt_ready = bool((mt.get("endpoint") or "").strip() and (mt.get("model") or "").strip())
    engines = [
        {"key": "cloud", "name": "云端（对话模型）", "ready": cloud_ready,
         "detail": ("用 %s" % (cc["model"] or "对话模型")) if cloud_ready else "没配 key / 端点"},
        {"key": "local", "name": "本地 Hy-MT2", "ready": local_ready, "detail": local_detail},
        {"key": "mt", "name": "翻译专用端点", "ready": mt_ready,
         "detail": (mt.get("endpoint") or "未配置") if mt_ready else "未配置（备用）"},
    ]
    label = {"cloud": "云端（对话模型）", "local": "本地 Hy-MT2", "mt": "翻译专用端点"}
    return {"provider": prov, "engine": prov, "label": label.get(prov, prov),
            "engines": engines,
            "cloud_ready": cloud_ready, "local_ready": local_ready, "mt_ready": mt_ready,
            "mt_in_use": prov == "mt"}


# ---------------------------------------------------------------- 术语表

def glossary_path():
    """当前角色的术语表：`characters/<角色>/translation_glossary.json`

    结构（两种都认，只写要纠正的）：
        {"to_zh": {"はるき": "春希", "せつな": "雪菜"}}   或扁平 {"春树": "春希"}
    """
    try:
        import characters
        d = characters.active_dir()
        return os.path.join(d, "translation_glossary.json") if d else ""
    except Exception:
        return ""


def glossary(p="zh", path=None):
    """读术语表。

    故意**不缓存**：文件很小（几百字节），但按 mtime 缓存会在「同一秒内改文件」时
    读到旧内容（自测当场抓到过）。翻译结果本身有缓存，这里每次读盘代价可以忽略。
    """
    gl = path if path is not None else glossary_path()
    if not gl or not os.path.isfile(gl):
        return {}
    try:
        with open(gl, encoding="utf-8") as f:
            d = json.load(f)
        table = {}
        if isinstance(d, dict):
            for k, v in d.items():
                # 允许 {"to_zh": {...}} 也允许 {"ja": {...}}；扁平结构默认算 to_zh
                lang = k[3:] if k.startswith("to_") else k
                if isinstance(v, dict):
                    table[lang] = {str(a): str(b) for a, b in v.items()}
                else:
                    table.setdefault("zh", {})[str(k)] = str(v)
        return table.get(p, {})
    except Exception:
        return {}


def apply_glossary(text, p="zh", path=None):
    """把术语表里的错译换回正确写法。

    **一遍过**（单个正则，长键优先）：逐条 replace 会自我覆盖 —— 例如同时有
    「春树→春希」和「春→X」时，「春树」先变「春希」、紧接着「春」又把里面的「春」
    换成 X，得到「X希」。自测就是被这一条抓到的。
    """
    out = text or ""
    table = glossary(p, path) or {}
    if not out or not table:
        return out
    keys = sorted([k for k in table if k], key=len, reverse=True)
    if not keys:
        return out
    rx = re.compile("|".join(re.escape(k) for k in keys))
    return rx.sub(lambda m: table.get(m.group(0), m.group(0)), out)


# ---------------------------------------------------------------- 引擎

def _local_raw(text, p="zh"):
    """本地 Hy-MT2 服务（tts_client）+ 它自己的兜底；失败返回空串"""
    try:
        import tts_client
        return (tts_client.translate_via_service(text, p) or "").strip()
    except Exception:
        return ""


def _cloud_raw(text, p="zh"):
    """云端 LLM 翻译；返回 (译文, 错误)。空译文也算失败（finish_reason=length 会这样）"""
    cfg = _cloud_cfg()
    if not cfg["key"] or not cfg["base"] or not cfg["model"]:
        return "", "没配云端"
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": PROMPT.get(p, PROMPT["zh"])},
                     {"role": "user", "content": text}],
        "stream": False,
        "max_tokens": CLOUD_MAX_TOKENS,
        "reasoning_effort": "none",     # 不禁用的话 150 token 可能全被 reasoning 吃掉
    }
    try:
        rq = urllib.request.Request(
            cfg["base"] + "/v1/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + cfg["key"]})
        with urllib.request.urlopen(rq, timeout=CLOUD_TIMEOUT_S) as r:
            d = json.loads(r.read().decode("utf-8"))
        out = ((d["choices"][0].get("message") or {}).get("content") or "").strip()
        return out, "" if out else "云端返回空"
    except Exception as e:
        return "", str(e)[:80]


def _clean(text, p="zh"):
    """去掉引号 / 前缀说明，只留一句译文"""
    t = (text or "").strip()
    t = re.sub(r"^(译文|翻译|日本語|中文)[:：]\s*", "", t)
    t = t.strip("「」『』\"'` \n")
    # 日文方向：只认带假名的行，顺手丢掉解释性尾巴
    if p == "ja":
        for line in t.split("\n"):
            line = line.strip()
            if line and _JA_KANA.search(line):
                return line.strip("「」『』")
        return ""
    return t.split("\n")[0].strip()


# 模型有时会把提示词里的词当成内容吐出来（实测出现过「术语表：」）
_PROMPT_ECHO = ("术语表", "人名按", "只输出", "把下面", "要求：", "翻译成")
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _looks_like_junk(out, src, p="zh"):
    """译文体检：空、纯标点、提示词回声、跟原文一模一样 都算失败"""
    s = (out or "").strip()
    if not s:
        return True
    core = re.sub(r"[\s，。！？、；：…—·「」『』“”‘’（）()]", "", s)
    if not core:
        return True
    if any(s.startswith(k) or (k in s and len(s) <= len(k) + 3) for k in _PROMPT_ECHO):
        return True
    if p == "zh":
        if not _CJK.search(s):          # 一个汉字都没有：多半是英文/原文
            return True
        if s == (src or "").strip():    # 原样返回＝没翻
            return True
    else:
        if not _JA_KANA.search(s):
            return True
    return False


def translate(text, p="zh"):
    """翻一句；返回译文（失败回空串）。引擎顺序按 provider()，另一个当兜底。"""
    t = (text or "").strip()
    if not t:
        return ""
    key = (p, t)
    if key in CACHE:
        return CACHE[key]

    order = ["cloud", "local"] if provider() == "cloud" else ["local", "cloud"]
    out = ""
    for engine in order:
        out = _clean(_cloud_raw(t, p)[0] if engine == "cloud" else _local_raw(t, p), p)
        if out and _looks_like_junk(out, t, p):
            out = ""            # 体检不过：当这台引擎失败，换下一台
        if out:
            break
    out = apply_glossary(out, p)
    if out and len(CACHE) < CACHE_MAX:
        CACHE[key] = out
    return out


def to_zh(text):
    """日 → 中（对话框字幕 / 中文配音）"""
    return translate(text, "zh")


def to_ja(text):
    """中 → 日（日文配音；有 speak_ja 时不走这条）"""
    return translate(text, "ja")
