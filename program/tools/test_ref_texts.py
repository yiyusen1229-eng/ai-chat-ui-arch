# -*- coding: utf-8 -*-
"""参考音频：文本配对、随机抽取、无文本的只做兜底。

    cd program && .\\build_env\\venv\\Scripts\\python.exe tools\\test_ref_texts.py
"""
import io
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))
# 角色目录指到部署目录，第 4 步才能扫到真实参考音频（只读）
os.environ.setdefault("WA2_USER_DIR",
                      os.path.join(os.path.dirname(HERE), "build_env"))

import tts_client as TC  # noqa: E402

TMP = tempfile.mkdtemp(prefix="wa2_refs_")
FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def make(emo, name, text=None):
    d = os.path.join(TMP, emo)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "wb") as f:
        f.write(b"RIFF" + b"0" * 40)
    if text is not None:
        io.open(os.path.join(d, os.path.splitext(name)[0] + ".txt"),
                "w", encoding="utf-8", newline="\n").write(text + "\n")


def main():
    TC._REFS_OVERRIDE = TMP
    TC._IMPORTED = {"by_emo": {}, "global": []}      # 排除"特别模式导入"的干扰

    make("calm", "a.wav", "最初から言ってる。別に、嫌じゃないって")
    make("calm", "b.wav", "なら拍手はやめろ。嫌味ったらしい")
    make("calm", "silent.wav", None)                 # 没文本（呼吸声之类）
    make("哭腔", "c.wav", "…そんなこと言われたって")

    print("[1] 扫描：分组与文本配对")
    refs = TC.load_refs()
    check("分成 2 组", set(refs) == {"calm", "哭腔"}, list(refs))
    calm = refs.get("calm") or []
    check("calm 3 条", len(calm) == 3, calm)
    check("有文本的排前面", calm[0][1] != "" and calm[-1][1] == "",
          [os.path.basename(x[0]) for x in calm])
    check("文本内容读对了", calm[0][1].startswith("最初から"), calm[0][1])
    check("文本不会带换行", "\n" not in calm[0][1], repr(calm[0][1]))

    print("[2] 抽取：只在有文本的里面随机")
    picks = set()
    for _ in range(40):
        p, txt = TC.pick_ref("calm")
        picks.add(os.path.basename(p or ""))
        if not txt:
            check("抽到了没文本的（不该发生）", False, os.path.basename(p or ""))
            break
    else:
        check("40 次都抽到有文本的", True)
    check("确实随机（抽到不止一个）", len(picks) >= 2, picks)
    check("不会抽到 silent.wav", "silent.wav" not in picks, picks)

    print("[3] 该情绪全都无文本时，兜底仍能出声")
    TC._REF_CACHE_SAVE = TC._ref_cache
    refs = TC.load_refs()
    refs["只有呼吸"] = [(os.path.join(TMP, "calm", "silent.wav"), "")]
    TC._ref_cache = refs
    p, txt = TC.pick_ref("只有呼吸")
    check("兜底返回了文件", bool(p), p)
    check("兜底文本是空", txt == "", repr(txt))

    print("[4] 真实目录：配对数")
    TC._REFS_OVERRIDE = ""
    real = TC.load_refs()
    tot = sum(len(v) for v in real.values())
    has = sum(1 for v in real.values() for x in v if x[1])
    check("真实参考音频都扫到了", tot > 0, tot)
    # 覆盖率不再要求 80%：H 那批被判定为转录噪声的（13 条无台词 + 13 条噪声）
    # 是有意排除的，宁可没文本也别拿噪声当 prompt_text
    # 现在拟声/呼吸类也补了「呼吸型文本」，所以要求全覆盖
    check("全目录都有文本（拟声/呼吸也不例外）", has == tot, (has, tot))
    empty = [x for v in real.values() for x in v if x[1] == ""]
    check("空文本只会出现在兜底项里（排最后）",
          all(not x[1] for v in real.values() for x in v[len([y for y in v if y[1]]):]),
          len(empty))


    print("[5] 文本判定只要求非空（真实素材不许被当噪声丢掉）")
    ok = TC._text_ok
    check("普通台词通过", ok("最初から言ってる。別に、嫌じゃないって"))
    check("喘息/拟声也通过（用户确认这是 H 的正确语调）", ok("んん…ん"))
    check("重复假名也通过", ok("あ" * 30))
    check("空文本不通过", not ok(""))
    check("只有空白不通过", not ok("   "))

    print("[6] H 组现在真的选得到（大小写不敏感）")
    import collections
    c = collections.Counter()
    for _ in range(12):
        p, txt = TC.pick_ref("H")
        c[os.path.basename(p or "")] += 1
    check("抽到了 H/接吻 里的文件", all(c), c)
    check("不止一个文件（随机）", len(c) >= 2, c)
    check("都有 prompt_text", all(TC.pick_ref("H")[1] for _ in range(5)))


    print("[7] 纯拟声句按 H 选参考（模型标签不可靠时的兜底）")
    m = TC._looks_like_moan
    check("「あ…っ…はぁ…」算拟声", m("あ…っ…はぁ…"))
    check("「はぁ…はぁ…」算拟声", m("はぁ…はぁ…"))
    check("「やめて」不算", not m("やめて"))
    check("「あぁ。…ちょっと、指、な…」不算（有实词）", not m("あぁ。…ちょっと、指、な…"))
    check("长句不算", not m("最初から言ってる。別に、嫌じゃないって"))


    print("[8] 参考音频按实词分分级（正常台词不用呼吸参考）")
    w = TC._ref_wordiness
    check("纯呼吸参考分数低", w("あ…はぁ…はぁ…はぁ…あ") < 0.3, w("あ…はぁ…はぁ…はぁ…あ"))
    check("正常台词分数高", w("いいからそのまま") >= 0.5, w("いいからそのまま"))
    check("空文本为 0", w("") == 0.0)
    # 正常台词：抽到的高分参考（>=0.5）
    picks = [TC.pick_ref("H", target="あぁ…ちょっと、指、なら、いいけど…っ") for _ in range(10)]
    check("正常台词抽到的参考分数都不低",
          all(w(t) >= 0.5 for _, t in picks if t),
          [(p, w(t)) for p, t in picks[:3]])
    # 纯拟声：允许呼吸参考
    moan_picks = [TC.pick_ref("H", target="はぁ…あ…っ…") for _ in range(6)]
    check("纯拟声句能抽到参考", all(p for p, _ in moan_picks))


    print("[9] H 参考含糊时借用能念清的参考")
    # 把 H 的 CER 全部标差，看是否会借到别的情绪
    saved = dict(TC._REFCER)
    for k in list(TC._REFCER):
        if k[0] in ("h", "接吻"):
            TC._REFCER[k] = 0.9
    p, txt = TC.pick_ref("H", target="あぁ…ちょっと、指、なら、いいけど…っ、そこは、だめ…")
    folder = os.path.basename(os.path.dirname(p or ""))
    # 2026-11-07 用户要求：H 就该用 H 的语气，不要借平静音色（借了听起来就不是 H）
    check("H 仍然用 H 自己的参考", folder.lower() in ("h",), folder)
    TC._REFCER = saved

    TC._REFS_OVERRIDE = ""
    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("全部通过（真实目录 %d 条，其中 %d 条有文本）" % (tot, has))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
