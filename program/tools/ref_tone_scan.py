# -*- coding: utf-8 -*-
"""参考音频「音色体检」：给每条参考算 响度(dBFS) / 频谱重心(Hz) / 时长，写进 `_reftone.json`。

为什么需要（2026-11-22 用户反馈「语调好奇怪、总是声音很粗」）：
    选参考音频的现有质量门只有 VAD 人声占比 + 时长 + CER，**没有管音色本身**。
    和纱 `委屈巴巴/` 这组 5 条里有 3 条频谱重心只有 1500~1800 Hz（正常说话 3500~4400），
    拿它当参考合成出来就是**又闷又粗**；而模型恰好连着几句「委屈巴巴」，
    听起来就像"声音一直很粗"。
    所以把亮度/响度写成侧车文件，`tts_client._good()` 按它把闷的参考剔掉。

跑（要 numpy，用 GPT-SoVITS 那个运行时或任意装了 numpy 的 python）：
    other\\dev\\tts\\GPT-SoVITS\\runtime\\python.exe program\\tools\\ref_tone_scan.py
    ... --char 和纱        # 只扫一个角色
    ... --dry              # 只打印，不写文件

输出：`<角色>/voice/<情绪>/_reftone.json`
    [{"wav": "...", "dur": 4.32, "db": -13.5, "cent": 3695}, ...]
"""
import argparse
import io
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROGRAM = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROGRAM, "src"))

import datadir  # noqa: E402

DARK_HZ = 2400          # 低于这个看作「闷」（正常说话 3500~4400，哭腔 2900）
QUIET_DB = -28.0        # 低于这个看作「太轻」（太轻的参考容易合成出气声）


def measure(path):
    """返回 (时长秒, 响度 dBFS, 频谱重心 Hz)；读不了返回 None"""
    try:
        with wave.open(path, "rb") as w:
            sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
            raw = w.readframes(n)
        if not sr or not raw:
            return None
        x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        x = x / 32768.0
        # 掐掉首尾静音，否则静音会把重心/响度带偏
        env = np.abs(x)
        if env.max() <= 0:
            return None
        idx = np.where(env > env.max() * 0.12)[0]
        if len(idx) > 100:
            x = x[idx[0]:idx[-1]]
        if len(x) < sr * 0.2:
            return None
        dur = len(x) / float(sr)
        db = 20.0 * np.log10(max(float(np.sqrt((x ** 2).mean())), 1e-9))
        sp = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        fr = np.fft.rfftfreq(len(x), 1.0 / sr)
        m = (fr > 80) & (fr < 16000)
        cent = float((sp[m] * fr[m]).sum() / max(1e-9, sp[m].sum()))
        return dur, db, cent
    except Exception:
        return None


def scan_char(cdir, dry=False):
    vroot = os.path.join(cdir, "voice")
    if not os.path.isdir(vroot):
        return 0, 0
    total = written = 0
    print("\n=== %s ===" % os.path.basename(cdir))
    print("%-12s %4s %10s %12s %8s" % ("情绪", "条", "响度dBFS", "重心Hz", "闷/轻"))
    for emo in sorted(os.listdir(vroot)):
        d = os.path.join(vroot, emo)
        if not os.path.isdir(d):
            continue
        rows = []
        for f in sorted(os.listdir(d)):
            if not f.lower().endswith(".wav"):
                continue
            m = measure(os.path.join(d, f))
            total += 1
            if not m:
                continue
            dur, db, cent = m
            rows.append({"wav": f, "dur": round(dur, 2), "db": round(db, 1),
                         "cent": round(cent)})
        if not rows:
            continue
        dark = sum(1 for r in rows if r["cent"] < DARK_HZ or r["db"] < QUIET_DB)
        cents = [r["cent"] for r in rows]
        dbs = [r["db"] for r in rows]
        print("%-12s %4d %5.1f~%-5.1f %7.0f~%-5.0f %3d/%d" % (
            emo, len(rows), min(dbs), max(dbs), min(cents), max(cents), dark, len(rows)))
        if not dry:
            out = os.path.join(d, "_reftone.json")
            with io.open(out, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(rows, ensure_ascii=False, indent=1))
            written += 1
    return total, written


def main():
    ap = argparse.ArgumentParser(description="参考音频音色体检（响度/亮度）")
    ap.add_argument("--char", default="", help="只扫这个角色（文件夹名）")
    ap.add_argument("--dry", action="store_true", help="只打印，不写 _reftone.json")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    root = os.path.join(datadir.root(), "characters")
    if not os.path.isdir(root):
        print("找不到角色目录：%s" % root)
        return 1
    names = [args.char] if args.char else sorted(
        n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n)))
    total = written = 0
    for n in names:
        cdir = os.path.join(root, n)
        if not os.path.isdir(cdir):
            print("跳过（不存在）：%s" % n)
            continue
        a, b = scan_char(cdir, args.dry)
        total += a
        written += b
    print("\n共 %d 条参考，%d 个目录写了 _reftone.json（闷/轻门槛：重心<%dHz 或 响度<%.0fdBFS）"
          % (total, written, DARK_HZ, QUIET_DB))
    return 0


if __name__ == "__main__":
    sys.exit(main())
