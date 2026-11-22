# -*- coding: utf-8 -*-
"""用 VAD 测参考音频里到底有多少「人声」，而不是看文本猜。

问题：我按文本的"实词分"给参考分级，但文本来自 ASR/训练清单，
      文本写着「いいからそのまま」，音频里可能大部分是喘息 —— 文本骗人。
办法：silero VAD 直接看音频，算出「有声时间占比」。占比低 = 气声为主，不适合念正常台词。

    other\\dev\\train\\venv\\Scripts\\python.exe program\\tools\\ref_vad_scan.py H
"""
import io
import json
import os
import sys
import wave

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
VOICE = os.path.join(PROGRAM, "build_env", "characters", "和纱", "voice")


def dur(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def main():
    from faster_whisper.vad import VadOptions, get_speech_timestamps, collect_chunks
    import numpy as np

    # 用法：ref_vad_scan.py <情绪目录> [--char 角色id]
    args = [a for a in sys.argv[1:]]
    char = ""
    if "--char" in args:
        i = args.index("--char")
        char = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
    folder = args[0] if args else "H"
    voice = (os.path.join(PROGRAM, "build_env", "characters", char, "voice")
             if char else VOICE)
    d = os.path.join(voice, folder)
    out_path = os.path.join(voice, folder, "_refaudio.json")
    print("目录:", d)

    opts = VadOptions(min_silence_duration_ms=250, speech_pad_ms=100)
    rows = []
    for f in sorted(os.listdir(d)):
        if not f.lower().endswith(".wav"):
            continue
        p = os.path.join(d, f)
        try:
            with wave.open(p, "rb") as w:
                sr, ch, sw = w.getframerate(), w.getnchannels(), w.getsampwidth()
                raw = w.readframes(w.getnframes())
            import array
            a = array.array("h")
            a.frombytes(raw)
            if ch > 1:
                a = a[::ch]
            x = np.array(a, dtype=np.float32) / 32768.0
            if sr != 16000:
                # 线性插值重采样到 16k（VAD 的时间戳按 16k 算，直接切片会算错时长）
                n_out = int(len(x) * 16000.0 / sr)
                idx = np.linspace(0, len(x) - 1, n_out)
                x = np.interp(idx, np.arange(len(x)), x).astype(np.float32)
            segs = get_speech_timestamps(x, opts)
            speech = sum(s["end"] - s["start"] for s in segs) / 16000.0
        except Exception as e:
            print("  跳过 %s: %s" % (f, e))
            continue
        total = dur(p)
        rows.append({"wav": f, "dur": round(total, 2),
                     "speech": round(speech, 2),
                     "ratio": round(speech / total, 3) if total else 0.0})
    io.open(out_path, "w", encoding="utf-8", newline="\n").write(
        json.dumps(rows, ensure_ascii=False, indent=1))
    rows.sort(key=lambda r: -r["ratio"])
    print("\n%-12s %6s %7s %7s" % ("文件", "时长", "人声", "占比"))
    for r in rows:
        print("%-12s %6.1f %7.1f %7.2f" % (r["wav"], r["dur"], r["speech"], r["ratio"]))
    n_low = sum(1 for r in rows if r["ratio"] < 0.3)
    print("\n共 %d 条；人声占比 <0.3 的 %d 条（气声为主）" % (len(rows), n_low))
    print("已写:", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
