# -*- coding: utf-8 -*-
"""把整理好的语音素材导入成「参考音频」：<角色>/voice/<情绪>/*.wav + 同名 .txt

    python tools/import_voice_refs.py --char 雪菜 --emo H --src "E:\\IDM下载目录\\H"
    python tools/import_voice_refs.py --char 雪菜 --emo H --src ... --asr   # 缺文本的用 ASR 补
    python tools/import_voice_refs.py --char 雪菜 --emo H --src ... --dry-run

文件名形如 `2031_0445_02.OGG`（场景_行号_第几遍）：
  · 场景/行号能对上 `other/dev/语音训练集/台词库.json` 的，直接用剧本原文当 prompt_text；
  · 对不上（H 场景的台词不在台词库里）时，加 `--asr` 用 faster-whisper 转写
    （ASR 用 GPT-SoVITS 的 runtime python 跑，模型是本地已缓存的 large-v3）。

产出的 .txt 是 GPT-SoVITS 的 prompt_text —— 没有它，参考音频只能当兜底，
念出来的语气会不对。导入完再跑一次 `tools/ref_vad_scan.py <情绪> --char <角色>`
生成 _refaudio.json（人声占比 / 时长），选参考时会按它分层。
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
ROOT = os.path.dirname(PROGRAM)
GSV = os.path.join(ROOT, "other", "dev", "tts", "GPT-SoVITS")
RUNTIME = os.path.join(GSV, "runtime")
FFMPEG = os.path.join(RUNTIME, "ffmpeg.exe")
RUNTIME_PY = os.path.join(RUNTIME, "python.exe")
LIB = os.path.join(ROOT, "other", "dev", "语音训练集", "台词库.json")

SRC_EXT = (".ogg", ".wav", ".mp3", ".flac", ".m4a", ".opus")
# WA2 的语音文件名：场景_行号_第几遍（第几遍用来区分角色，比如 _02 是雪菜那条）
RE_NAME = re.compile(r"^(\d+)_(\d+)_(\d+)$", re.I)


def load_lib(path=LIB):
    """台词库 → {(scene, line): text}"""
    if not os.path.isfile(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception as e:
        print("台词库读不了：%s" % e)
        return {}
    out = {}
    for e in data:
        try:
            key = (str(e.get("scene") or ""), int(e.get("line") or -1))
        except Exception:
            continue
        t = clean(e.get("text") or "")
        if t:
            out[key] = t
    return out


def clean(text):
    """剧本原文 → prompt_text：去引号、换行拼起来

    台词库里的换行有两种写法：真换行，和**字面上的反斜杠+n**（导出时没转义好）。
    两种都得去掉，否则 prompt_text 里会带着 "\n" 两个字符。
    """
    s = (text or "").replace("\r", "").replace("\\n", " ").strip()
    if s.startswith("「"):
        s = s[1:]
    if s.endswith("」"):
        s = s[:-1]
    return s.replace("\n", "").replace("\u3000", " ").strip()


def text_from_name(stem, lib):
    m = RE_NAME.match(stem)
    if not m:
        return ""
    return lib.get((m.group(1), int(m.group(2))), "")


def convert(src, dst):
    cmd = [FFMPEG, "-y", "-v", "error", "-i", src,
           "-ac", "1", "-ar", "32000", "-sample_fmt", "s16", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip()[:160] or "ffmpeg 失败")
    return dst


def asr_missing(dirpath, stems, lang="ja"):
    """用 runtime 的 faster-whisper 转写 dirpath 下缺文本的 wav → {stem: text}"""
    if not os.path.isfile(RUNTIME_PY):
        print("没有 GPT-SoVITS runtime，跳过 ASR")
        return {}
    todo = [s for s in stems if not os.path.isfile(os.path.join(dirpath, s + ".txt"))]
    if not todo:
        return {}
    print("ASR 转写 %d 条（CPU large-v3，慢一点）…" % len(todo))
    script = os.path.join(HERE, "_asr_refs_tmp.py")
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(ASR_SCRIPT)
    listp = os.path.join(dirpath, "_asr_in.json")
    outp = os.path.join(dirpath, "_asr_out.json")
    json.dump({"dir": dirpath, "files": [s + ".wav" for s in todo], "out": outp},
              open(listp, "w", encoding="utf-8"), ensure_ascii=False)
    try:
        r = subprocess.run([RUNTIME_PY, script, listp], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print("ASR 失败：%s" % ((r.stderr or "").strip()[-300:]))
            return {}
        data = json.load(open(outp, encoding="utf-8"))
    finally:
        for p in (script, listp, outp):
            try:
                os.remove(p)
            except OSError:
                pass
    return {os.path.splitext(k)[0]: clean(v) for k, v in data.items() if (v or "").strip()}


ASR_SCRIPT = r'''# -*- coding: utf-8 -*-
import json, os, sys, io
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from faster_whisper import WhisperModel
spec = json.load(open(sys.argv[1], encoding="utf-8"))
d, files, out = spec["dir"], spec["files"], spec["out"]
m = WhisperModel("large-v3", device="cpu", compute_type="int8")
res = {}
for i, f in enumerate(files, 1):
    p = os.path.join(d, f)
    try:
        segs, _info = m.transcribe(p, language="ja", beam_size=5, vad_filter=False)
        res[f] = "".join(s.text for s in segs).strip()
    except Exception as e:
        res[f] = ""
    print("  [%d/%d] %s" % (i, len(files), (res[f] or "")[:40]), flush=True)
json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
'''


def main():
    ap = argparse.ArgumentParser(description="导入参考音频（wav + prompt_text）")
    ap.add_argument("--char", required=True, help="角色 id（characters 下的文件夹名）")
    ap.add_argument("--emo", required=True, help="情绪目录名，例如 H / calm / 毒舌")
    ap.add_argument("--src", required=True, help="素材目录（ogg/wav/mp3…）")
    ap.add_argument("--asr", action="store_true", help="缺文本的用 faster-whisper 转写")
    ap.add_argument("--index", default=LIB, help="台词库 json 路径")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = ap.parse_args()

    src = os.path.abspath(args.src)
    if not os.path.isdir(src):
        print("素材目录不存在：%s" % src)
        return 2
    dst = os.path.join(PROGRAM, "build_env", "characters", args.char, "voice", args.emo)
    if not os.path.isdir(os.path.dirname(dst)):
        print("角色文件夹不存在：%s" % os.path.dirname(dst))
        return 2
    lib = load_lib(args.index)
    print("台词库：%d 条；素材：%s" % (len(lib), src))
    print("目标：%s" % dst)
    os.makedirs(dst, exist_ok=True)

    files = sorted(f for f in os.listdir(src) if os.path.splitext(f)[1].lower() in SRC_EXT)
    stems = [os.path.splitext(f)[0] for f in files]
    made = skipped = failed = from_lib = 0
    no_text = []
    for f in files:
        stem = os.path.splitext(f)[0]
        out_wav = os.path.join(dst, stem + ".wav")
        if os.path.isfile(out_wav):
            skipped += 1
        elif args.dry_run:
            made += 1
        else:
            try:
                convert(os.path.join(src, f), out_wav)
                made += 1
            except Exception as e:
                failed += 1
                print("  转换失败 %s：%s" % (f, e))
                continue
        t = text_from_name(stem, lib)
        tp = os.path.join(dst, stem + ".txt")
        if t and not os.path.isfile(tp) and not args.dry_run:
            open(tp, "w", encoding="utf-8", newline="\n").write(t + "\n")
            from_lib += 1
        if not t:
            no_text.append(stem)

    print("\n转换 %d（跳过已有 %d、失败 %d）" % (made, skipped, failed))
    print("剧本原文直接可用：%d 条；缺文本：%d 条" % (from_lib, len(no_text)))
    if no_text and args.asr:
        got = asr_missing(dst, stems, "ja")
        n = 0
        for stem, t in got.items():
            if not t:
                continue
            tp = os.path.join(dst, stem + ".txt")
            if not os.path.isfile(tp):
                open(tp, "w", encoding="utf-8", newline="\n").write(t + "\n")
                n += 1
        print("ASR 补了 %d 条" % n)
        no_text = [s for s in no_text if not os.path.isfile(os.path.join(dst, s + ".txt"))]
    elif no_text:
        print("缺文本的（只会当兜底参考）：%s" % "、".join(no_text[:8]))
        print("想要它们也能当正常参考，加 --asr 再跑一次（已转换的 wav 不会重转）")

    total = len([x for x in os.listdir(dst) if x.lower().endswith(".wav")])
    with_txt = len([x for x in os.listdir(dst) if x.lower().endswith(".txt")])
    print("\n目录现状：%d 条 wav，%d 条有文本" % (total, with_txt))
    print("下一步：python tools/ref_vad_scan.py %s --char %s" % (args.emo, args.char))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
