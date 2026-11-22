# -*- coding: utf-8 -*-
"""语音缓存自测：命中/未命中、修剪不误删、启动不再清空。

    cd program && .\\build_env\\venv\\Scripts\\python.exe tools\\test_tts_cache.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

# 先把缓存目录指到临时目录，别动真实缓存
TMP = tempfile.mkdtemp(prefix="wa2_ttscache_")
import tts_client as TC  # noqa: E402

TC._TTS_CACHE_DIR = TMP

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:220]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def put(name, size, age_s=0.0):
    p = os.path.join(TMP, name)
    with open(p, "wb") as f:
        f.write(b"x" * size)
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def main():
    print("[1] 缓存 key：文本/语种/情绪任一不同就换一个文件")
    a = TC._tts_cache_path("こんばんは", "ja", "calm")
    b = TC._tts_cache_path("こんばんは", "ja", "calm")
    c = TC._tts_cache_path("こんばんは", "ja", "毒舌")
    d = TC._tts_cache_path("こんばんは", "zh", "calm")
    e = TC._tts_cache_path("おはよう", "ja", "calm")
    check("同样的参数 → 同一个文件", a == b, (a, b))
    check("换情绪 → 不同", a != c)
    check("换语种 → 不同", a != d)
    check("换文本 → 不同", a != e)
    check("文件名在缓存目录里", os.path.dirname(a) == TMP, a)

    print("[2] 修剪：按年龄删")
    put("old1.wav", 1000, age_s=60 * 86400)      # 60 天前
    put("old2.wav", 1000, age_s=50 * 86400)
    put("fresh.wav", 1000)
    n = TC.prune_tts_cache(max_mb=100, max_days=45)
    left = set(os.listdir(TMP))
    check("删掉超龄的", n == 2, (n, left))
    check("保留新的", "fresh.wav" in left, left)

    print("[3] 修剪：按总量删，但永远保留最近 3 条")
    for f in os.listdir(TMP):
        os.remove(os.path.join(TMP, f))
    names = []
    for i in range(6):
        put("k%d.wav" % i, 400 * 1024, age_s=(6 - i) * 600)   # k5 最新
        names.append("k%d.wav" % i)
    n = TC.prune_tts_cache(max_mb=1, max_days=45)               # 限 1MB，但 3 条×400KB 必须留
    left = set(os.listdir(TMP))
    check("最新 3 条保留", all(x in left for x in names[-3:]), left)
    check("更老的删掉了", not any(x in left for x in names[:3]), left)

    print("[4] 空目录 / 不存在的目录不炸")
    shutil.rmtree(TMP, ignore_errors=True)
    check("目录不存在时返回 0", TC.prune_tts_cache() == 0)

    print("[5] 启动流程不再清空缓存（源码级检查）")
    src = io.open(os.path.join(os.path.dirname(HERE), "src", "main.py"),
                  encoding="utf-8").read()
    check("main.py 启动时调的是 prune_tts_cache", "prune_tts_cache()" in src)
    # 只看真实调用（注释里提到这个名字是正常的，那是在说明历史）
    import re as _re
    calls = _re.findall(r"_tc\.clear_tts_cache\s*\(", src)
    check("main.py 不再调 clear_tts_cache", not calls, calls)
    check("clear_tts_cache 仍保留（清理面板要用）",
          "def clear_tts_cache" in io.open(
              os.path.join(os.path.dirname(HERE), "src", "tts_client.py"),
              encoding="utf-8").read())


    print("[6] 重放要复用原播放的参数（否则同进程内也命不中）")
    voice = io.open(os.path.join(os.path.dirname(HERE), "src", "ui", "wt-voice.js"),
                    encoding="utf-8").read()
    back = io.open(os.path.join(os.path.dirname(HERE), "src", "ui", "wt-backlog.js"),
                   encoding="utf-8").read()
    chat = io.open(os.path.join(os.path.dirname(HERE), "src", "ui", "wt-chat.js"),
                   encoding="utf-8").read()
    check("fetchVoice 记录 __lastVoiceReq", "__lastVoiceReq" in voice)
    check("回想有 setVoiceOnLast", "function setVoiceOnLast" in back)
    check("回想重放优先用 dataset.voice", "row.dataset.voice" in back)
    check("聊天收到回复后写回参数", "setVoiceOnLast(window.__lastVoiceReq)" in chat)
    check("重放不再写死 lang=ja", "&lang=ja&" not in back)
    check("有缓存命中日志", "TTS 缓存命中" in io.open(
        os.path.join(os.path.dirname(HERE), "src", "tts_client.py"),
        encoding="utf-8").read())


    print("[7] 缓存 key 含版本与采样参数（改参数后不会再放旧音频）")
    check("有版本号", isinstance(getattr(TC, "_CACHE_VERSION", None), int) and TC._CACHE_VERSION >= 2,
          getattr(TC, "_CACHE_VERSION", None))
    k1 = TC._tts_cache_path("テスト", "ja", "H")
    k2 = TC._tts_cache_path("テスト", "ja", "H")
    check("同参数同 key", k1 == k2)
    import copy
    old_prm = copy.deepcopy(TC.EMOTION_PARAMS["H"])
    TC.EMOTION_PARAMS["H"]["temperature"] = round(old_prm["temperature"] + 0.1, 2)
    k3 = TC._tts_cache_path("テスト", "ja", "H")
    TC.EMOTION_PARAMS["H"] = old_prm
    check("改温度后 key 变化", k3 != k1, (k1, k3))


    print("[8] 修剪时保留最近 3 条语音（重启也不清）")
    d = tempfile.mkdtemp(prefix="wa2_keep3_")
    TC._TTS_CACHE_DIR = d
    # 造 6 个文件，大小都超限，但最近 3 个必须留下
    for i in range(6):
        p = os.path.join(d, "c%d.wav" % i)
        io.open(p, "wb").write(b"x" * (400 * 1024))
        at = time.time() - (6 - i) * 600      # c5 最新
        os.utime(p, (at, at))
    TC.prune_tts_cache(max_mb=1, max_days=45)
    left = sorted(os.listdir(d))
    check("最新 3 条还在", all(("c%d.wav" % i) in left for i in (3, 4, 5)), left)
    check("更老的被删了", "c0.wav" not in left, left)

    print("[9] 命中缓存时不去加载/切换权重（2026-11-22「回想重放又要重新加载」）")
    d2 = tempfile.mkdtemp(prefix="wa2_hit_")
    TC._TTS_CACHE_DIR = d2
    hp = TC._tts_cache_path("テスト用の文です", "ja", "calm")
    os.makedirs(os.path.dirname(hp), exist_ok=True)
    io.open(hp, "wb").write(b"FAKE-CACHED")
    calls = {"n": 0}
    real_ensure, real_ready, real_post = TC._ensure_char_model, TC._ready, TC._post

    def _counting():
        calls["n"] += 1
        return True

    TC._ensure_char_model = _counting
    TC._post = lambda *a, **k: b"NEW-AUDIO"
    TC._ready = True
    try:
        data = TC._synthesize_inner("テスト用の文です", "ja", 3000, "calm", port=0)
    finally:
        TC._ensure_char_model, TC._ready, TC._post = real_ensure, real_ready, real_post
    check("命中时直接返回缓存", data == b"FAKE-CACHED", data)
    check("命中时没调 _ensure_char_model（不切权重）", calls["n"] == 0, calls["n"])

    os.remove(hp)
    calls["n"] = 0
    TC._ensure_char_model = _counting
    TC._post = lambda *a, **k: b"NEW-AUDIO"
    TC._ready = True
    try:
        TC._synthesize_inner("别の未缓存文本です", "ja", 50, "calm", port=0)
    finally:
        TC._ensure_char_model, TC._ready, TC._post = real_ensure, real_ready, real_post
    check("没缓存时才去切权重", calls["n"] == 1, calls["n"])
    shutil.rmtree(d2, ignore_errors=True)

    print()
    if FAILS:
        print("失败 %d 项：%s" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
