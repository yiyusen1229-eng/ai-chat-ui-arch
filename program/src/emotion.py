# -*- coding: utf-8 -*-
"""情绪迟滞：别让立绘表情和参考音频**一句话一跳**。

为什么要有这个（用户 2026-11-22 反馈）：
    「上一句语音还是平静语气，下一句突然变成哭音，再下一句又变回平静」。
    根因是两层都是**逐句**判定、句与句之间没有连续性：
      · 模型每句自己写 `<emotion>`，写了就照用 —— 它不知道上一句是什么；
      · 没写标签时 `text_parse.infer_emotion` 也按这一句的字眼猜（一句「ばか」就判毒舌）。
    实测：和纱 `sessions/auto.json` 里 338 次相邻比较，**154 次情绪不同（45%）**，
    而且大量是 `撒娇 撒娇 撒娇 calm 撒娇` 这种单句离群。

规则（像 `hscene` 的迟滞一样，只管「这一句实际用哪个情绪」）：
    1. 同一个新情绪要**连续出现 2 次**才真的切 —— 单句离群压掉；
    2. 一直不重复也不许卡死：连续 3 句都不是基线 → 认了，切过去（MAX_HOLD）；
    3. 「问句」是句型不是心情：直接放行，**不改基线**（问一句不该把气氛重置）；
    4. 「H」是场面模式不是心情：直接放行；从 H 出来时立刻切回（不然 H 语音拖到正常台词）；
    5. 没线索（`source="default"` / 空）→ 沿用上一次的情绪，别回落 calm；
    6. `prev_hint`（前端带上来的「上一句情绪」）只在**还没有基线**时用来播种 ——
       读档 / 重启后第一句不会从 calm 从零开始。

对外：
    settle(emo, source="tag", prev_hint="", scene_h=False) → 这一句实际用的情绪
    last()      → 当前基线（还没判定过是 ""）
    reset()     → 换角色 / 新游戏 / 读档时清空
    status()    → 日志 / 调试面板用
"""

EMO_DEFAULT = "calm"

PASS_THROUGH = ("问句",)     # 句型：放行但不参与基线
SCENE_EMO = "H"              # 场面模式：直接接管基线，退出时立刻切回
SWITCH_AFTER = 2             # 同一个新情绪连续几次才切
MAX_HOLD = 3                 # 连续几句都不是基线就认了（防卡死）

_STATE = {"last": "", "pend": "", "n": 0, "hold": 0,
          "switched": 0, "kept": 0, "reset": 0}


def last():
    """当前生效的情绪基线（还没判定过是 ""）"""
    return _STATE["last"]


def status():
    return dict(_STATE)


def reset():
    """换角色 / 新游戏 / 读档时清空（下一句重新建立基线）"""
    _STATE.update(last="", pend="", n=0, hold=0)
    _STATE["reset"] += 1
    return _STATE


def settle(emo, source="tag", prev_hint="", scene_h=False):
    """返回这一句**实际该用**的情绪（写进回复、选参考音频、挑立绘表情的都是它）。"""
    emo = (emo or "").strip()
    hint = (prev_hint or "").strip()

    # ---- 还没基线：先看场面（H 优先），否则用前端带上来的上一句情绪播种 ----
    if not _STATE["last"]:
        if scene_h or emo == SCENE_EMO:
            _STATE["last"] = SCENE_EMO
            _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
            return SCENE_EMO
        seed = hint or emo or EMO_DEFAULT
        _STATE["last"] = seed
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        if seed == emo or not emo:
            return _STATE["last"]

    # ---- 场面模式（H）：直接接管，不算心情 ----
    if scene_h or emo == SCENE_EMO:
        if _STATE["last"] != SCENE_EMO:
            _STATE["switched"] += 1
        _STATE["last"] = SCENE_EMO
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        return SCENE_EMO

    # ---- 从 H 场面出来：立刻切回，别把 H 语气拖到正常台词 ----
    if _STATE["last"] == SCENE_EMO:
        _STATE["last"] = emo or EMO_DEFAULT
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        _STATE["switched"] += 1
        return _STATE["last"]

    # ---- 没线索：沿用上一次（别回落 calm，也别把离群候选当成新情绪） ----
    if not emo or source == "default":
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        _STATE["kept"] += 1
        return _STATE["last"]

    # ---- 问句：放行，但不改基线 ----
    if emo in PASS_THROUGH:
        return emo

    # ---- 和基线一样：确认，清掉候选计数 ----
    if emo == _STATE["last"]:
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        return _STATE["last"]

    # ---- 不同：累计「连续几次是新的」+ 候选出现次数 ----
    _STATE["hold"] += 1
    if emo == _STATE["pend"]:
        _STATE["n"] += 1
    else:
        _STATE["pend"], _STATE["n"] = emo, 1
    if _STATE["n"] >= SWITCH_AFTER or _STATE["hold"] >= MAX_HOLD:
        _STATE["last"] = emo
        _STATE["pend"], _STATE["n"], _STATE["hold"] = "", 0, 0
        _STATE["switched"] += 1
        return _STATE["last"]

    # 这一句压回基线（立绘 / 参考音频都跟着不动）
    _STATE["kept"] += 1
    return _STATE["last"]
