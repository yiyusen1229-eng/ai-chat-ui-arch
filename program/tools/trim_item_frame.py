# -*- coding: utf-8 -*-
"""去掉配置项底框外面的深青色描边（只留亮线）—— 逐行扫描版。

现象：item_frame.png 每帧的亮线外面还有 2px 深青色 (10,81,90) 实心边，
      界面上就是「亮线外有一圈深青色框子」。

做法（比泛洪稳）：
  · 以第 3 帧为基准算出亮线（luma>150）；
  · 对每一行：行内有亮线 → 把亮线左右两侧的像素清成透明；
              行内没有亮线 → 整行清透明（那是圆角外的区域）；
  · 同一套轮廓套到四帧上（几何位置一致）。

用法：python tools/trim_item_frame.py [--dry-run]
"""
import os
import sys

from PIL import Image

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(BASE, "res", "assets", "ui", "special", "item_frame.png")


def _luma(px):
    return (px[0] * 299 + px[1] * 587 + px[2] * 114) / 1000.0


def scan_bounds(im, frame_index=2, thresh=150):
    """返回每行的 (min_x, max_x)；没有亮线的行返回 None"""
    w, h = im.size
    fh = h // 4
    y0 = frame_index * fh
    rows = []
    for y in range(y0, y0 + fh):
        xs = [x for x in range(w)
              if im.getpixel((x, y))[3] > 180 and _luma(im.getpixel((x, y))) > thresh]
        rows.append((min(xs), max(xs)) if xs else None)
    return rows, y0, fh


def main():
    dry = "--dry-run" in sys.argv
    im = Image.open(SRC).convert("RGBA")
    rows, y0, fh = scan_bounds(im)
    px = im.load()
    cleared = 0
    for f in range(4):
        fy = f * fh
        for i, b in enumerate(rows):
            y = fy + i
            if b is None:
                for x in range(im.width):
                    if px[x, y][3] != 0:
                        px[x, y] = (0, 0, 0, 0)
                        cleared += 1
                continue
            lo, hi = b
            for x in list(range(0, lo)) + list(range(hi + 1, im.width)):
                if px[x, y][3] != 0:
                    px[x, y] = (0, 0, 0, 0)
                    cleared += 1
    print("清除像素：%d 个" % cleared)
    if dry:
        out = os.path.join(BASE, "work_scratch", "item_frame_trimmed.png")
        im.save(out)
        print("dry-run →", out)
        return 0
    im.save(SRC)
    print("已写回", SRC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
