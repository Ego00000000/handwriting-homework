# -*- coding: utf-8 -*-
"""纸面预处理：擦掉纸张自带的横线 + 探测可书写的矩形区域。

顺序很重要：**先探可用区，再在区内擦线**。否则照片四周的暗背景会被当成
「整行都暗」，一次擦掉几十万像素，纸面反而被抹花。

擦线用「竖直灰值闭运算」：只在竖直方向用 11~13 行的结构元把细暗线抹平，
宽的暗部（书脊、页边阴影、污渍）不会被吃掉；填充色取上下邻行插值，
所以纸面颗粒和明暗渐变都能保住。
"""
import os

import pngtool


def _shift(seq, d):
    n = len(seq)
    if d == 0:
        return seq
    if d > 0:
        return seq[:d] + seq[:n - d]
    return seq[-d:] + seq[-1:] * (-d)


def _close_col(col, r=6):
    """单列灰值的竖直闭运算（先膨胀后腐蚀）。"""
    dil = col
    for k in range(1, r + 1):
        dil = bytes(map(max, dil, _shift(col, k), _shift(col, -k)))
    clo = dil
    for k in range(1, r + 1):
        clo = bytes(map(min, clo, _shift(dil, k), _shift(dil, -k)))
    return clo


def probe(grey, w, h, edge=None):
    """探测纸面矩形（亮区）。返回 dict(w,h,x0,x1,y0,y1,thr)。"""
    samples = sorted(grey[y * w + x] for y in range(h // 10, h * 9 // 10, 17)
                     for x in range(w // 3, w * 2 // 3, 13))
    med = samples[len(samples) // 2] if samples else 200
    thr = max(70, min(150, int(med * 0.6)))

    lefts, rights = [], []
    for y in range(h // 12, h * 11 // 12, 23):
        row = grey[y * w:(y + 1) * w]
        a = 0
        while a < w and row[a] <= thr:
            a += 1
        b = w - 1
        while b > a and row[b] <= thr:
            b -= 1
        if b - a > w * 0.3:
            lefts.append(a)
            rights.append(b)
    if not lefts:
        x0, x1 = int(w * 0.04), int(w * 0.96)
    else:
        lefts.sort()
        rights.sort()
        x0, x1 = lefts[len(lefts) // 2], rights[len(rights) // 2]

    tops, bots = [], []
    for fx in (0.25, 0.5, 0.75):
        x = int(x0 + (x1 - x0) * fx)
        a = 0
        while a < h and grey[a * w + x] <= thr:
            a += 1
        b = h - 1
        while b > a and grey[b * w + x] <= thr:
            b -= 1
        tops.append(a)
        bots.append(b)
    tops.sort()
    bots.sort()
    y0, y1 = tops[len(tops) // 2], bots[len(bots) // 2]

    # 纸边留白：默认按短边的 4% 内缩（笔记本的书脊、页边阴影都在这个范围内）
    if edge is None:
        edge = max(8, int(min(w, h) * 0.04))
    return dict(w=w, h=h,
                x0=min(max(0, x0 + edge), w - 12), x1=min(max(12, x1 - edge), w - 1),
                y0=min(max(0, y0 + edge), h - 12), y1=min(max(12, y1 - edge), h - 1),
                thr=thr, edge=edge)


def _dilate_rows(mask, w, h, r=2):
    """竖直膨胀 r 行：纸张横线常因拍摄有 1~3px 的弯曲，单行覆盖率会被打散。"""
    out = mask
    for k in range(1, r + 1):
        up = b"\x00" * (w * k) + out[:len(out) - w * k]
        dn = out[w * k:] + b"\x00" * (w * k)
        out = bytes(map(max, out, up, dn))
    return out


def count_rules(grey, w, h, rect, ratio=0.6, min_rows=3):
    """统计纸面区内「长横线」条数。

    阈值按纸色自适应：纸面中位亮度往下 12 灰阶即算线（实拍的横线常常只比纸暗
    十几级），再用竖直膨胀容忍拍摄造成的弯曲。
    """
    x0, x1, y0, y1 = rect["x0"], rect["x1"], rect["y0"], rect["y1"]
    span = x1 - x0
    med = int(rect.get("thr", 120) / 0.6)
    dark = max(100, min(170, med - 12))
    tbl = bytes(1 if v < dark else 0 for v in range(256))
    mask = bytes(grey).translate(tbl)
    mask = _dilate_rows(mask, w, h, 2)
    lim = ratio * span
    return sum(1 for y in range(y0, y1)
               if mask[y * w + x0:y * w + x1].count(1) > lim)


def clean(grey, w, h, rect, r=6, lo=2, hi=120):
    """在纸面区内擦掉细横线，返回 (新 grey, 被改像素数)。"""
    g = bytearray(grey)
    x0, x1, y0, y1 = rect["x0"], rect["x1"], rect["y0"], rect["y1"]
    erased = 0
    for x in range(x0, x1):
        col = bytes(g[y * w + x] for y in range(y0, y1))
        clo = _close_col(col, r)
        runs, start = [], None
        n = y1 - y0
        for i in range(n):
            d = clo[i] - col[i]
            bad = lo <= d <= hi
            if bad and start is None:
                start = i
            elif not bad and start is not None:
                runs.append((start, i))
                start = None
        if start is not None:
            runs.append((start, n))
        for a, b in runs:
            up = col[a - 1] if a > 0 else (col[b] if b < n else 255)
            dn = col[b] if b < n else up
            ln = b - a
            for i in range(ln):
                v = int(up + (dn - up) * (i + 1) / (ln + 1))
                g[(y0 + a + i) * w + x] = v
                erased += 1
    return bytes(g), erased


def prepare(path, workdir, mode="auto", verbose=True, out_name="paper.png", rect=None):
    """读纸面 →（可选）擦线 → 落盘 work/<out_name> + 返回 paper.json 内容。

    mode: auto | on | off
    """
    with open(path, "rb") as f:
        raw = f.read()
    w, h, ch, px = pngtool.decode(raw)
    grey = px[0::ch]

    if rect is None:
        rect = probe(grey, w, h)
    else:
        rect = dict(rect)
        rect.setdefault("w", w)
        rect.setdefault("h", h)
    rules = count_rules(grey, w, h, rect)
    do_clean = (mode == "on") or (mode == "auto" and rules >= 3)
    grey0 = grey
    erased = 0
    if do_clean:
        grey, erased = clean(grey, w, h, rect)
        if verbose:
            print(f"  擦线：检出约 {rules} 条长横线，抹除 {erased} 像素", flush=True)

    info = dict(rect)
    info.update(cleaned=bool(do_clean), erased_px=erased, rules_found=rules,
                source=os.path.abspath(path), pil=pngtool.HAVE_PIL)

    os.makedirs(workdir, exist_ok=True)
    out = os.path.join(workdir, out_name)
    if do_clean:
        # 只把「提亮量」按通道加到原色上：既抹掉线，又保住纸的色相与颗粒
        rgba = bytearray(w * h * 4)
        for i in range(w * h):
            j = i * ch
            k = i * 4
            d = grey[i] - grey0[i]
            if d > 0:
                r = px[j] + d
                g2 = px[j + 1] + d
                b = px[j + 2] + d
                rgba[k] = 255 if r > 255 else r
                rgba[k + 1] = 255 if g2 > 255 else g2
                rgba[k + 2] = 255 if b > 255 else b
            else:
                rgba[k] = px[j]
                rgba[k + 1] = px[j + 1]
                rgba[k + 2] = px[j + 2]
            rgba[k + 3] = 255
        with open(out, "wb") as f:
            f.write(pngtool.encode_rgba(w, h, bytes(rgba)))
    else:
        with open(out, "wb") as f:
            f.write(raw)

    info["paper_png"] = out
    if verbose:
        print(f"  纸面 {w}x{h}，可用区 x[{info['x0']}-{info['x1']}] "
              f"y[{info['y0']}-{info['y1']}]", flush=True)
    return info


def load_rects(paths):
    """纸面目录里的 rects.json。

    两种写法都认：
      {"paper-01.png": [x0,x1,y0,y1], ...}                 仅给可用区
      {"_clean": true, "paper-01.png": [x0,x1,y0,y1], ...} 额外声明这组纸面已无需擦线
    返回 (rects, clean)
    """
    m, clean = {}, False
    for p in paths:
        d = os.path.dirname(os.path.abspath(p))
        j = os.path.join(d, "rects.json")
        if not os.path.isfile(j):
            continue
        try:
            import json
            with open(j, encoding="utf-8") as f:
                d = json.load(f)
            clean = bool(d.pop("_clean", False))
            d.pop("_note", None)
            m.update({k: v for k, v in d.items() if isinstance(v, (list, tuple))})
        except Exception:
            pass
        break
    return m, clean


def prepare_set(paths, workdir, mode="off", verbose=True):
    """按顺序准备一组纸面（一页一张，用满后循环），用于整本作业本换页。

    返回的 info 里：
      x0/x1/y0/y1 —— 所有纸面可用区的**交集**（排版按它算字号，保证每页都不溢）
      papers       —— 每页用的纸面 png 文件名（相对 workdir）
      rects        —— 每页各自的可用区 [x0,x1,y0,y1]
      sources      —— 原始纸面路径（做缓存指纹用）
    """
    rects, pre_clean = load_rects(paths)
    if pre_clean:
        mode = "off"
    infos = []
    for i, p in enumerate(paths, 1):
        name = "paper.png" if len(paths) == 1 else f"paper{i}.png"
        r = rects.get(os.path.basename(p))
        info = prepare(p, workdir, mode=mode, verbose=verbose, out_name=name,
                       rect=dict(zip(("x0", "x1", "y0", "y1"), r)) if r else None)
        info["paper_png"] = os.path.join(workdir, name)
        infos.append(info)

    ws = {i["w"] for i in infos}
    hs = {i["h"] for i in infos}
    if len(ws) > 1 or len(hs) > 1:
        raise SystemExit(f"这组纸面尺寸不一致：{sorted(ws)} x {sorted(hs)}，请统一后再用")

    merged = dict(infos[0])
    merged.pop("source", None)
    merged["x0"] = max(i["x0"] for i in infos)
    merged["x1"] = min(i["x1"] for i in infos)
    merged["y0"] = max(i["y0"] for i in infos)
    merged["y1"] = min(i["y1"] for i in infos)
    merged["papers"] = [os.path.basename(i["paper_png"]) for i in infos]
    merged["rects"] = [[i["x0"], i["x1"], i["y0"], i["y1"]] for i in infos]
    merged["sources"] = [os.path.abspath(p) for p in paths]
    merged["paper_png"] = infos[0]["paper_png"]
    if verbose and len(infos) > 1:
        print(f"  纸面组：{len(infos)} 张，排版按交集 "
              f"x[{merged['x0']}-{merged['x1']}] y[{merged['y0']}-{merged['y1']}]", flush=True)
    return merged
