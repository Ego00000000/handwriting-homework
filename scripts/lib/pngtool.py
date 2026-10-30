# -*- coding: utf-8 -*-
"""极简 PNG 编解码（纯标准库，零第三方依赖）。

- decode()       -> (w, h, ch, rgb_bytes)         需要彩图时用
- decode_grey()  -> (w, h, grey_bytes)            只要灰度时用，省 2/3 内存与一次切片
- encode_rgba()  -> PNG 字节                      透明底贴片用

unfilter 是热点（实测占单页解码的 95% 以上），因此按 filter 类型做分支特化，
而不是在字节循环里逐字节判断类型。
"""
import zlib
import struct
import io

try:                      # 可选加速轨：有 Pillow 就让 C 去做 unfilter
    from PIL import Image as _Image
    HAVE_PIL = True
except Exception:         # 没有就走纯标准库，功能完全一致
    _Image = None
    HAVE_PIL = False

SIG = b"\x89PNG\r\n\x1a\n"


def _ihdr(png):
    pos, idat = 8, []
    w = h = ch = None
    while pos < len(png):
        ln, typ = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, bd, ct, comp, filt, inter = struct.unpack(">IIBBBBB", data)
            if bd != 8 or inter != 0:
                raise ValueError(f"只支持 8bit 非隔行 PNG（bd={bd} inter={inter}）")
            if ct not in (2, 6):
                raise ValueError(f"只支持 RGB/RGBA PNG（colortype={ct}）")
            ch = 3 if ct == 2 else 4
        elif typ == b"IDAT":
            idat.append(data)
        pos += 12 + ln
    return w, h, ch, b"".join(idat)


def _unfilter(raw, w, h, ch):
    """逐行还原，返回 (总字节 buffer, stride)。filter 分支在行级特化。"""
    stride = w * ch
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    rng = range
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 0:
            pass
        elif ft == 1:                      # Sub
            for i in rng(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ft == 2:                      # Up
            for i in rng(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:                      # Average
            for i in rng(ch):
                line[i] = (line[i] + (prev[i] >> 1)) & 255
            for i in rng(ch, stride):
                line[i] = (line[i] + ((line[i - ch] + prev[i]) >> 1)) & 255
        elif ft == 4:                      # Paeth
            for i in rng(ch):
                line[i] = (line[i] + prev[i]) & 255
            for i in rng(ch, stride):
                a = line[i - ch]
                b = prev[i]
                c = prev[i - ch]
                pa = (a + b - c) - a
                pa = pa if pa >= 0 else -pa
                pb = (a + b - c) - b
                pb = pb if pb >= 0 else -pb
                pc = (a + b - c) - c
                pc = pc if pc >= 0 else -pc
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 255
        else:
            raise ValueError(f"未知 filter 类型 {ft}")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out, stride


def decode(png):
    """-> (w, h, ch, pixel_bytes)"""
    if HAVE_PIL:
        im = _Image.open(io.BytesIO(png))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        return im.width, im.height, (4 if im.mode == "RGBA" else 3), im.tobytes()
    w, h, ch, idat = _ihdr(png)
    out, _ = _unfilter(zlib.decompress(idat), w, h, ch)
    return w, h, ch, bytes(out)


def decode_grey(png):
    """只取 R 通道当灰度（墨迹是黑的，任一通道都够）。-> (w, h, grey_bytes)

    这里刻意与 _unfilter 重复一份还原逻辑：少建 26MB 的整幅缓冲，
    8 个进程并行时峰值内存能省一半。两版输出在测试里做过逐字节对拍。
    """
    if HAVE_PIL:
        im = _Image.open(io.BytesIO(png))
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")
        return im.width, im.height, im.split()[0].tobytes()
    w, h, ch, idat = _ihdr(png)
    raw = zlib.decompress(idat)
    stride = w * ch
    grey = bytearray(w * h)
    prev = bytearray(stride)
    p = 0
    rng = range
    for y in range(h):
        ft = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        if ft == 1:
            for i in rng(ch, stride):
                line[i] = (line[i] + line[i - ch]) & 255
        elif ft == 2:
            for i in rng(stride):
                line[i] = (line[i] + prev[i]) & 255
        elif ft == 3:
            for i in rng(ch):
                line[i] = (line[i] + (prev[i] >> 1)) & 255
            for i in rng(ch, stride):
                line[i] = (line[i] + ((line[i - ch] + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in rng(ch):
                line[i] = (line[i] + prev[i]) & 255
            for i in rng(ch, stride):
                a = line[i - ch]
                b = prev[i]
                c = prev[i - ch]
                pa = (a + b - c) - a
                pa = pa if pa >= 0 else -pa
                pb = (a + b - c) - b
                pb = pb if pb >= 0 else -pb
                pc = (a + b - c) - c
                pc = pc if pc >= 0 else -pc
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 255
        grey[y * w:(y + 1) * w] = line[0::ch]
        prev = line
    return w, h, bytes(grey)


def encode_rgba(w, h, rgba):
    def chunk(t, d):
        return (struct.pack(">I", len(d)) + t + d +
                struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + rgba[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (SIG
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
