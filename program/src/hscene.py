# -*- coding: utf-8 -*-
"""成人场面判定：给情绪标签兜底，让 H 场景用上 H 的参考音频。

为什么需要：模型把 H 台词标成 calm 是常态（实测日志 3/3 都是 calm）：
  んあぁっ、も、もう、むり…っ、いく、また、いく
  ひゃっ…！ た、たかい…っ、ん、この、体勢…っ
  はぁっ、あ、まだ…っ、もう、いってる、のに…っ
靠"纯拟声"启发式抓不住（有实词、句子 23 字），必须用**上下文信号**。

判据：最近几条对话里 H 特征词的命中数（日语原句 + 中文译文都看），
再叠加"短句 + 假名占比高"的加权。命中达到阈值就判定为成人场面，
此时 calm/soft/neutral 一律改成 H（只影响选参考音频，不改台词）。

刻意保守：普通亲密场景（抱一下、亲一下）不该被误判成 H，
所以要求 ≥2 个不同特征词、且至少有一个"强特征词"。
"""
import re

# 强特征：出现就已经很说明问题
STRONG = (
    "いく", "いっちゃ", "いってる", "いかされ", "むり", "だめ", "きもちいい",
    "気持ちいい", "きもち", "奥", "おく", "中で", "なかで", "出して", "だして",
    "挿", "はいっ", "入って", "はいって", "抜", "ぬい", "体勢", "たいせい",
    "腰", "こし", "胸", "むね", "舐", "なめ", "くわ", "感じ", "かんじ",
    "敏感", "びんかん", "濡", "ぬれ", "喘", "あえ", "イク", "イか",
    "はぁ", "んっ", "あぁっ", "やめて", "やだ",
    # 气声写法：以前「んぁっ」「はぁっ」「ふぅっ」匹配不上（词表里写的是 んっ/あぁっ）
    "ぁっ", "んぁ", "はぁっ", "ふぅっ", "んんっ", "そこ", "いやだ", "いやっ",
)
# 弱特征：单独出现不算，但和强特征一起加分
# 日常对话里也常出现的词：只加分，不足以单独判定
WEAK = ("キス", "きす", "抱", "だき", "触", "さわ", "熱", "あつ", "痛", "いた",
        "恥", "はじ", "声", "こえ", "息", "いき",
        "もっと", "まだ", "そこ", "だめ")

# 中文（译文）里的对应说法
ZH = ("里面", "里面", "好深", "太深", "进来", "不要", "停下", "去了", "要去了",
      "忍不住", "好舒服", "舒服", "湿", "兴奋", "身体", "腰", "胸", "舔",
      "拔", "插", "用力", "再快", "慢点", "轻点", "好色", "色情")

_STRONG_RE = re.compile("|".join(re.escape(w) for w in STRONG))
_WEAK_RE = re.compile("|".join(re.escape(w) for w in WEAK))
_ZH_RE = re.compile("|".join(re.escape(w) for w in ZH))
# 中文里也算强特征的几个词（译文里出现就已经很说明问题）
ZH_STRONG = ("里面", "太深", "好深", "要去了", "忍不住", "好舒服",
             "色情", "插入", "拔出来")
_ZH_STRONG_RE = re.compile("|".join(re.escape(w) for w in ZH_STRONG))


def score(text):
    """单条文本的 H 特征分（强特征 2 分、弱特征 1 分，中文词各 1 分）"""
    s = text or ""
    strong = len(set(_STRONG_RE.findall(s)))
    weak = len(set(_WEAK_RE.findall(s)))
    zh = len(set(_ZH_RE.findall(s)))
    # 中文强特征也按个数算强信号（只看译文也能判出来）；
    # 以前只 +1，导致「里面太深了…要去了」这种只出现在译文里的句子过不了门槛
    strong += len(set(_ZH_STRONG_RE.findall(s)))
    return strong * 2 + weak + zh, strong


def is_intimate(messages, lookback=6):
    """最近几条对话是不是成人场面。

    只看 user / assistant 的正文，遇不到就返回 False（宁可漏判，不要乱判）。
    """
    if not messages:
        return False
    total = 0
    strong_total = 0
    hits = 0
    for m in messages[-lookback:]:
        if not isinstance(m, dict):
            continue
        txt = (m.get("content") or "") + " " + (m.get("zh") or "")
        s, strong = score(txt)
        if s >= 2:
            hits += 1
        total += s
        strong_total += strong
    # 强特征至少 1 个、且总分够、且至少一条消息有信号 → 认为是成人场面。
    # 门槛 2 而不是 3：单个强特征词就是 2 分，门槛 3 会让「…むり…」这种短句永远判否
    # （日志实证：H 场景里反复出现「关键词判定=否」→ 语音退回平静）。
    # 门槛：至少 2 个不同的强特征词、总分 ≥4。
    # 单凭一个「だめ」「まだ」就判定的时代结束了——那会把日常吐槽全变成特殊场面。
    return strong_total >= 2 and total >= 4 and hits >= 1


def hint():
    """给提示词用的一句话（模型自己也会因此更愿意标 H）"""
    return ("\n【场面】当前处于成人场面：情绪标记一律用 H"
            "（即使台词听起来平常、很短、只有拟声和喘息也一样），不要用 calm 或 soft。")

# ---------------- 当前场面状态（跨请求） ----------------
# /api/chat 判定完写这里；/api/tts 没带 hscene 参数时读它。
# 这样"载入存档后自动重播的那一句"也能按当前场面念，而不是靠前端有没有记住标记。
_LAST = {"scene": None, "neg": 0}

# 连续多少次判否才真的退出成人场面。
# 为什么要迟滞：H 场景里常有「ん…」「…」这种极短的一两句，
# 关键词分数会掉到阈值下，判定翻成否 → 下一句突然用平静语气（用户反馈「很出戏」）。
EXIT_STREAK = 5


def set_last(is_intimate):
    """记录一次判定，带迟滞。返回**生效**的场面状态。"""
    if is_intimate:
        _LAST["scene"] = True
        _LAST["neg"] = 0
        return True
    if _LAST["scene"] is True:
        _LAST["neg"] += 1
        if _LAST["neg"] < EXIT_STREAK:
            return True          # 还在 H 场面里，短句不改变判定
        _LAST["scene"] = False
        _LAST["neg"] = 0
        return False
    _LAST["scene"] = False
    _LAST["neg"] = 0
    return False


def force_exit():
    """立刻退出成人场面（用于"玩家说了一句完整、毫无 H 特征的句子"）。"""
    _LAST["scene"] = False
    _LAST["neg"] = 0
    return False


def last_scene():
    """None=还没判定过，True/False=当前生效的场面状态"""
    return _LAST["scene"]


def reset():
    """强制退出（换角色 / 读档到别的场景时用）"""
    _LAST["scene"] = None
    _LAST["neg"] = 0
