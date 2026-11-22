# -*- coding: utf-8 -*-
"""参考音频挑选的自测（不联网、不读真音频）。

为什么单独测（2026-11-22 用户反馈「语调好奇怪、总是声音很粗」）：
    · 和纱 `委屈巴巴/` 5 条参考里有 3 条频谱重心只有 1400~1800 Hz（正常说话 3500~4400），
      选到它们合成就又闷又粗；原来的质量门只看 VAD/时长/CER，**没有管音色**。
    · 未知情绪（例如目录是空的「气喘吁吁」）的兜底会把**所有**参考都抓进来，
      包括 H / 接吻 / 呼吸那 151 条气声 —— 念普通台词又粗又糊。
    · 参考的 prompt_text 里有 25 条夹着**字面反斜杠 n**，送进 GPT-SoVITS 会让语调怪。
    · 「紧张」原来会借「争执」（吵架喊叫）的参考。

这个测试用临时目录 + 假侧车（_refaudio.json / _reftone.json，不需要真 wav），
只验挑选逻辑，不碰真数据。

跑：
    cd program
    build_env/venv/Scripts/python.exe tools/test_tts_refs.py
"""
import io
import json
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import tts_client as T  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:240]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"")


def ref(voice, emo, name, txt, cent=3700, db=-13.0, ratio=0.9, dur=4.5):
    """造一条假参考：空 wav + txt，并把侧车数据攒进 globals 列表"""
    touch(os.path.join(voice, emo, name + ".wav"))
    if txt is not None:
        write(os.path.join(voice, emo, name + ".txt"), txt)
    ref.audio.setdefault(emo, []).append(
        {"wav": name + ".wav", "ratio": ratio, "dur": dur})
    ref.tone.setdefault(emo, []).append(
        {"wav": name + ".wav", "cent": cent, "db": db})


ref.audio = {}
ref.tone = {}


def flush(voice):
    for emo in os.listdir(voice):
        d = os.path.join(voice, emo)
        if not os.path.isdir(d):
            continue
        write(os.path.join(d, "_refaudio.json"),
              json.dumps(ref.audio.get(emo, []), ensure_ascii=False))
        write(os.path.join(d, "_reftone.json"),
              json.dumps(ref.tone.get(emo, []), ensure_ascii=False))


def main():
    # ---------- 1) prompt_text 清洗 ----------
    print("[1] 参考音频的 prompt_text 清洗")
    dirty = "すぐそうやって自分が上だって決めつける！\\n大人げないのはどっちだ！"
    clean = T._clean_prompt(dirty)
    check("字面反斜杠 n 被换成空格", "\\n" not in clean, clean)
    check("两句连成一句、内容还在", "決めつける" in clean and "大人げない" in clean, clean)
    check("真换行 / 制表 / 连续空白都压成一个空格",
          T._clean_prompt("あ\nい\tう  え") == "あ い う え",
          repr(T._clean_prompt("あ\nい\tう  え")))
    check("首尾空白去掉", T._clean_prompt("  こんばんは  ") == "こんばんは")

    # ---------- 2) 音色门 ----------
    print("[2] 音色门：闷 / 太轻的参考不许用")
    tmp = tempfile.mkdtemp(prefix="wa2_refs_")
    voice = os.path.join(tmp, "voice")
    try:
        ref.audio, ref.tone = {}, {}
        ref(voice, "calm", "good", "普通に話している台詞です", cent=3700, db=-13)
        ref(voice, "calm", "dark", "こもった声の台詞です", cent=1500, db=-13)   # 闷
        ref(voice, "calm", "quiet", "すごく小さい声の台詞です", cent=3700, db=-32)  # 太轻
        ref(voice, "h", "breath", "少し息の荒い台詞です", cent=1500, db=-20)       # 气声：豁免
        ref(voice, "紧张", "tight", "緊張した声の台詞です", cent=1600, db=-13)   # 闷 → 本组没得选
        os.makedirs(os.path.join(voice, "气喘吁吁"), exist_ok=True)              # 空组
        flush(voice)

        T._REFS_OVERRIDE = voice
        T.load_refs()

        got = set()
        for _ in range(60):
            r, _t = T.pick_ref("calm", target="普通の台詞です")
            got.add(os.path.basename(r) if r else "")
        check("calm 不会抽到闷的参考", "dark.wav" not in got, got)
        check("calm 不会抽到太轻的参考", "quiet.wav" not in got, got)
        check("calm 抽得到正常参考", "good.wav" in got, got)

        hgot = set()
        for _ in range(30):
            r, _t = T.pick_ref("H", target="普通の台詞です")
            hgot.add(os.path.basename(r) if r else "")
        check("H 的气声参考不受音色门影响（豁免）", "breath.wav" in hgot, hgot)

        tgot = set()
        for _ in range(40):
            r, _t = T.pick_ref("紧张", target="普通の台詞です")
            t_got = os.path.basename(r) if r else ""
            tgot.add((os.path.basename(os.path.dirname(r)) if r else "", t_got))
        check("紧张：自己的参考都闷 → 去别的「说话类」组借",
              all(d != "紧张" for d, _f in tgot), tgot)
        check("紧张：借来的参考不能是闷的", all(f != "tight.wav" for _d, f in tgot), tgot)
        check("紧张：绝不从 h（气声）借", all(d != "h" for d, _f in tgot), tgot)

        bgot = set()
        for _ in range(40):
            r, _t = T.pick_ref("气喘吁吁", target="普通の台詞です")
            bgot.add(os.path.basename(os.path.dirname(r)) if r else "")
        check("空组（气喘吁吁）不会抓到 h 的气声参考", "h" not in bgot, bgot)
        check("空组（气喘吁吁）能借到说话类的参考", bool(bgot) and "h" not in bgot, bgot)

        print("[3] 分组与兜底白名单")
        check("紧张不再借争执（吵架喊叫）", "争执" not in T.REF_GROUPS.get("紧张", []),
              T.REF_GROUPS.get("紧张"))
        check("沉重借自怨自艾而不是吵架", T.REF_GROUPS.get("沉重")[0] == "自怨自艾",
              T.REF_GROUPS.get("沉重"))
        bad_safe = [x for x in T.SAFE_BORROW if x.lower() in ("h", "接吻", "睡觉时的呼吸", "争执", "回嘴")]
        check("跨情绪借的白名单里没有气声/喊叫组", not bad_safe, T.SAFE_BORROW)
    finally:
        T._REFS_OVERRIDE = ""
        try:
            T.load_refs()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)

    # ---------- 4) 角色语音权重确定性 ----------
    print("[4] 角色语音权重配对是确定的")
    tmp2 = tempfile.mkdtemp(prefix="wa2_vm_")
    try:
        vm = os.path.join(tmp2, "voice_model")
        os.makedirs(vm)
        for n in ("b-e5.ckpt", "a-e10.ckpt", "b_e4.pth", "a_e8.pth"):
            touch(os.path.join(vm, n))
        m1 = T._pick_weights(vm)
        m2 = T._pick_weights(vm)
        check("两次调用结果一致", m1 == m2, (m1, m2))
        check("按文件名排序取第一个（确定性）",
              os.path.basename(m1.get("gpt") or "") == "a-e10.ckpt" and
              os.path.basename(m1.get("sovits") or "") == "a_e8.pth", m1)
        check("目录不存在返回空", T._pick_weights(os.path.join(tmp2, "nope")) == {})
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), " / ".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
