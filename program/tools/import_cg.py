# -*- coding: utf-8 -*-
"""把场景背景图转成 webp 并生成 assets/cg/index.json

场景图放一个目录里，**文件名就是标签**，括号里写时间 / 天气 / 地点细节：

    咖啡厅（白天）.tga              → place=咖啡厅  time=白天
    和纱家别墅（客厅）（黑夜）.tga   → place=和纱家别墅  state=[客厅]  time=黑夜
    商业街的斑马线前（黄昏）（圣诞节）.tga

用法（在 program/ 下）：
    python tools/import_cg.py --src <场景原图目录>
    python tools/import_cg.py --src <目录> --force          # 全部重转
    python tools/import_cg.py --src <目录> --prune          # 顺手删掉源目录里没有的旧图

产出：
    res/assets/cg/<原名>.webp      —— 统一 1280x720 webp（q=90，约 100KB/张）
    res/assets/cg/index.json       —— 地点/时间/天气/细节标签，scene_cg.py 只读它

只支持读图 + 写图，不改源目录。标签解析复用 src/scene_cg.py，别在这里另写一套。
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import scene_cg  # noqa: E402

SRC_EXT = (".tga", ".png", ".jpg", ".jpeg", ".bmp", ".webp")
DEFAULT_DST = os.path.join(ROOT, "res", "assets", "cg")


def list_sources(src):
    out = []
    for f in sorted(os.listdir(src)):
        stem, ext = os.path.splitext(f)
        if ext.lower() not in SRC_EXT:
            continue
        if stem.startswith("."):
            continue
        out.append((stem, os.path.join(src, f)))
    return out


def convert(src_path, dst_path, max_w, quality, force):
    """原图 → webp。已存在且不比源文件旧就跳过，返回 (是否写了, 说明)"""
    from PIL import Image

    if os.path.exists(dst_path) and not force:
        try:
            if os.path.getmtime(dst_path) >= os.path.getmtime(src_path):
                return False, "跳过（已存在）"
        except OSError:
            pass
    im = Image.open(src_path)
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGB")
    elif im.mode == "RGBA":
        # 场景图不需要透明通道，去掉能小一半
        bg = Image.new("RGB", im.size, (0, 0, 0))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    if max_w and im.width > max_w:
        h = int(round(im.height * max_w / float(im.width)))
        im = im.resize((max_w, h), Image.LANCZOS)
    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    im.save(dst_path, "WEBP", quality=quality, method=6)
    return True, "%dx%d" % im.size


def build_index(dst, src_names, prune=False):
    """按文件名生成目录。标签解析全交给 scene_cg.parse_name()

    增量导入必须把**输出目录里已有的图**也带上：以前只按 --src 生成，
    导一批新 CG 会把这之前的目录整个冲掉（血泪教训：2026-11-21 导「新加入cg」
    63 张，把原有 119 条的目录写成只剩 63 条）。--prune 时才算"只保留源目录"。
    """
    stems = list(src_names)
    if not prune and os.path.isdir(dst):
        for f in os.listdir(dst):
            if f.lower().endswith(".webp"):
                stem = os.path.splitext(f)[0]
                if stem not in stems:
                    stems.append(stem)
    items = []
    for stem in stems:
        file = stem + ".webp"
        if not os.path.exists(os.path.join(dst, file)):
            continue
        tags = scene_cg.parse_name(stem)
        kind = "sky" if any(w in tags["place"] for w in scene_cg.SKY_WORDS) else "place"
        items.append({
            "id": stem,
            "file": file,
            "place": tags["place"],
            "kind": kind,
            "time": tags["time"],
            "weather": tags["weather"],
            "state": tags["state"],
        })
    items.sort(key=lambda it: (it["kind"] != "place", it["place"], it["id"]))
    data = {
        "version": 1,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
    }
    path = os.path.join(dst, "index.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return path, items


def main():
    ap = argparse.ArgumentParser(description="场景背景图 → webp + index.json")
    ap.add_argument("--src", help="场景原图所在目录（tga/png/jpg…）；--rebuild 时可省略")
    ap.add_argument("--dst", default=DEFAULT_DST, help="输出目录（默认 res/assets/cg）")
    ap.add_argument("--width", type=int, default=1280, help="最大宽度，0 表示不缩放")
    ap.add_argument("--quality", type=int, default=90, help="webp 质量")
    ap.add_argument("--force", action="store_true", help="已存在也重转")
    ap.add_argument("--prune", action="store_true", help="删掉输出目录里源图已不存在的 webp")
    ap.add_argument("--rebuild", action="store_true",
                    help="只按输出目录里已有的 webp 重建 index.json（不转图）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = ap.parse_args()

    dst = os.path.abspath(args.dst)
    if args.rebuild:
        index, items = build_index(dst, [], prune=False)
        places = sorted({it["place"] for it in items if it["kind"] == "place"})
        print("重建目录：%d 条，地点 %d 个" % (len(items), len(places)))
        print("目录：%s" % index)
        return 0

    src = os.path.abspath(args.src or "")
    if not os.path.isdir(src):
        print("源目录不存在：%s（要只重建目录请用 --rebuild）" % src)
        return 2
    sources = list_sources(src)
    if not sources:
        print("源目录里没有可用的图片（%s）" % "、".join(SRC_EXT))
        return 2
    print("源目录：%s\n共 %d 张，输出到：%s" % (src, len(sources), dst))

    done = skipped = failed = 0
    total = 0
    for i, (stem, path) in enumerate(sources, 1):
        target = os.path.join(dst, stem + ".webp")
        if args.dry_run:
            print("  [%3d/%d] %s" % (i, len(sources), stem))
            continue
        try:
            wrote, note = convert(path, target, args.width, args.quality, args.force)
            if wrote:
                done += 1
                total += os.path.getsize(target)
            else:
                skipped += 1
            if i % 10 == 0 or wrote:
                print("  [%3d/%d] %s  %s" % (i, len(sources), stem, note))
        except Exception as e:                       # noqa: BLE001
            failed += 1
            print("  [%3d/%d] %s  失败：%s" % (i, len(sources), stem, e))

    if args.dry_run:
        return 0

    index, items = build_index(dst, [s for s, _ in sources], prune=args.prune)
    places = sorted({it["place"] for it in items if it["kind"] == "place"})
    print("\n转换 %d 张（跳过 %d，失败 %d），本次写入 %.1f MB" %
          (done, skipped, failed, total / 1048576.0))
    print("目录：%s" % index)
    print("地点 %d 个：%s" % (len(places), "、".join(places)))

    if args.prune:
        keep = {it["file"] for it in items}
        removed = 0
        for f in os.listdir(dst):
            if f.lower().endswith(".webp") and f not in keep:
                os.remove(os.path.join(dst, f))
                removed += 1
                print("  删除旧图：%s" % f)
        if removed:
            print("删掉 %d 张源图已不存在的旧图" % removed)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
