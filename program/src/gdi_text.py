# -*- coding: utf-8 -*-
"""用 Windows GDI 渲染文字 —— 系统字体兜底方案
- 微软雅黑 56px 光栅化（2 倍超采样）
- 形态学膨胀生成硬边描边（中灰色）
- 高斯模糊做圆化
- 白芯提纯（纯 PIL 实现，不依赖 numpy）
"""
import ctypes
import hashlib
import io
import os
from ctypes import wintypes

from PIL import Image, ImageFilter

gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

GB2312_CHARSET = 134
ANTIALIASED_QUALITY = 4
OUT_TT_ONLY_PRECIS = 7
TRANSPARENT = 1

# ---- 字体: 汉化字模由 微软雅黑 Bold 生成(字形 IoU 比对 0.596) ----
FR_PRIVATE = 0x10
_FONT_FILE = r"C:/Windows/Fonts/msyh.ttc"
FALLBACK_FONT = r"C:/Windows/Fonts/msyh.ttc"
FACE = "微软雅黑"
try:
    if os.path.exists(_FONT_FILE):
        if gdi32.AddFontResourceExW(ctypes.c_wchar_p(_FONT_FILE), FR_PRIVATE, 0):
            FACE = "微软雅黑"
except Exception:
    pass

# ---- 可调参数 ----
SIZE = 56
OUTLINE_W = 3
OUTLINE_COLOR = (36, 51, 61)
COLOR = (255, 255, 255)
BODY_BLUR = 0.0
EDGE_BLUR = 0.0
WHITE_BOOST = 0.0
PAD = 3

# 模拟游戏位图字模: 先按 30px 渲染再放大
EMULATE_BITMAP = False
BITMAP_SIZE = 30


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.DWORD]
gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wintypes.SIZE)]
gdi32.TextOutW.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.LPCWSTR, ctypes.c_int]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


_PIL_FONT_CACHE = {}




_CMAP = None
_FALLBACK_CMAP = None


def _load_cmaps():
    global _CMAP, _FALLBACK_CMAP
    if _CMAP is not None:
        return
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        _CMAP = set()
        _FALLBACK_CMAP = set()
        return
    def cmap_of(path):
        try:
            if path.lower().endswith((".ttc", ".otc")):
                f = TTFont(path, fontNumber=0, lazy=True)
            else:
                f = TTFont(path, lazy=True)
            s = set()
            for t in f["cmap"].tables:
                s.update(t.cmap.keys())
            f.close()
            return s
        except Exception:
            return set()
    _CMAP = cmap_of(_FONT_FILE)
    _FALLBACK_CMAP = cmap_of(FALLBACK_FONT)


def font_for_char(ch):
    """返回该字应使用的字体文件"""
    _load_cmaps()
    if not _CMAP:
        return _FONT_FILE
    if ord(ch) in _CMAP:
        return _FONT_FILE
    if _FALLBACK_CMAP and ord(ch) in _FALLBACK_CMAP:
        return FALLBACK_FONT
    return _FONT_FILE


def split_runs(text):
    """把一行按所用字体切成若干段: [(字体, 子串), ...]"""
    runs = []
    for ch in text:
        fp = font_for_char(ch)
        if runs and runs[-1][0] == fp:
            runs[-1][1].append(ch)
        else:
            runs.append([fp, [ch]])
    return [(fp, "".join(cs)) for fp, cs in runs]

def _pil_font(size, path=None):
    """按字号+字体文件取 PIL 字体对象; 没有字体文件时返回 None"""
    fpath = path or _FONT_FILE
    if not (fpath and os.path.exists(fpath)):
        return None
    key = (int(size), fpath)
    if key not in _PIL_FONT_CACHE:
        try:
            from PIL import ImageFont
            _PIL_FONT_CACHE[key] = ImageFont.truetype(fpath, int(size))
        except Exception:
            _PIL_FONT_CACHE[key] = None
    return _PIL_FONT_CACHE[key]


def _make_dc(w, h):
    screen = user32.GetDC(0)
    hdc = gdi32.CreateCompatibleDC(screen)
    bmp = gdi32.CreateCompatibleBitmap(screen, w, h)
    gdi32.SelectObject(hdc, bmp)
    user32.ReleaseDC(0, screen)
    return hdc, bmp


def _read_pixels(hdc, bmp, w, h):
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(hdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    return buf.raw


def measure(text, size=None, face=None):
    """与补丁一致: 每个字固定占 size 像素"""
    _sz = size or SIZE
    return (len(text) * _sz, _sz)
    size = size or SIZE
    face = face or FACE
    ft = _pil_font(size)
    if ft is not None:
        try:
            # 用 advance 宽度(不是 bbox), 否则粗体行尾会被裁掉
            w = int(ft.getlength(text or "国")) + 4
            return max(1, w), int(size * 1.05)
        except Exception:
            pass
    hdc, bmp = _make_dc(8, 8)
    font = gdi32.CreateFontW(-size, 0, 0, 0, 400, 0, 0, 0,
                             GB2312_CHARSET, OUT_TT_ONLY_PRECIS, 0,
                             ANTIALIASED_QUALITY, 0, face)
    old = gdi32.SelectObject(hdc, font)
    sz = wintypes.SIZE()
    gdi32.GetTextExtentPoint32W(hdc, text, len(text), ctypes.byref(sz))
    gdi32.SelectObject(hdc, old)
    gdi32.DeleteObject(font)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)
    return sz.cx, sz.cy


def wrap_text(text, maxw, size=None, face=None):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        trial = cur + ch
        if measure(trial, size, face)[0] > maxw and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur or not lines:
        lines.append(cur)
    return lines


def render_block(text, maxw=1200, size=None, face=None, color=None,
                 outline=None, outline_w=None, embolden=0, pad=None,
                 line_gap=0):
    """折行渲染, 返回 (png_bytes, w, h)"""
    size = size or SIZE
    face = face or FACE
    color = color or COLOR
    outline = outline or OUTLINE_COLOR
    outline_w = OUTLINE_W if outline_w is None else outline_w
    pad = PAD if pad is None else pad

    # 模拟游戏位图字模: 内部按 30px 渲染, 最后整数倍放大
    upscale = 1.0
    if EMULATE_BITMAP and size > BITMAP_SIZE:
        upscale = size / float(BITMAP_SIZE)
        size = BITMAP_SIZE
        maxw = max(1, int(maxw / upscale))

    lines = wrap_text(text, maxw, size, face)
    _, line_h = measure("国", size, face)
    line_h = line_h or size
    line_pitch = line_h + line_gap

    widths = [measure(ln, size, face)[0] for ln in lines] or [0]
    W = max(widths) + pad * 2 + outline_w * 2 + 2
    H = line_pitch * len(lines) + pad * 2 + outline_w * 2 + 2
    W, H = max(1, int(W)), max(1, int(H))

    ft = _pil_font(size)
    if ft is not None:
        base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        from PIL import ImageDraw
        dr = ImageDraw.Draw(base)
        sw = max(0, int(round(size * 2.0 / 28.0)))   # 与补丁同比例: 28px 配 2px 描边
        for i, ln in enumerate(lines):
            y = pad + outline_w + i * line_pitch
            x = pad + outline_w
            # 与补丁一致: 逐字绘制, 每个字固定前进 size 像素
            for fp, seg in split_runs(ln):
                f_seg = _pil_font(size, fp)
                for ch_one in seg:
                    dr.text((x, y), ch_one, font=f_seg,
                            fill=(color[0], color[1], color[2], 255),
                            stroke_width=sw, stroke_fill=(0, 0, 0, 255))
                    x += size
        # 与补丁 sub_compiler.py 一致的配方: PIL 画完直接输出,
        # 不做亮度转 alpha / 膨胀 / 模糊(那些会把黑色描边吃掉)
        buf = io.BytesIO()
        base.save(buf, "PNG")
        return buf.getvalue(), W, H
    else:
        hdc, bmp = _make_dc(W, H)
        font = gdi32.CreateFontW(-size, 0, 0, 0, 400, 0, 0, 0,
                                 GB2312_CHARSET, OUT_TT_ONLY_PRECIS, 0,
                                 ANTIALIASED_QUALITY, 0, face)
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        gdi32.SetTextColor(hdc, color[0] | (color[1] << 8) | (color[2] << 16))
        for i, ln in enumerate(lines):
            y = pad + outline_w + i * line_pitch
            for ex in range(embolden + 1):
                for ey in range(embolden + 1):
                    gdi32.TextOutW(hdc, pad + outline_w + ex, int(y) + ey, ln, len(ln))
        raw = _read_pixels(hdc, bmp, W, H)
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(font)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(hdc)
        base = Image.frombuffer("RGBA", (W, H), raw, "raw", "BGRA", 0, 1)
    r_, g_, b_, _ = base.split()
    alpha = base.convert("L")

    if False and outline_w > 0:
        dil = alpha
        for _ in range(outline_w):
            dil = dil.filter(ImageFilter.MaxFilter(3))
        if EDGE_BLUR:
            dil = dil.filter(ImageFilter.GaussianBlur(EDGE_BLUR))
        layer = Image.new("RGBA", (W, H), outline + (0,))
        layer.putalpha(dil)
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        img.alpha_composite(layer)
    else:
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if False and BODY_BLUR:
        alpha = alpha.filter(ImageFilter.GaussianBlur(BODY_BLUR))
    body = Image.merge("RGBA", (r_, g_, b_, alpha))
    img.alpha_composite(body)

    if False and WHITE_BOOST > 0:
        al = img.split()[3]
        mask = al.point(lambda v: int(v * WHITE_BOOST) if v > 150 else 0)
        white = Image.new("RGBA", img.size, (255, 255, 255, 0))
        white.putalpha(mask)
        img = Image.alpha_composite(img, white)

    if upscale > 1.0:
        W, H = int(round(W * upscale)), int(round(H * upscale))
        img = img.resize((W, H), Image.NEAREST)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue(), W, H


CACHE_DIR = os.path.join(os.environ.get("TEMP") or os.path.expanduser("~"), "wa2_textcache")


def render_cached(text, maxw=1200, size=None, embolden=0, outline_w=None):
    # 缓存键必须包含所有影响像素的参数, 尤其是字体文件
    try:
        _fsig = "%s|%d" % (_FONT_FILE, int(os.path.getmtime(_FONT_FILE)))
    except Exception:
        _fsig = str(_FONT_FILE)
    key = hashlib.md5((
        "%s|%s|%d|%d|%d|%d|%s|%d|%.2f|%.2f|%.2f|%d|%d" % (
            _fsig, text, maxw, size or SIZE, embolden,
            OUTLINE_W if outline_w is None else outline_w,
            str(EMULATE_BITMAP), BITMAP_SIZE,
            BODY_BLUR, EDGE_BLUR, WHITE_BOOST, SIZE, PAD)
    ).encode("utf-8")).hexdigest()
    os.makedirs(CACHE_DIR, exist_ok=True)
    fp = os.path.join(CACHE_DIR, key + ".png")
    if os.path.exists(fp):
        return open(fp, "rb").read()
    data, w, h = render_block(text, maxw=maxw, size=size, embolden=embolden,
                              outline_w=outline_w)
    try:
        open(fp, "wb").write(data)
    except Exception:
        pass
    return data


if __name__ == "__main__":
    # 手动跑一下看渲染效果：产物放数据根的 work/，别写进程序目录
    d, w, h = render_block("不然的话，这次我可是真的要", maxw=1200)
    import datadir
    out = os.path.join(datadir.sub("work"), "gdi_test.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    open(out, "wb").write(d)
    print("渲染 OK:", w, "x", h, "->", out)
