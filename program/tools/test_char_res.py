# -*- coding: utf-8 -*-
"""角色资源接线的自测（不联网、不启动模型/语音）。

跑法：
    cd program
    build_env/venv/Scripts/python.exe tools/test_char_res.py

验的是「导入的东西真的会被读到」这条链路：
  1. char/prompt 里的提示词会被 char_res 读到，删掉就回落
  2. char/worldbook 的 json 能解析成世界书；txt 走整段附加
  3. char/memory 的文本能读到
  4. char/voice 的 wav 能被 tts_client 扫到、并且优先于自带参考音频
  5. 导入立绘时按「变化编号」自动补 emotions 映射
  6. 换了参考音频，TTS 缓存 key 会变（不会播回旧音色）
"""
import json
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import special_files as SF  # noqa: E402
import char_res as CR      # noqa: E402
import tts_client as TTS   # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name +
          (("  " + str(extra)[:300]) if extra and not cond else ""))
    if not cond:
        FAILS.append(name)


def write(path, text, binary=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if binary else "w"
    with open(path, mode, **({} if binary else {"encoding": "utf-8"})) as f:
        f.write(text)
    return path


def bump(path, delta=5):
    """把 mtime 往后拨，保证「改了文件」能被缓存判断出来"""
    t = os.path.getmtime(path) + delta
    os.utime(path, (t, t))


def main():
    tmp = tempfile.mkdtemp(prefix="wa2_char_")
    char_dir = os.path.join(tmp, "char")
    assets = os.path.join(tmp, "assets")
    refs_dir = os.path.join(tmp, "refs_touma")
    os.makedirs(refs_dir, exist_ok=True)
    SF.CHAR_DIR = char_dir
    SF.set_assets_dir(assets)
    CR._CACHE.clear()
    TTS._REFS_OVERRIDE = refs_dir
    TTS._ref_cache = {}

    print("[1] 没导入时一律回落")
    check("prompt 为空", CR.prompt_text() == "")
    check("世界书为空", CR.worldbook() is None and CR.worldbook_text() == "")
    check("记忆书为空", CR.memory_text() == "")
    check("参考音频为空", CR.voice_refs() == [])

    print("\n[2] 提示词：读得到、改得动、删了回落")
    p = write(os.path.join(char_dir, "prompt", "我的提示词.txt"), "你是测试角色。说话只说一个字。")
    check("读到内容", CR.prompt_text() == "你是测试角色。说话只说一个字。")
    write(p, "第二版提示词。")
    bump(p)
    check("改文件后自动重读", CR.prompt_text() == "第二版提示词。", repr(CR.prompt_text()))
    os.remove(p)
    check("删掉后回落为空", CR.prompt_text() == "")

    print("\n[3] 世界书：json 结构 / txt 整段")
    wb = {"entries": [{"name": "测试", "keywords": ["甲"], "content": "乙"}]}
    wj = write(os.path.join(char_dir, "worldbook", "wb.json"), json.dumps(wb, ensure_ascii=False))
    check("json 解析成世界书", (CR.worldbook() or {}).get("entries", [{}])[0].get("name") == "测试")
    check("txt 版为空", CR.worldbook_text() == "")
    os.remove(wj)
    write(os.path.join(char_dir, "worldbook", "设定.md"), "这是整段的设定资料。")
    check("txt 走整段附加", CR.worldbook_text() == "这是整段的设定资料。")
    check("json 版为空", CR.worldbook() is None)

    # 末尾多一行垃圾不该让整本世界书失效。
    # 2026-11-06 真实踩到：worldbook.json 最后多了一个「xx」，json.load 抛 Extra data，
    # 两处加载器都是 except 后静默回落 —— 界面上一切正常，世界书却一条都没注入。
    os.remove(os.path.join(char_dir, "worldbook", "设定.md"))
    write(os.path.join(char_dir, "worldbook", "wb.json"),
          json.dumps(wb, ensure_ascii=False) + "\nxx\n")
    got = CR.worldbook()
    check("末尾有垃圾也能读出来（宽容解析）",
          bool(got) and (got.get("entries") or [{}])[0].get("name") == "测试", got)
    os.remove(os.path.join(char_dir, "worldbook", "wb.json"))   # 后面的用例要「没文件」的状态

    print("\n[4] 记忆书")
    write(os.path.join(char_dir, "memory", "旧事.txt"), "我们约好周末去海边。")
    check("读到记忆书", CR.memory_text() == "我们约好周末去海边。")

    print("\n[5] 参考音频：扫得到、优先用、缓存跟着变")
    w1 = write(os.path.join(char_dir, "voice", "1005_0427_01.wav"), b"RIFF0000", binary=True)
    write(os.path.join(char_dir, "voice", "1005_0427_01.txt"), "こんにちは")
    write(os.path.join(char_dir, "voice", "angry_02.wav"), b"RIFF0001", binary=True)
    refs = CR.voice_refs()
    check("扫到 2 条", len(refs) == 2, repr(refs))
    check("同名 txt 当参考文本", any(t == "こんにちは" for _w, t in refs))
    TTS.load_refs()
    check("通用参考进了 global", len(TTS._IMPORTED["global"]) == 1, repr(TTS._IMPORTED))
    check("带情绪的归到组里", "毒舌" in TTS._IMPORTED["by_emo"], repr(TTS._IMPORTED))
    rp, _rt = TTS.pick_ref("calm")
    check("pick_ref 用导入的那条", rp == w1, repr(rp))
    rp2, _rt2 = TTS.pick_ref("毒舌")
    check("毒舌优先用 angry 那条", os.path.basename(rp2) == "angry_02.wav", repr(rp2))
    key1 = TTS._tts_cache_path("测试", "zh", "calm")
    bump(os.path.join(char_dir, "voice"))
    TTS.load_refs()
    key2 = TTS._tts_cache_path("测试", "zh", "calm")
    check("换参考音频后缓存 key 变了", key1 != key2)

    print("\n[6] 立绘：衣服名/表情名（写进角色自己的 portrait/index.json）")
    idx = {"height": 900, "outfits": {
        "旧衣服": {"code": "001", "label": "旧衣服", "default": "认真.png",
                 "variants": [{"variant": "101", "file": "认真.png"}],
                 "emotions": {"serious": "认真.png"}}}}
    write(os.path.join(assets, "sprites", "index.json"), json.dumps(idx, ensure_ascii=False))
    own_idx = os.path.join(char_dir, "portrait", "index.json")
    r = SF.save_portrait("kaz001212.png", b"\x89PNGfake")
    check("立绘写入成功", r.get("ok") and r.get("emotion") == "angry", repr(r))
    check("按衣服名 + 表情名落盘",
          r.get("outfit") == "旧衣服" and r.get("name") == "生气.png", repr(r))
    check("文件落在角色的 portrait/旧衣服/",
          os.path.isfile(os.path.join(char_dir, "portrait", "旧衣服", "生气.png")), repr(r))
    got = json.load(open(own_idx, encoding="utf-8"))
    o = got["outfits"]["旧衣服"]
    check("emotions 补上 angry", o.get("emotions", {}).get("angry") == "生气.png", repr(o.get("emotions")))
    check("variants 里也有", any(v["file"] == "生气.png" for v in o.get("variants", [])))
    check("code 保留了", o.get("code") == "001", repr(o.get("code")))
    # 同一套衣服再导入同一表情 → 不覆盖，名字加序号
    r2 = SF.save_portrait("kaz001205.png", b"\x89PNGfake2")
    check("同名表情自动加序号", r2.get("name") == "生气2.png", repr(r2))
    # 新衣服：自带索引里没有这个编号 → 建一条「衣服099」
    SF.save_portrait("kaz099115.png", b"\x89PNGfake3")
    got2 = json.load(open(own_idx, encoding="utf-8"))
    check("新衣服也能建条并映射 surprise",
          got2["outfits"]["衣服099"]["emotions"].get("surprise") == "惊讶.png",
          repr(got2["outfits"].get("衣服099")))
    check("自带 assets/sprites/index.json 没被动过",
          "衣服099" not in json.load(open(os.path.join(assets, "sprites", "index.json"),
                                        encoding="utf-8"))["outfits"])

    print("\n[7] synthesize 真的把导入的参考音频传给合成接口")
    import urllib.parse as _up
    import urllib.request as _ur
    calls = []
    TTS._TTS_CACHE_DIR = os.path.join(tmp, "tts_cache")
    TTS._post = lambda path, timeout=30: (calls.append(path), b"RIFFfake")[1]
    TTS._ready = True
    try:
        data = TTS.synthesize("测试一句", "zh", emo="calm")
        check("合成返回数据", data == b"RIFFfake")
        # 只数**合成**请求：数据根指向真实角色时，_ensure_char_model() 还会发
        # 「切 GPT/SoVITS 权重」的 POST（正常行为，不算进合成次数）
        synth = [c for c in calls if "/tts?" in c]
        check("接口只被调一次", len(synth) == 1, "%d（总 POST %d）" % (len(synth), len(calls)))
        q = _up.parse_qs(_ur.urlparse(synth[0]).query)
        check("ref_audio_path 用的是导入那条", q.get("ref_audio_path", [""])[0] == w1,
              q.get("ref_audio_path"))
        check("prompt_text 用的是同名 txt", q.get("prompt_text", [""])[0] == "こんにちは",
              q.get("prompt_text"))
        TTS.synthesize("测试一句", "zh", emo="calm")
        synth2 = [c for c in calls if "/tts?" in c]
        check("第二次走缓存不再合成", len(synth2) == 1, "%d（总 POST %d）" % (len(synth2), len(calls)))
    finally:
        TTS._ready = False

    print("\n[8] 语音边界：依赖判断 / 启动失败复位 / 挂掉后自动重拉")
    import urllib.error
    # 依赖：参考音频在角色 voice/ 里（按情绪分组）才算齐全
    real = {k: getattr(TTS, k) for k in ("API_PY", "PY", "GPT_W", "SOVITS_W")}
    for k in ("API_PY", "PY", "GPT_W", "SOVITS_W"):
        setattr(TTS, k, write(os.path.join(tmp, "deps", k), b"x", binary=True))
    TTS._REFS_OVERRIDE = os.path.join(tmp, "no_refs")      # 分组目录不存在
    check("导入过参考音频 → 依赖算齐全", TTS.available() is True)
    for wav, _t in list(CR.voice_refs()):
        os.remove(wav)
    check("没参考音频 → 依赖不齐", TTS.available() is False)
    TTS._REFS_OVERRIDE = refs_dir
    for k, v in real.items():
        setattr(TTS, k, v)
    # 后面的合成测试要有一条参考音频（前面把导入的都删了）
    write(os.path.join(refs_dir, "calm", "ref.wav"), b"RIFFtest", binary=True)
    TTS.load_refs()

    # 启动失败必须复位 _starting，否则之后永远「启动中」
    old_avail = TTS.available
    TTS.available = lambda: False
    TTS._ready = False
    TTS._starting = False
    try:
        check("依赖缺失时 start() 返回 False", TTS.start() is False)
        check("_starting 已复位", TTS._starting is False)
        check("第二次调用仍然会尝试（不是直接短路）", TTS.start() is False)
    finally:
        TTS.available = old_avail

    # 合成时连不上 → 标记未就绪，下次请求重新拉起；HTTP 错误不重拉
    TTS._ready = True
    TTS._post = lambda path, timeout=30: (_ for _ in ()).throw(urllib.error.URLError("refused"))
    TTS.synthesize("边界测试", "zh", emo="neutral")
    check("连不上时标记未就绪", TTS._ready is False, TTS.status())
    TTS._ready = True
    TTS._post = lambda path, timeout=30: (_ for _ in ()).throw(
        urllib.error.HTTPError("http://x", 500, "err", {}, None))
    TTS.synthesize("边界测试2", "zh", emo="neutral")
    check("HTTP 错误不重拉（服务还活着）", TTS._ready is True, TTS.status())

    print("\n[9] 「查看文件」：一律打开当前角色文件夹里的这一类（自包含）")
    for k in ("prompt", "worldbook", "memory"):
        p = SF.char_file(k, CR.TEXT_EXTS)
        if p:
            os.remove(p)                       # 清掉前面写进去的，测「目录还是空的」分支
    persona = write(os.path.join(tmp, "persona_core.txt"), "默认人设")
    wbjson = write(os.path.join(tmp, "worldbook.json"), json.dumps({"entries": []}))
    sess = os.path.join(tmp, "sessions")
    refs = os.path.join(tmp, "refs")
    os.makedirs(sess, exist_ok=True)
    os.makedirs(refs, exist_ok=True)
    SF.set_default_paths(prompt=persona, worldbook=wbjson, memory=sess, voice=refs)

    t = SF.open_target("prompt")
    check("提示词没文件→开角色文件夹的 prompt/",
          t["source"] == "empty" and t["folder"] == os.path.join(char_dir, "prompt"), t)
    t = SF.open_target("worldbook")
    check("世界书没文件→开角色文件夹的 worldbook/",
          t["source"] == "empty" and t["folder"] == os.path.join(char_dir, "worldbook"), t)
    t = SF.open_target("memory")
    check("记忆书→开角色文件夹的 memory/",
          t["source"] == "empty" and t["folder"] == os.path.join(char_dir, "memory"), t)
    t = SF.open_target("voice")
    check("参考音频→开角色文件夹的 voice/",
          t["source"] == "empty" and t["folder"] == os.path.join(char_dir, "voice"), t)

    SF.save_upload("prompt", "我的提示词.txt", "测试".encode("utf-8"))
    t = SF.open_target("prompt")
    check("有了文件→在角色文件夹里选中它", t["source"] == "imported"
          and os.path.basename(t["select"]) == "我的提示词.txt", t)

    calls, opened = [], []
    old_popen, old_start = SF.subprocess.Popen, os.startfile
    SF.subprocess.Popen = lambda *a, **k: calls.append(a[0])
    os.startfile = lambda p: opened.append(p)
    try:
        r = SF.open_folder("prompt")
        check("open_folder 报 imported", r.get("ok") and r.get("source") == "imported", r)
        check("explorer /select 选中文件",
              bool(calls) and calls[0][0] == "explorer" and "/select," in calls[0][1], calls)
        r = SF.open_folder("memory")
        check("目录型直接打开（角色文件夹）", r.get("ok") and opened
              and os.path.normpath(opened[0]) == os.path.normpath(os.path.join(char_dir, "memory")),
              (r, opened))
    finally:
        SF.subprocess.Popen, os.startfile = old_popen, old_start
    check("未知类型返回空", SF.open_target("nope") is None)

    print("\n[10] status 汇总")
    write(os.path.join(char_dir, "memory", "旧事.txt"), "我们约好周末去海边。")
    st = CR.status()
    check("status 带上文件名", st.get("memory", {}).get("file") == "旧事.txt", repr(st))
    check("status 统计字数", st.get("memory_chars") == len("我们约好周末去海边。"), repr(st))

    print("\n" + ("全部通过" if not FAILS else "失败 %d 项：%s" % (len(FAILS), "、".join(FAILS))))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
