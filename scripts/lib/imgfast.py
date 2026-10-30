# -*- coding: utf-8 -*-
"""bytes 级图像原语。

所有操作都靠 bytes/bytearray 的 C 实现（切片、translate、count、find/rfind、
join+map），不做逐像素 Python 循环 —— 这是「纯标准库也能 1.5s/页」的关键。
"""
import os

INK = 176          # 灰度 >= 此值视为纸白
SPAN = 118.0       # alpha 归一化跨度
DARK = 110         # 判定「深色」的阈值（找辅助横线用）

_TBL_CACHE = {}
_ALPHA_CACHE = {}


def _mask_tbl(thr):
    t = _TBL_CACHE.get(thr)
    if t is None:
        t = bytes(1 if v < thr else 0 for v in range(256))
        _TBL_CACHE[thr] = t
    return t


def mask(grey, thr=DARK):
    """二值掩码：暗于 thr 记 1。C 级一次成型。"""
    return bytes(grey).translate(_mask_tbl(thr))


def row_counts(mask_bytes, w, h, y0=0, y1=None):
    """每行的暗像素数（C 级 count）。"""
    y1 = h if y1 is None else min(y1, h)
    return [mask_bytes[y * w:(y + 1) * w].count(1) for y in range(y0, y1)]


def find_rule_rows(mask_bytes, w, h, ratio=0.80, near=0.25):
    """找「辅助横线」：整行暗像素 > ratio 页宽，且上下各 2 行几乎空白。

    手写服务给每行画的下划线是 1px 纯黑、横贯整页（~88% 宽）；
    图形坐标轴最宽只占 50%~67%，所以 ratio=0.80 能放过坐标轴。
    """
    cnts = [mask_bytes[y * w:(y + 1) * w].count(1) for y in range(h)]
    lim = ratio * w
    out = []
    for y, c in enumerate(cnts):
        if c <= lim:
            continue
        a = cnts[y - 2] if y >= 2 else 0
        b = cnts[y + 2] if y + 2 < h else 0
        if (a + b) < near * c:
            out.append(y)
    return out


def clear_rows(grey, w, h, rows):
    """把指定行整行涂白，返回新的 bytes。"""
    if not rows:
        return bytes(grey)
    buf = bytearray(grey)
    blank = b"\xff" * w
    for y in rows:
        if 0 <= y < h:
            buf[y * w:(y + 1) * w] = blank
    return bytes(buf)


def bands(mask_bytes, w, h, gap=8, min_px=1, y0=0, y1=None):
    """按行墨迹切带：相邻空洞 <= gap 行合并。[{y0,y1}]"""
    y1 = h if y1 is None else min(y1, h)
    cnts = [mask_bytes[y * w:(y + 1) * w].count(1) for y in range(y0, y1)]
    out, cur, hole = [], None, 0
    for i, c in enumerate(cnts):
        y = y0 + i
        if c >= min_px:
            if cur is None:
                cur = [y, y]
            else:
                cur[1] = y
            hole = 0
        elif cur is not None:
            hole += 1
            if hole > gap:
                out.append(tuple(cur))
                cur = None
                hole = 0
    if cur:
        out.append(tuple(cur))
    return out


def bbox(mask_bytes, w, h, y0=0, y1=None, min_px=1):
    """区域内墨迹包围盒 (x0, y0, x1, y1)，右下为闭区间；无墨返回 None。"""
    y1 = h if y1 is None else min(y1, h)
    x0, x1, ty, by = w, -1, -1, -1
    for y in range(y0, y1):
        row = mask_bytes[y * w:(y + 1) * w]
        if row.count(1) < min_px:
            continue
        a = row.find(1)
        b = row.rfind(1)
        if a < x0:
            x0 = a
        if b > x1:
            x1 = b
        if ty < 0:
            ty = y
        by = y
    if x1 < 0:
        return None
    return x0, ty, x1, by


def column_box(mask_bytes, w, h, y0, y1, min_px=1):
    """行带内首/末墨迹列（等价 bbox，语义更清楚）。"""
    b = bbox(mask_bytes, w, h, y0, y1, min_px)
    return (b[0], b[2]) if b else None


def _lut_rgba(thr, span, ink=None, gamma=1.0):
    """256 项查找表：灰度值 -> 4 字节 RGBA。

    ink=None 时颜色保留原灰度（正文/图形用，等价旧版逐像素写法）；
    给定 ink 时颜色统一（标签贴片用，沿用旧版口径）。
    gamma < 1 时提高中间灰的不透明度（让抗锯齿边缘更实、不出现「断墨」）。
    """
    t = []
    for v in range(256):
        if v >= thr:
            a = 0
        else:
            cov = (thr - v) / span
            if gamma != 1.0:
                cov = cov ** gamma
            a = int(cov * 255)
            a = 255 if a > 255 else a
        t.append(bytes((v, v, v, a)) if ink is None else bytes((ink, ink, ink, a)))
    return t


def cut_rgba(grey, w, h, box, pad=6, thr=INK, span=SPAN, ink=None, gamma=1.0):
    """按包围盒裁出透明底 RGBA（颜色默认保留原灰度，ink 给定时统一墨色）。"""
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(w, x1 + 1 + pad)
    y1 = min(h, y1 + 1 + pad)
    bw, bh = x1 - x0, y1 - y0
    get = _lut_rgba(thr, span, ink, gamma).__getitem__
    parts = [b"".join(map(get, grey[y * w + x0:y * w + x1])) for y in range(y0, y1)]
    return b"".join(parts), bw, bh


def to_alpha_rgba(grey, w, h, thresh=248, ink=20):
    """白底转透明、墨色统一为 ink（标签贴片沿用旧版口径，保持观感一致）。"""
    get = _lut_rgba(thresh, 243.0, ink).__getitem__
    return b"".join(b"".join(map(get, grey[y * w:(y + 1) * w])) for y in range(h))


def write_png(path, w, h, rgba, encode):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(encode(w, h, rgba))
