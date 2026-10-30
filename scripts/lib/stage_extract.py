# -*- coding: utf-8 -*-
"""阶段 B：把整页拆成「文字块」和「图形块」（透明底）。

关键点：
  · 手写服务给每行画的 1px 纯黑下划线要整行剔掉，否则会跟着文字一起贴到纸上；
    判据是「暗像素 > 80% 页宽 且 上下 2 行几乎空白」（图形坐标轴只占 50%~67%，不受影响）。
  · 文字区 / 图形区的分界用几何关系算：y_fig = 上边距 + 行数×行距 + 90。
  · 全程 bytes 级操作（切片 / translate / count / find / join），单页 ~1.5s。
"""
import json
import os
from concurrent.futures import ProcessPoolExecutor

import imgfast
import pngtool

INK = imgfast.INK

# 输出墨色调节（默认口径不动；用环境变量临时加压）
#   HW_INK=25      墨色统一压到接近黑（模拟真实笔芯：颜料黑、覆盖度变）
#   HW_GAMMA=0.7   提高中间灰的不透明度（抗锯齿边缘更实）
_INK_OUT = int(os.environ["HW_INK"]) if os.environ.get("HW_INK") else None
_GAMMA = float(os.environ.get("HW_GAMMA") or 1.0)


def _fingerprint(page, lines, top, ls, has_fig):
    st = os.stat(page)
    return (f"{st.st_size}:{int(st.st_mtime)}:{lines}:{top}:{ls}:{int(has_fig)}:{INK}"
            f":{_INK_OUT}:{_GAMMA}")


def one(page, index, lines, top, ls, has_fig, outdir):
    with open(page, "rb") as f:
        w, h, grey = pngtool.decode_grey(f.read())

    rules = imgfast.find_rule_rows(imgfast.mask(grey, imgfast.DARK), w, h)
    grey = imgfast.clear_rows(grey, w, h, rules)
    m = imgfast.mask(grey, INK)

    y_fig = top + lines * ls + 90
    tbox = imgfast.bbox(m, w, h, 0, max(1, y_fig - 30))
    if not tbox:
        raise RuntimeError(f"第 {index} 题：没有检测到文字墨迹")

    rgba, bw, bh = imgfast.cut_rgba(grey, w, h, tbox, pad=6,
                                    ink=_INK_OUT, gamma=_GAMMA)
    tp = os.path.join(outdir, f"text{index}.png")
    with open(tp, "wb") as f:
        f.write(pngtool.encode_rgba(bw, bh, rgba))

    fsz = None
    if has_fig:
        fbox = imgfast.bbox(m, w, h, max(0, y_fig - 30), h)
        if not fbox:
            raise RuntimeError(f"第 {index} 题：声明了配图但没检测到图形墨迹")
        frgba, fw, fh = imgfast.cut_rgba(grey, w, h, fbox, pad=10,
                                        ink=_INK_OUT, gamma=_GAMMA)
        with open(os.path.join(outdir, f"fig{index}.png"), "wb") as f:
            f.write(pngtool.encode_rgba(fw, fh, frgba))
        fsz = [fw, fh]

    return dict(index=index, text_size=[bw, bh], fig_size=fsz,
                rules=len(rules), page_size=[w, h], y_fig=y_fig,
                fp=_fingerprint(page, lines, top, ls, has_fig))


def run(pages, outdir, top, ls, jobs=8, force=False, verbose=True):
    """pages: [{index, page, figures, text}]；返回 {index: info}。"""
    os.makedirs(outdir, exist_ok=True)
    meta_path = os.path.join(outdir, "meta.json")
    meta = {}
    if os.path.exists(meta_path) and not force:
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {}

    tasks, cached = [], {}
    for p in pages:
        i = p["index"]
        lines = p["text"].count("\n") + 1
        has_fig = bool(p.get("figures"))
        fp = _fingerprint(p["page"], lines, top, ls, has_fig)
        old = meta.get(str(i))
        ok = (old and old.get("fp") == fp
              and os.path.exists(os.path.join(outdir, f"text{i}.png"))
              and (not has_fig or os.path.exists(os.path.join(outdir, f"fig{i}.png"))))
        if ok:
            cached[i] = old
        else:
            tasks.append((p["page"], i, lines, top, ls, has_fig, outdir))

    if cached and verbose:
        print(f"  拆块：命中缓存 {len(cached)} 题，需处理 {len(tasks)} 题", flush=True)
    if tasks:
        if jobs > 1 and len(tasks) > 1:
            with ProcessPoolExecutor(max_workers=min(jobs, len(tasks))) as ex:
                res = list(ex.map(_star, tasks))
        else:
            res = [_star(t) for t in tasks]
        for r in res:
            cached[r["index"]] = r
    meta = {str(k): v for k, v in sorted(cached.items())}
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return meta


def _star(t):
    return one(*t)
