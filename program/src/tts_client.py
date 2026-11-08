# -*- coding: utf-8 -*-
"""语音合成客户端

程序启动时拉起 GPT-SoVITS 的 API 服务（用它的独立 python 运行时），
之后每次合成走本地 HTTP 请求。

若 GPT-SoVITS 目录或模型不存在，则整体降级为"无语音"，不影响聊天。
"""
import atexit
import io
import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import char_res
import audio_gain
import cache_io
import diag

BASE = os.path.dirname(os.path.abspath(__file__))
def _find_gsv():
    cands = [
        os.path.join(os.path.dirname(BASE), "tts", "GPT-SoVITS"),
        os.path.join(os.path.dirname(os.path.dirname(BASE)), "tts", "GPT-SoVITS"),
        # 整理后的位置：<项目根>/other/dev/tts/GPT-SoVITS
        os.path.join(os.path.dirname(os.path.dirname(BASE)), "other", "dev", "tts", "GPT-SoVITS"),
        os.path.join(os.path.dirname(os.path.dirname(BASE)), "dev", "tts", "GPT-SoVITS"),
    ]
    for c in cands:
        if os.path.exists(os.path.join(c, "api_v2.py")):
            return c
    return cands[0]


GSV = _find_gsv()
DATA = os.path.join(os.path.dirname(GSV), "..", "语音训练集")
API_PY = os.path.join(GSV, "api_v2.py")
PY = os.path.join(GSV, "runtime", "python.exe")

HOST = "127.0.0.1"
PORT = 9880
T_PORT = 9881
# 等 GPT-SoVITS 起来的超时（第一次要加载模型，给足）；单句合成超时
READY_TIMEOUT_S = 120
SYNTH_TIMEOUT_S = 60
T_PY = os.path.join(os.path.dirname(GSV), "翻译服务.py")
_tproc = None
URL = "http://%s:%d" % (HOST, PORT)


def endpoint():
    """从 URL 解析出 (host, port) —— URL 是唯一事实来源。

    设置界面「语音」板块填的端点会写进 tts_client.URL（main.apply_model_cfg），
    所以探活也必须从 URL 算，不能写死 127.0.0.1：否则把语音服务放在另一台机器上时，
    程序会一直去摸本机，报“没有语音”或者“复用失败”。
    """
    try:
        from urllib.parse import urlparse
        u = urlparse(URL or "")
        host = u.hostname or HOST
        port = int(u.port or PORT)
        return host, port
    except Exception:
        return HOST, PORT


def is_remote():
    """语音服务在别的机器上？（127.0.0.1 / localhost / ::1 之外都算）"""
    return endpoint()[0] not in ("127.0.0.1", "localhost", "::1", "")


def bind_host():
    """本机启动 GPT-SoVITS 时监听的地址。

    默认只听本机（安全）；要给局域网/其他设备用，设环境变量 WA2_TTS_BIND=0.0.0.0。
    """
    return (os.environ.get("WA2_TTS_BIND") or HOST).strip() or HOST

# 本地 TTS 权重（由使用者自行准备并放入对应目录）
GPT_W = os.path.join(GSV, "GPT_weights_v4", "Touma_Kazusa-e15.ckpt")
SOVITS_W = os.path.join(GSV, "SoVITS_weights_v4", "Touma_Kazusa_e8_s33072_l32.pth")

_model_key = ""      # 合并版：当前已加载的角色语音配置签名


def _char_json(name):
    """读当前角色文件夹里的可选配置文件；没有/坏了都返回 {}。"""
    try:
        import characters
        p = os.path.join(characters.active_dir(), name)
        if os.path.isfile(p):
            import json
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _character_tts():
    """角色的 tts.json：{gpt, sovits}；相对路径按 GPT-SoVITS 根目录解析"""
    d = _char_json("tts.json")
    g = (d.get("gpt") or "").strip()
    s = (d.get("sovits") or "").strip()
    if not g or not s:
        return {}
    if not os.path.isabs(g):
        g = os.path.join(GSV, g)
    if not os.path.isabs(s):
        s = os.path.join(GSV, s)
    return {"gpt": g, "sovits": s}

# 参考音频：按情绪准备（语气由参考决定，音色由模型决定）
# 现在放在当前角色文件夹的 voice/<情绪>/ 下；老版本的 refs_touma 只当兜底
LEGACY_REFS_DIR = os.path.join(os.path.dirname(GSV), "refs_touma")
_REFS_OVERRIDE = ""      # 自测用：把参考音频目录指到临时位置

_proc = None
_ready = False
_lock = threading.Lock()
_synth_lock = threading.Lock()      # 合成串行：GPT-SoVITS 的 api 不是并发安全的
_last_err = ""
_starting = False


# ===== 情绪 → TTS 参数（你筛完音频后可以在这里微调）=====
EMOTION_PARAMS = {
    "neutral": {"speed_factor": 1.00, "temperature": 0.62, "top_k": 5,  "top_p": 0.90},
    "calm":    {"speed_factor": 0.98, "temperature": 0.55, "top_k": 4,  "top_p": 0.88},
    "soft":    {"speed_factor": 0.92, "temperature": 0.50, "top_k": 3,  "top_p": 0.85},
    "cold":    {"speed_factor": 1.02, "temperature": 0.35, "top_k": 2,  "top_p": 0.80},
    "angry":   {"speed_factor": 1.14, "temperature": 0.75, "top_k": 8,  "top_p": 0.95},
    "shy":     {"speed_factor": 0.90, "temperature": 0.70, "top_k": 6,  "top_p": 0.92},
    "surprised": {"speed_factor": 1.12, "temperature": 0.80, "top_k": 9, "top_p": 0.95},
    "毒舌":     {"speed_factor": 1.02, "temperature": 0.55, "top_k": 4,  "top_p": 0.88},
    "撒娇":     {"speed_factor": 0.94, "temperature": 0.70, "top_k": 6,  "top_p": 0.92},
    "哭腔":     {"speed_factor": 0.90, "temperature": 0.55, "top_k": 4,  "top_p": 0.88},
    "调侃吐槽": {"speed_factor": 1.05, "temperature": 0.72, "top_k": 7,  "top_p": 0.93},
    "问句":     {"speed_factor": 1.00, "temperature": 0.60, "top_k": 5,  "top_p": 0.90},
    "沉重":     {"speed_factor": 0.88, "temperature": 0.40, "top_k": 3,  "top_p": 0.82},
    "紧张":     {"speed_factor": 1.08, "temperature": 0.78, "top_k": 8,  "top_p": 0.94},
    "激动":     {"speed_factor": 1.12, "temperature": 0.82, "top_k": 9,  "top_p": 0.95},
    "委屈巴巴": {"speed_factor": 0.90, "temperature": 0.58, "top_k": 4,  "top_p": 0.88},
    "自怨自艾": {"speed_factor": 0.86, "temperature": 0.45, "top_k": 3,  "top_p": 0.84},
    "故作轻松": {"speed_factor": 0.98, "temperature": 0.62, "top_k": 5,  "top_p": 0.90},
    "气喘吁吁": {"speed_factor": 1.10, "temperature": 0.85, "top_k": 10, "top_p": 0.96},
    "H":        {"speed_factor": 0.94, "temperature": 0.72, "top_k": 5,  "top_p": 0.92},
    "睡觉时的呼吸": {"speed_factor": 0.80, "temperature": 0.40, "top_k": 3, "top_p": 0.82},
}

# 情绪分组：条数少的合并到主情绪（只影响读取，不移动文件）
REF_GROUPS = {
    "calm":  ["calm"],
    "soft":  ["soft", "轻快"],
    "毒舌":  ["毒舌", "回嘴"],
    "撒娇":  ["撒娇"],
    "哭腔":  ["哭腔", "悲伤"],
    "调侃吐槽": ["调侃吐槽"],
    "问句":  ["问句"],
    # 以下与提示词词表一一对应：模型能输出，才可能选到这些文件夹
    "沉重":  ["争执", "紧张"],
    "紧张":  ["紧张", "争执"],
    "激动":  ["激动"],
    "委屈巴巴": ["委屈巴巴"],
    "自怨自艾": ["自怨自艾"],
    "故作轻松": ["故作轻松"],
    "气喘吁吁": ["气喘吁吁"],
    "H":     ["H", "接吻"],
    "睡觉时的呼吸": ["睡觉时的呼吸"],
}

# 别名：模型可能输出的近义词 → 上面的主情绪
EMOTION_ALIAS = {
    "neutral": "calm", "平静": "calm", "冷静": "calm", "cold": "calm",
    "温柔": "soft", "轻快": "soft", "happy": "soft", "warm": "soft", "开心": "soft",
    "angry": "毒舌", "生气": "毒舌", "冷淡": "毒舌", "冷嘲": "毒舌",
    "shy": "撒娇", "害羞": "撒娇", "害羞撒娇": "撒娇",
    "悲伤": "哭腔", "委屈": "哭腔", "哽咽": "哭腔", "sad": "哭腔",
    "吐槽": "调侃吐槽", "调侃": "调侃吐槽",
    "疑问": "问句", "疑惑": "问句",
    # 新标签的英文/近义写法（模型偶尔会写英文）
    "nervous": "紧张", "nervousness": "紧张",
    "excited": "激动", "excitement": "激动",
    "委屈": "委屈巴巴", "受伤": "委屈巴巴",
    "自责": "悲伤", "selfblame": "自怨自艾",
    "awkward": "故作轻松", "装轻松": "故作轻松",
    "breathless": "气喘吁吁", "喘": "气喘吁吁",
    "呼吸": "睡觉时的呼吸", "睡觉": "睡觉时的呼吸",
    "性": "H", "色情": "H", "sex": "H",
}

def refs_dir():
    """参考音频根目录：当前角色 voice/ 里有情绪子目录就用它，否则回落到自带的 refs_touma。

    「查看文件」也打开这里，所以两边是同一个地方。自测可以用 `_REFS_OVERRIDE` 指到临时目录。
    """
    if _REFS_OVERRIDE:
        return _REFS_OVERRIDE
    try:
        d = char_res.voice_dir()
    except Exception:
        d = ""
    if d and os.path.isdir(d):
        try:
            if any(os.path.isdir(os.path.join(d, n)) for n in os.listdir(d)):
                return d
        except OSError:
            pass
    return LEGACY_REFS_DIR


def _has_refs():
    """参考音频根目录里有没有 wav（递归，情绪子目录也算）"""
    for _dp, _dn, files in os.walk(refs_dir()):
        for f in files:
            if f.lower().endswith(".wav"):
                return True
    return False


_ref_cache = {}
_REF_RATIO = {}          # {(文件夹, 文件名): 人声占比 0~1}，tools/ref_vad_scan.py 生成
_REF_DUR = {}            # {(文件夹, 文件名): 时长秒}
_REFCER = {}             # {(文件夹, 文件名): 平均 CER（越低越清楚）}
# 特别模式导入的参考音频（角色 voice/ 顶层的散装 wav）：优先于按情绪分组的
_IMPORTED = {"global": [], "by_emo": {}}
_refs_sig = ""


def _char_voice_sig():
    """参考音频目录的 mtime 签名：导入/删除了文件、或者换了角色，缓存都跟着失效"""
    sig = []
    for d in (refs_dir(), char_res.voice_dir()):
        try:
            sig.append(os.path.getmtime(d) if d and os.path.isdir(d) else 0)
        except Exception:
            sig.append(0)
    try:
        import characters
        cid = characters.active_id()
    except Exception:
        cid = ""
    return "%s|%.0f/%.0f" % (cid, sig[0], sig[1])


_JUNK_PHRASES = ("ご視聴", "お疲れ様でした", "ありがとうございます", "チャンネル登録",
                 "高評価", "字幕", "opening", "ending")


# 拟声/喘息里常见的字符（长音、促音、省略号、以及几个元音假名）
_MOAN_CHARS = set("あぁいうえおんっはひふへほー…、。！？ んあぁっ")


def _looks_like_moan(text):
    """这句是不是几乎全是拟声/喘息（「あ…っ…はぁ…」这种）。

    模型打情绪标签不可靠：H 场面的句子常被标成 calm/soft，结果用平静参考音频，
    听起来完全不对。这种句子改走 H 的参考音频才合理。

    判据是**字符集合占比**（不是"汉字少"）：整句 ≥75% 的字符来自拟声集合才算，
    这样「やめて」「あぁ。…ちょっと、指、な…」这类有实义假名的句子不会被误判。
    """
    s = (text or "").strip()
    if not s or len(s) > 20:
        return False
    kana = sum(1 for ch in s if "\u3041" <= ch <= "\u30ff")
    kanji = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
    if kana < 3 or kanji > 1:
        return False
    hit = sum(1 for ch in s if ch in _MOAN_CHARS)
    return hit / max(1, len(s)) >= 0.75


def _ref_wordiness(txt):
    """这段参考文本"有多像正常台词"（0~1）。

    H 目录里很多参考是「はぁー、はぁ、はぁ……ハルキ……」这种，拟声字符占绝大多数。
    拿它当参考去念正常台词，GPT-SoVITS 会被气声条件带跑，念到实词就退化。
    所以按"非拟声字符占比"给参考分级，正常台词优先用分数高的。
    """
    s = (txt or "").strip()
    if not s:
        return 0.0
    hit = sum(1 for ch in s if ch in _MOAN_CHARS)
    return 1.0 - hit / len(s)


def _text_ok(txt):
    """这段文本够不够当 prompt_text（现在只要求非空）。

    历史（2026-11-06）：这里曾经按"ASR 噪声特征"过滤，把 H 目录里 21 条参考音频
    判成不合格 —— 但用户逐个试听确认那些就是 H 的正确语调，是**判定过严**。
    真正的问题是当时 draft 文本来自视频转录（含幻觉「ご視聴ありがとうございました」），
    已经逐条重新转写并人工整理过，所以过滤退化为"非空即可"。
    """
    s = (txt or "").strip()
    return bool(s)


def _emo_from_filename(path):
    """文件名里带情绪就用它（angry_01.wav / 毒舌.wav / soft-2.wav）"""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    for emo, keys in REF_GROUPS.items():
        for k in keys:
            if k and k.lower() in stem:
                return emo
    for alias, emo in EMOTION_ALIAS.items():
        if alias and alias.lower() in stem:
            return emo
    return ""


def _load_imported():
    """扫 char/voice/*.wav → {global:[...], by_emo:{情绪:[...]}}"""
    out = {"global": [], "by_emo": {}}
    try:
        for wav, txt in char_res.voice_refs():
            emo = _emo_from_filename(wav)
            if emo:
                out["by_emo"].setdefault(emo, []).append((wav, txt))
            else:
                out["global"].append((wav, txt))
    except Exception:
        pass
    return out


def load_refs():
    """扫描参考音频目录：<emotion>/*.wav + 同名 .txt（prompt_text）

    返回 {emotion: [(wav_path, prompt_text), ...]}
    顶层散放的 wav 归入 neutral。
    另外把特别模式导入的 char/voice/*.wav 读进 _IMPORTED（合成时优先用）。
    """
    global _ref_cache, _IMPORTED, _refs_sig, _REF_RATIO, _REF_DUR, _REFCER
    out = {}
    _dropped = []
    root = refs_dir()
    try:
        if not os.path.isdir(root):
            return out
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                emo = name.lower()
                items = []
                for f in os.listdir(p):
                    if f.lower().endswith(".wav"):
                        base = os.path.splitext(f)[0]
                        tpath = os.path.join(p, base + ".txt")
                        txt = ""
                        if os.path.exists(tpath):
                            try:
                                txt = io.open(tpath, encoding="utf-8").read().strip()
                            except Exception:
                                txt = ""
                        if txt and not _text_ok(txt):
                            _dropped.append((os.path.basename(p), os.path.basename(f), txt[:24]))
                            txt = ""      # 当无文本处理：排到最后、只做兜底
                        items.append((os.path.join(p, f), txt))
                        if items:
                            out[emo] = items
            elif name.lower().endswith(".wav"):
                base = os.path.splitext(name)[0]
                tpath = os.path.join(root, base + ".txt")
                txt = ""
                if os.path.exists(tpath):
                    try:
                        txt = io.open(tpath, encoding="utf-8").read().strip()
                    except Exception:
                        txt = ""
                if txt and not _text_ok(txt):
                    txt = ""
                out.setdefault("neutral", []).append((p, txt))
    except Exception as e:
        log_err = str(e)
    # 有 prompt_text 的排前面：pick_ref 用 random.choice 均匀抽，
    # 但把无文本的排在后面并单独计数，抽的时候优先从「有文本」的里面抽。
    _transcribed = 0
    _plain = 0
    for _emo, _items in list(out.items()):
        _has = [x for x in _items if x[1]]
        _no = [x for x in _items if not x[1]]
        _transcribed += len(_has)
        _plain += len(_no)
        if _has:
            out[_emo] = _has + _no        # 有文本的在前，无文本的垫底
    if _dropped:
        try:
            import applog
            applog.log("参考音频：%d 条文本被判为空（只做兜底），例如 %s"
                       % (len(_dropped), "、".join("%s/%s" % (a, b) for a, b, _ in _dropped[:3])),
                       "TTS")
        except Exception:
            pass
    if _plain:
        try:
            import applog
            applog.log("参考音频：%d 个有文本、%d 个没有文本（没有的只在兜底时用）"
                       % (_transcribed, _plain), "TTS")
        except Exception:
            pass
    # 参考音频的「人声占比」（tools/ref_vad_scan.py 生成）：选参考要用它
    global _REF_RATIO
    _REF_RATIO = {}
    _REF_DUR = {}
    _REFCER = {}
    for _emo, _items in out.items():
        _side = os.path.join(root, _emo, "_refaudio.json")
        if not os.path.exists(_side):
            # 目录名可能是原样大小写（H）；再试一次原名
            for _n in os.listdir(root):
                if _n.lower() == _emo and os.path.isdir(os.path.join(root, _n)):
                    _side = os.path.join(root, _n, "_refaudio.json")
                    break
        if not os.path.exists(_side):
            continue
        try:
            _dir = os.path.basename(os.path.dirname(_side)).lower()
            _cer_side = os.path.join(os.path.dirname(_side), "_refcer.json")
            if os.path.isfile(_cer_side):
                try:
                    for _w, _c in json.loads(io.open(_cer_side, encoding="utf-8").read()).items():
                        _REFCER[(_dir, _w.lower())] = float(_c)
                except Exception:
                    pass
            for _r in json.loads(io.open(_side, encoding="utf-8").read()):
                # 用「文件夹/文件名」小写做键：Windows 大小写不敏感，
                # 直接拼路径会得到 h/ 而实际目录是 H/，对不上
                _k = (_dir, (os.path.basename(_r.get("wav") or "")).lower())
                _REF_RATIO[_k] = float(_r.get("ratio") or 0)
                _REF_DUR[_k] = float(_r.get("dur") or 0)
        except Exception:
            pass
    _ref_cache = out
    _IMPORTED = _load_imported()
    _refs_sig = _char_voice_sig()
    return out


def pick_ref(emo="neutral", target=""):
    """按情绪挑参考音频：特别模式导入的优先，其次 refs_touma 的分组，最后兜底"""
    import random
    emo = EMOTION_ALIAS.get(emo, emo)
    if _char_voice_sig() != _refs_sig:      # 导入 / 删除过参考音频 → 重新扫
        load_refs()
    if _IMPORTED["by_emo"].get(emo):        # 文件名带情绪的，只在对应情绪里用
        return random.choice(_IMPORTED["by_emo"][emo])
    if _IMPORTED["global"]:                 # 没带情绪的就是通用参考，优先于自带素材
        return random.choice(_IMPORTED["global"])
    refs = _ref_cache or load_refs()
    names = REF_GROUPS.get(emo, [emo])
    # 目录名在 load_refs 里被 lower() 过（h），REF_GROUPS 可能写的是 "H"
    # —— 不统一的话 H 这 38 条永远选不到，只剩「接吻」兜着
    _lower = {k.lower(): v for k, v in refs.items()}
    items = []
    for n in names:
        items.extend(refs.get(n) or _lower.get(str(n).lower()) or [])
    if not items:                     # 兜底：任意一条
        for v in refs.values():
            items.extend(v)
    if not items:
        return None, ""
    # 同情绪下随机抽，但按质量分层：
    #   1) 有正常台词（不是纯拟声、长度够）的优先 —— H 目录里混着「はぁ…はぁ…」这种
    #      纯呼吸参考，抽到它去念正常台词会念得怪
    #   2) 没有才用其它有文本的
    #   3) 再没有才用无文本的
    with_text = [x for x in items if x[1]]
    # 目标句本身是纯拟声 → 呼吸参考正合适，随便抽
    if _looks_like_moan(target):
        return random.choice(with_text or items)

    # 正常台词：**音频里得真有人声**，否则模型只出气声不出字（VAD 实测 H 里 74/151 是 0）。
    # 但 H 是例外：它的素材本来就是气声，VAD 天然低；用 VAD 门槛会把 H 全筛掉，
    # 只能借 calm 的音色 → 听起来完全不是 H 的语气（用户逐条听过，确认不是噪声）。
    def _key(path):
        return (os.path.basename(os.path.dirname(path)).lower(),
                os.path.basename(path).lower())

    def _ratio(path):
        # 没侧车信息 = 未知，按「不优先」处理（默认 1.0 会让没扫过的目录压过有数据的）
        return _REF_RATIO.get(_key(path), 0.0)

    def _dur(path):
        return _REF_DUR.get(_key(path), 0.0)

    def _good(pairs, lo=3.5, hi=7.5, min_ratio=0.5):
        """念正常台词用的参考：有人声、时长适中、文本不是纯拟声。

        实测：>8 秒的长参考会掉字；2~3 秒的气声参考会把整句带成气声
        （ka1625「はぁっ、はっ、はっ…」被抽中就整句念不清）。
        """
        out = []
        for x in pairs:
            d = _dur(x[0])
            if _ratio(x[0]) < min_ratio:
                continue
            if d and not (lo <= d <= hi):
                continue
            if _looks_like_moan(x[1]):
                continue
            c = _REFCER.get(_key(x[0]))
            if c is not None and c > 0.5:      # 实测念不清的参考不用
                continue
            out.append(x)
        return out

    local = _good(with_text, min_ratio=(0.0 if emo == "H" else 0.5))
    if emo == "H" and local:
        # H 放宽了 VAD，但「CER 未知」不能当成好：优先用测过的那些
        _known = [x for x in local if _REFCER.get(_key(x[0])) is not None]
        if _known:
            local = _known
    # 实测：H 那批参考最好也只有 CER≈0.45（念字不可靠）。
    # 若本情绪最好的一条也超过门槛，就借其它情绪「念得清」的参考，
    # 语速/温度仍用本情绪的，语气不至于跑掉。
    if local and emo != "H":
        # H 例外：H 那批参考本来就念不清（实测 0.44~0.56），借 calm 的音色会变成平静语气。
        # 用户要求「H 就用 H 的语气」，所以 H 不吃这条。
        _cs = [ _REFCER.get(_key(x[0])) for x in local ]
        _cs = [ c for c in _cs if c is not None ]
        if _cs and min(_cs) > 0.45:
            try:
                import applog
                applog.log("参考音频：%s 最好的一条 CER=%.2f 偏含糊，改借音色参考"
                           % (emo, min(_cs)), "TTS")
            except Exception:
                pass
            local = []
    if local:
        # 有 CER 分数的：只在「最好的那一档」里随机（保留多样性，但不抽到念不清的）
        scored = [(x, _REFCER.get(_key(x[0]))) for x in local]
        scored = [(x, c) for x, c in scored if c is not None]
        if scored:
            best = min(c for _, c in scored)
            pool = [x for x, c in scored if c <= best + 0.12]
            if pool:
                return random.choice(pool)
        _pick = random.choice(local)
        try:
            import applog
            applog.log("参考：%s → %s（CER=%s）" %
                       (emo, os.path.basename(_pick[0]),
                        _REFCER.get(_key(_pick[0]))), "TTS")
        except Exception:
            pass
        return _pick
    # 本情绪里没有能说话的参考 → 跨情绪借（calm/soft 这类以说话为主），参数仍用本情绪的
    borrow = []
    for _e, _v in refs.items():
        # 不要从本情绪自己的目录里「借」：这里要的是**别的**情绪的音色
        if str(_e).lower() == str(emo).lower():
            continue
        for x in _good([y for y in _v if y[1]]):
            borrow.append(x)
    if borrow:
        try:
            import applog
            applog.log("参考音频：%s 里没有足够「人声」的参考（VAD <0.5），改借其它情绪的" % emo, "TTS")
        except Exception:
            pass
        return random.choice(borrow)
    # 都没有 → 退回按文本分的选法
    wordy = [x for x in with_text if _ref_wordiness(x[1]) >= 0.5 and len(x[1]) >= 6]
    return random.choice(wordy or with_text or items)


def _http_ok(timeout=2.0):
    """对当前 URL 发一个轻请求，看服务在不在（远程模式用）"""
    try:
        with urllib.request.urlopen(URL + "/", timeout=timeout) as r:
            return r.status < 500
    except Exception:
        try:
            h, p = endpoint()
            import socket
            with socket.create_connection((h, p), timeout=timeout):
                return True
        except Exception:
            return False


def available():
    """能不能用语音。

    · 本地模式：权重 / 运行时 / 参考音频都要在（原来就有的检查）。
    · 远程模式（URL 指向别的机器）：本机没有权重是正常的，只看那头通不通。
    """
    if is_remote():
        return _http_ok()
    if not all(os.path.exists(p) for p in (API_PY, PY, GPT_W, SOVITS_W)):
        return False
    if _has_refs():
        return True
    try:
        return bool(char_res.voice_refs())
    except Exception:
        return False


def _post(path, timeout=30):
    with urllib.request.urlopen(URL + path, timeout=timeout) as r:
        return r.read()


def _ensure_char_model():
    """合并版：切到当前角色 tts.json 里的 GPT/SoVITS 权重（同一进程，不重启）"""
    global _model_key, _last_err
    m = _character_tts()
    if not m:
        return True
    key = m["gpt"] + "|" + m["sovits"]
    if key == _model_key:
        return True
    try:
        _post("/set_gpt_weights?weights_path=" + urllib.parse.quote(m["gpt"]))
        _post("/set_sovits_weights?weights_path=" + urllib.parse.quote(m["sovits"]))
        _model_key = key
        return True
    except Exception as e:
        _last_err = "切换角色权重失败: %s" % e
        return False


def _wait_ready(timeout=READY_TIMEOUT_S):
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with socket.create_connection(endpoint(), timeout=2):
                return True
        except Exception:
            time.sleep(1.0)
    return False


def _child_env():
    """给 GPT-SoVITS 子进程准备环境：runtime/ 里的 ffmpeg 进 PATH，TMP 指到 tts/tmp。"""
    env = os.environ.copy()
    rt = os.path.dirname(PY)
    if os.path.isdir(rt):
        env["PATH"] = rt + os.pathsep + env.get("PATH", "")
    tmp = os.path.join(os.path.dirname(GSV), "tmp")
    try:
        os.makedirs(tmp, exist_ok=True)
        env["TMP"] = tmp
        env["TEMP"] = tmp
    except Exception:
        pass
    return env


def start():
    """拉起 API 服务并切到我们的权重（幂等）。

    `_starting` 只在真正启动过程中是 True，任何失败路径都在 finally 里复位 ——
    否则一次「依赖缺失」就会让之后所有请求直接返回 False，再也拉不起来。
    """
    global _starting
    load_refs()
    # 已有线程在启动中: 不阻塞请求, 直接报告当前状态
    if _starting and not _ready:
        return False
    if _ready:
        return True
    with _lock:
        if _ready:
            return True
        _starting = True
        try:
            return _start_locked()
        finally:
            _starting = False


def _start_locked():
    """真正启动（调用方已持有 _lock）"""
    global _proc, _ready, _last_err
    if not available():
        _last_err = "语音依赖缺失"
        return False
    import socket
    in_use = False
    try:
        with socket.create_connection(endpoint(), timeout=1):
            in_use = True
    except Exception:
        pass
    if in_use:
        # 已有服务在跑: 直接复用（可能是上次残留）
        try:
            _post("/set_gpt_weights?weights_path=" + urllib.parse.quote(GPT_W))
            _post("/set_sovits_weights?weights_path=" + urllib.parse.quote(SOVITS_W))
            _ready = True
            return True
        except Exception:
            pass   # 复用失败就走重新启动
    try:
        cfg = os.path.join(GSV, "GPT_SoVITS", "configs", "tts_infer.yaml")
        _proc = subprocess.Popen(
            [PY, API_PY, "-a", bind_host(), "-p", str(endpoint()[1]), "-c", cfg],
            cwd=GSV,
            env=_child_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
    except Exception as e:
        _last_err = "启动失败: %s" % e
        return False

    if not _wait_ready():
        _last_err = "服务未就绪"
        return False

    try:
        _post("/set_gpt_weights?weights_path=" + urllib.parse.quote(GPT_W))
        _post("/set_sovits_weights?weights_path=" + urllib.parse.quote(SOVITS_W))
    except Exception as e:
        _last_err = "加载权重失败: %s" % e
        return False

    _ready = True
    return True


def start_translate():
    """拉起翻译服务（Hy-MT2 + llama.cpp）"""
    global _tproc
    if _tproc is not None:
        return True
    if not os.path.exists(T_PY):
        return False
    import socket
    try:
        with socket.create_connection(("127.0.0.1", T_PORT), timeout=1):
            return True
    except Exception:
        pass
    try:
        _tproc = subprocess.Popen(
            [PY, T_PY], cwd=GSV, env=_child_env(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=0x08000000 if os.name == "nt" else 0)
    except Exception:
        return False
    for _ in range(60):
        try:
            with socket.create_connection(("127.0.0.1", T_PORT), timeout=1):
                return True
        except Exception:
            time.sleep(0.5)
    return False


def translate_via_service(text, to="ja", timeout=30):
    """调用翻译服务; 失败返回空串"""
    try:
        q = urllib.parse.urlencode({"t": text, "to": to})
        with urllib.request.urlopen("http://127.0.0.1:%d/translate?%s" % (T_PORT, q),
                                    timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
        return (d.get("text") or "").strip() if d.get("ok") else ""
    except Exception:
        return ""


_TTS_CACHE_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                "..", "cache", "tts"))


# 合成行为（参数/参考挑选/切分）一变就 +1：否则会一直命中旧参数合成的音频。
# v3（2026-11-07）：H 不再借 calm 音色。旧音频是用 calm 参考合成的，
# key 里不含"选了哪条参考"，所以必须靠版本号让它们全部失效，
# 否则同一句话重放时仍然是那种平静嗓音（表现就是"偶尔还是平静"）。
_CACHE_VERSION = 3


def _tts_cache_path(text, lang, emo):
    """文本+语种+情绪+参考音频版本 唯一决定一段音频。

    参考音频版本算进 key：导入了新参考音频，旧的缓存就不再命中（否则换了音色
    还是播昨天那段）。
    """
    import hashlib
    # 每次现算参考音频目录的签名（两次 stat，很便宜）：刚导入完的那一句也能立刻换新，
    # 不会先命中导入前的旧缓存
    # 参数也要进 key：否则改了温度/top_k/切分，还是放上一次的旧音频
    _prm = EMOTION_PARAMS.get(EMOTION_ALIAS.get(emo, emo)) or EMOTION_PARAMS["neutral"]
    _sig = "%s|%s|%s|%s|%s|%s|%s|%s" % (
        text, lang, emo, _char_voice_sig(), _CACHE_VERSION,
        _prm.get("temperature"), _prm.get("top_k"), _prm.get("top_p"))
    key = _sig
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(_TTS_CACHE_DIR, h + ".wav")


def prune_tts_cache(max_mb=800, max_days=45):
    """修剪语音缓存：超过总量按最近使用删，超过天数的也删。

    为什么不再「启动即清空」：合成一句要 3~7 秒，而 key 里已经带了
    「文本 + 语种 + 情绪 + 参考音频版本」—— 换音色、改台词天然不会命中旧档，
    用清空来保证正确性是过度保守，代价是每次开程序后回想重放都要重新合成。
    """
    import time as _t
    try:
        if not os.path.isdir(_TTS_CACHE_DIR):
            return 0
        files = []
        total = 0
        for fn in os.listdir(_TTS_CACHE_DIR):
            p = os.path.join(_TTS_CACHE_DIR, fn)
            if not os.path.isfile(p):
                continue
            try:
                st = os.stat(p)
            except OSError:
                continue
            files.append((st.st_atime or st.st_mtime, st.st_size, p))
            total += st.st_size
        now = _t.time()
        limit = max(0, int(max_mb)) * 1024 * 1024
        days = max(0, int(max_days)) * 86400
        removed = 0
        # 先按年龄删
        keep = []
        for atime, size, p in files:
            if days and (now - atime) > days:
                try:
                    os.remove(p)
                    removed += 1
                    total -= size
                except OSError:
                    pass
            else:
                keep.append((atime, size, p))
        # 再按总量删（最久没用的先删），但**永远保留最近 3 个**：
        # 用户要求「例外保留最近三条语音，重启也不清除」，这样读档后头几句能立刻重放。
        if limit and total > limit:
            keep.sort()
            _n = len(keep)
            for _i, (atime, size, p) in enumerate(keep):
                if total <= limit:
                    break
                if _i >= _n - 3:          # 最后 3 个（最新用过的）不删
                    continue
                try:
                    os.remove(p)
                    removed += 1
                    total -= size
                except OSError:
                    pass
        if removed:
            try:
                import applog
                applog.log("语音缓存修剪：删了 %d 个文件，剩 %.1f MB"
                           % (removed, total / 1048576.0), "TTS")
            except Exception:
                pass
        return removed
    except Exception:
        return 0


def clear_tts_cache():
    """清空语音缓存（清理面板会调它；启动时不再自动调用，改 prune_tts_cache）"""
    try:
        import shutil
        if os.path.isdir(_TTS_CACHE_DIR):
            shutil.rmtree(_TTS_CACHE_DIR, ignore_errors=True)
    except Exception:
        pass


def _gain_path(wav_path):
    """增益侧车：wav 旁边一个 .gain.json，wav 本身永远不改。"""
    return wav_path + ".gain.json"


def _record_gain(wav_path, data):
    """量一次响度，把「到 -16 LUFS 要几个 dB」记在侧车 json 里。

    同一段音频（字节数没变、目标没变）就沿用旧记录，不重复量。
    量不出来（格式不认识 / 静音 / 出错）就返回 None，不影响播放。
    """
    if not wav_path or not data:
        return None
    side = _gain_path(wav_path)
    try:
        old = cache_io.read_json(side, None)
    except Exception:
        old = None
    if (isinstance(old, dict) and old.get("target") == audio_gain.TARGET_LUFS
            and old.get("bytes") == len(data)):
        return old
    try:
        info = dict(audio_gain.probe(data))
    except Exception as e:
        applog.log("响度测量失败：%s" % e, "WARN")
        return None
    info["bytes"] = len(data)
    info["at"] = int(time.time())
    try:
        diag.note("tts", lufs=info.get("lufs"), peak_db=info.get("peak_db"),
                  gain_db=info.get("gain_db"), target=info.get("target"),
                  secs=round(len(data) / 2.0 / max(1, info.get("rate") or 32000), 2))
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(side), exist_ok=True)
        with open(side, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        applog.log("TTS 响度：%.1f LUFS / 峰值 %.1f dBFS → 增益 %+.1f dB"
                   % (info.get("lufs") or -99.0, info.get("peak_db") or -99.0,
                      info.get("gain_db") or 0.0), "TTS")
    except Exception:
        pass
    return info


def gain_of_cache(wav_path):
    """读侧车（没有就 None）；给自测 / 调试用。"""
    try:
        d = cache_io.read_json(_gain_path(wav_path), None)
        return d if isinstance(d, dict) else None
    except Exception:
        return None



# 角色名的中文写法 → 日文读音。数据放当前角色文件夹的 pronunciation.json，
# 代码里不写死具体角色；没有这个文件就原样送 TTS。
_NAME_CACHE = {"path": "", "mtime": 0, "pairs": []}


def _name_pairs():
    try:
        import characters
        p = os.path.join(characters.active_dir(), "pronunciation.json")
        if not os.path.isfile(p):
            return []
        mt = os.path.getmtime(p)
        if _NAME_CACHE["path"] == p and _NAME_CACHE["mtime"] == mt:
            return _NAME_CACHE["pairs"]
        import json
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        pairs = []
        if isinstance(d, dict):
            pairs = [(str(k), str(v)) for k, v in d.items()]
        elif isinstance(d, list):
            for it in d:
                if isinstance(it, (list, tuple)) and len(it) >= 2:
                    pairs.append((str(it[0]), str(it[1])))
                elif isinstance(it, dict) and it.get("from") and it.get("to"):
                    pairs.append((str(it["from"]), str(it["to"])))
        pairs.sort(key=lambda x: -len(x[0]))
        _NAME_CACHE.update({"path": p, "mtime": mt, "pairs": pairs})
        return pairs
    except Exception:
        return []


def normalize_ja_names(text):
    """按角色 pronunciation.json 把名字换成假名；界面文字不变。"""
    if not text:
        return text
    for a, b in _name_pairs():
        if a in text:
            text = text.replace(a, b)
    return text


def synthesize(text, lang="zh", timeout=SYNTH_TIMEOUT_S, emo="neutral"):
    """返回 wav 字节；失败返回 None

    合成串行：GPT-SoVITS 的 api 同时来两个请求会抢模型，这里排队；
    排队时先看缓存，同一句重复播不会重复合成。
    """
    global _last_err, _ready
    if not _ready:
        return None
    if lang == "ja":
        text = normalize_ja_names(text)
    # 兜底：模型把 H 场面的拟声句标成了 calm/soft，这里按句子本身纠正
    if emo in ("calm", "soft", "neutral", "calm(平静)", "soft(温柔)", ""):
        if _looks_like_moan(text):
            emo = "H"
            try:
                import applog
                applog.log("情绪纠正：纯拟声句按 H 选参考音频（原标签 %s）" % (emo,), "TTS")
            except Exception:
                pass
    try:
        _cp = _tts_cache_path(text, lang, emo)
    except Exception:
        _cp = None
    with _synth_lock:
        if not _ensure_char_model():
            return None
        if _cp:
            try:
                if os.path.exists(_cp):
                    with open(_cp, 'rb') as _f:
                        _cached = _f.read()
                    if _cached:
                        diag.count("tts.hit")
                        try:
                            import applog
                            applog.log("TTS 缓存命中：len=%d emo=%s key=%s"
                                       % (len(text), emo, os.path.basename(_cp)[:8]), "TTS")
                        except Exception:
                            pass
                        _record_gain(_cp, _cached)      # 侧车缺了就补一份，下次直接读
                        return _cached
            except Exception:
                pass
        diag.count("tts.miss")
        try:
            import applog
            applog.log("TTS 合成请求：%d 字，切分=%s，参考=%s"
                       % (len(text), "cut0" if len(text) <= 40 else "cut5",
                          os.path.basename(ref_path or "")), "TTS")
            applog.log("TTS 缓存未命中：len=%d emo=%s key=%s"
                       % (len(text), emo, os.path.basename(_cp or "")[:8]), "TTS")
        except Exception:
            pass
        # 按情绪挑参考音频；参数也跟着情绪走（一条都没有就别合成，交给上层回报）
        ref_path, ref_text = pick_ref(emo, target=text)
        if not ref_path:
            return None
        prm = EMOTION_PARAMS.get(EMOTION_ALIAS.get(emo, emo)) or EMOTION_PARAMS["neutral"]
        q = urllib.parse.urlencode({
            "text": text,
            "text_lang": lang,
            "ref_audio_path": ref_path,
            "prompt_text": ref_text,
            "prompt_lang": "ja",
            "top_k": prm["top_k"],
            "top_p": prm["top_p"],
            "temperature": prm["temperature"],
            # 短句整句合成：cut5 会把「〜して、〜」从词中间切开，被切断的实词会丢
            "text_split_method": ("cut0" if len(text) <= 40 else "cut5"),
            "batch_size": 1,
            "speed_factor": prm["speed_factor"],
        })
        try:
            # 第一次合成要加载模型，给足时间
            _data = _post("/tts?" + q, timeout=timeout)
            try:
                if _data and _cp:
                    diag.count("tts.render")
                    os.makedirs(os.path.dirname(_cp), exist_ok=True)
                    with open(_cp, 'wb') as _f:
                        _f.write(_data)
                    _record_gain(_cp, _data)            # 增益记在侧车，wav 不动
            except Exception:
                pass
            return _data
        except Exception as e:
            _last_err = "合成失败: %s" % e
            # 连不上 / 超时 = 服务像是挂了：标记未就绪，下次请求会重新拉起。
            # （HTTP 4xx/5xx 说明服务还活着，只是这句没合成出来，不重拉）
            if isinstance(e, urllib.error.HTTPError):
                pass
            elif isinstance(e, (urllib.error.URLError, OSError)):
                _ready = False
            return None


def stop():
    global _proc, _ready
    if _proc is not None:
        try:
            _proc.terminate()
        except Exception:
            pass
        _proc = None
    _ready = False


atexit.register(stop)


def status():
    return {"available": available(), "ready": _ready, "error": _last_err}
