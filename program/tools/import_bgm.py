# -*- coding: utf-8 -*-
"""把解包目录里的 41 首 BGM 全部转成 mp3 并接进曲库。

映射规则：
  · 原曲库里已有的 8 首（menu/kazusa/calm/warm/sad/toukanai/tense/answer）
    用同一首 FLAC 重新压一遍，覆盖原来的 mp3（旧的是低码率，重压后音质更好）；
  · 其余 33 首新增为 id 8..，文件名 bgm_nNN.mp3，并给每首标一个「氛围槽」；
  · catalog.json 里每首带 slot 字段，前端按槽轮播（以后加歌只改 catalog 就行）。
"""
import io
import json
import os
import re
import subprocess

SRC = r"E:\IDM下载目录\解包\BGM"
OUT = r"program/res/assets/snd"
FFMPEG = r"other/dev/tts/GPT-SoVITS/runtime/ffmpeg.exe"

# 原曲库 8 首：源文件 → 目标文件名（slot 沿用）
LEGACY = [
    ("主菜单背景音乐.flac", "bgm_menu.mp3", "menu"),
    ("POWDER SNOW PIANO 〜kazusa〜 - AQUAPLUS.flac", "bgm_kazusa.mp3", "kazusa"),
    ("静かな冬の夜 - AQUAPLUS.flac", "bgm_calm.mp3", "calm"),
    ("穏やかに過ぎゆく時 (缓缓而逝的时间) - AQUAPLUS.flac", "bgm_warm.mp3", "warm"),
    ("ためいき (叹息) - AQUAPLUS.flac", "bgm_sad.mp3", "sad"),
    ("届かない恋 (无法传达的爱恋 Live at CampusFes)(Piano version) - AQUAPLUS.flac", "bgm_toukanai.mp3", "sad"),
    ("氷の刃 (冰之刃) - AQUAPLUS.flac", "bgm_tense.mp3", "tense"),
    ("Answer - AQUAPLUS.flac", "bgm_answer.mp3", "answer"),
]

# 其余 33 首：源文件 → (显示名, slot)
EXTRA = [
    ("After All〜綴る想い〜 (instrumental) - AQUAPLUS.flac", "After All〜綴る想い〜 (instrumental)", "sad"),
    ("boy meets girl and girl - AQUAPLUS.flac", "boy meets girl and girl", "comic"),
    ("closing (Acoustic Guitar) - AQUAPLUS.flac", "closing (Acoustic Guitar)", "sad"),
    ("closing instrumental (Instrumental) - AQUAPLUS.flac", "closing (instrumental)", "sad"),
    ("Dear Friends - AQUAPLUS.flac", "Dear Friends", "warm"),
    ("Honesty - AQUAPLUS.flac", "Honesty", "warm"),
    ("POWDER SNOW Guitar solo - AQUAPLUS.flac", "POWDER SNOW Guitar solo", "kazusa"),
    ("POWDER SNOW PIANO 2010 - AQUAPLUS.flac", "POWDER SNOW PIANO 2010", "kazusa"),
    ("SLAPSTICK STREET - AQUAPLUS.flac", "SLAPSTICK STREET", "comic"),
    ("Snowfalls - AQUAPLUS.flac", "Snowfalls", "calm"),
    ("Sunshine Day - AQUAPLUS.flac", "Sunshine Day", "warm"),
    ("Twinkle Snow (Piano quartet) - AQUAPLUS.flac", "Twinkle Snow (Piano quartet)", "answer"),
    ("Twinkle Snow instrumental (Instrumental) - AQUAPLUS.flac", "Twinkle Snow (instrumental)", "answer"),
    ("あの頃のように - AQUAPLUS.flac", "あの頃のように", "warm"),
    ("いつか見た景色 - AQUAPLUS.flac", "いつか見た景色", "calm"),
    ("イルミネーションタウン - AQUAPLUS.flac", "イルミネーションタウン", "comic"),
    ("キャンドルライト - AQUAPLUS.flac", "キャンドルライト", "calm"),
    ("愛する心 (爱恋之心) - 津田朱里.flac", "愛する心", "warm"),
    ("愛する心instrumental (爱恋之心)(Instrumental) - AQUAPLUS.flac", "愛する心 (instrumental)", "warm"),
    ("愛する心Music Box (爱恋之心)(Music Box) - AQUAPLUS.flac", "愛する心 (Music Box)", "warm"),
    ("本当の、嘘 (真正的谎言) - AQUAPLUS.flac", "本当の、嘘", "tense"),
    ("冬の街路樹 - AQUAPLUS.flac", "冬の街路樹", "calm"),
    ("見果てぬ夢 (无止境的梦) - AQUAPLUS.flac", "見果てぬ夢", "sad"),
    ("綺麗で儚いもの - AQUAPLUS.flac", "綺麗で儚いもの", "calm"),
    ("溶け合う心 (相融的心) - AQUAPLUS.flac", "溶け合う心", "warm"),
    ("聖夜 - AQUAPLUS.flac", "聖夜", "calm"),
    ("時の魔法 (时间的魔法)(Music Box) - AQUAPLUS.flac", "時の魔法 (Music Box)", "warm"),
    ("誰かが傷ついても (两败俱伤) - AQUAPLUS.flac", "誰かが傷ついても", "tense"),
    ("吐露 - AQUAPLUS.flac", "吐露", "tense"),
    ("心はいつもあなたのそばにinstrumental (我的心永远在你的身边)(Instrumental) - AQUAPLUS.flac",
     "心はいつもあなたのそばに (instrumental)", "warm"),
    ("言葉にできない想い - AQUAPLUS.flac", "言葉にできない想い", "sad"),
    ("優しい嘘Piano (温柔的谎言)(Piano) - AQUAPLUS.flac", "優しい嘘 (Piano)", "sad"),
    ("最後に残るもの (最后残存之物) - AQUAPLUS.flac", "最後に残るもの", "sad"),
]


def convert(src_name, dst_name):
    src = os.path.join(SRC, src_name)
    dst = os.path.join(OUT, dst_name)
    if not os.path.isfile(src):
        return "缺失"
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-i", src,
           "-codec:a", "libmp3lame", "-b:a", "128k", "-map_metadata", "-1", dst]
    r = subprocess.run(cmd, capture_output=True)
    return "ok" if r.returncode == 0 and os.path.isfile(dst) else ("失败 " + r.stderr.decode("utf-8", "replace")[:60])


def main():
    ok, bad = 0, []
    for src_name, dst_name, _slot in LEGACY:
        r = convert(src_name, dst_name)
        if r == "ok":
            ok += 1
        else:
            bad.append((src_name, r))
    for i, (src_name, _disp, _slot) in enumerate(EXTRA, start=8):
        r = convert(src_name, "bgm_n%02d.mp3" % i)
        if r == "ok":
            ok += 1
        else:
            bad.append((src_name, r))

    items = []
    for idx, (src_name, dst_name, slot) in enumerate(LEGACY):
        disp = {
            "bgm_menu.mp3": "主菜单",
            "bgm_kazusa.mp3": "POWDER SNOW 〜kazusa〜",
            "bgm_calm.mp3": "静かな冬の夜",
            "bgm_warm.mp3": "穏やかに過ぎゆく時",
            "bgm_sad.mp3": "ためいき",
            "bgm_toukanai.mp3": "届かない恋 (Piano)",
            "bgm_tense.mp3": "氷の刃",
            "bgm_answer.mp3": "Answer",
        }[dst_name]
        items.append({"id": idx, "file": dst_name, "name": disp, "slot": slot})
    for i, (src_name, disp, slot) in enumerate(EXTRA, start=8):
        items.append({"id": i, "file": "bgm_n%02d.mp3" % i, "name": disp, "slot": slot})

    path = os.path.join(OUT, "catalog.json")
    cat = json.loads(io.open(path, encoding="utf-8").read())
    cat["bgm"] = items
    io.open(path, "w", encoding="utf-8").write(json.dumps(cat, ensure_ascii=False, indent=2))
    print("转换成功 %d 首，失败 %d 首" % (ok, len(bad)))
    for b in bad:
        print("   ", b)
    print("catalog: %d 首 BGM" % len(items))
    from collections import Counter
    print("按槽统计:", dict(Counter(x["slot"] for x in items)))


if __name__ == "__main__":
    main()
