# -*- coding: utf-8 -*-
"""阶段 C：把文字块/图形块自适应排到纸面上，出图片和 PDF。

所有尺寸都从纸面的实际可用区按比例派生，所以换任何尺寸的纸都不会溢出。
"""
import json
import math
import os

import edge

SMAX = 0.40          # 多题一页时的字号缩放上限（观感约束，与纸面无关）
SMAX_ONE = 0.62      # 一题一页时允许更大
SMIN = 0.30          # 防溢出的兜底下限（低于它会强行放大，可能顶出纸边，别拿它当目标）
MIN_READ = 0.38      # 目标字号：分页后统一 s 低于它，就多分一页。0.40 在多数组面下不可达
                     # （无图题文字块约 2900px，纸面可写宽约 1100px，宽度天花板 ≈ 0.38）
GAIN_MIN = 0.08      # 多分一页至少要把字号提升 8%，否则不值：页数多一页是看得见的代价
FIG_H_RATIO = 1.22   # 图高上限 = 文字块高 × 此值
ONE_FIG_H_RATIO = 1.55


def split_pages(heights, npages):
    """把序列切成 npages 段，使各段和的最大值最小（穷举切点）。"""
    n = len(heights)
    if npages >= n:
        return [[i] for i in range(n)]
    best, best_cuts = None, None
    from itertools import combinations
    for cuts in combinations(range(1, n), npages - 1):
        idx = (0,) + cuts + (n,)
        m = max(sum(heights[a:b]) for a, b in zip(idx, idx[1:]))
        if best is None or m < best:
            best, best_cuts = m, cuts
    idx = (0,) + best_cuts + (n,)
    return [list(range(a, b)) for a, b in zip(idx, idx[1:])]


def _page_fit(page_idx, infos, are_w, are_h, gap_min, smax):
    """一页的缩放上限：受高度、以及每个块的可用宽度共同约束。

    有图题要把右侧让给图（只占 66% 宽），无图题可以用满整个可用宽度——
    无图题的画布本来就是加宽的，用满宽度正好「一行写到右边距」。
    """
    need = sum(infos[i]["text_size"][1] for i in page_idx)
    if need <= 0:
        return smax
    s = (are_h - gap_min * (len(page_idx) - 1)) / need
    for i in page_idx:
        tw_ = infos[i]["text_size"][0]
        if tw_ <= 0:
            continue
        cap = 0.66 * are_w if infos[i].get("fig_size") else are_w
        s = min(s, cap / tw_)
    return min(s, smax)


def plan(infos, paper, layout):
    """算出 (s, paginate, 每页布局参数)。infos 按题目顺序。"""
    x0, x1, y0, y1 = paper["x0"], paper["x1"], paper["y0"], paper["y1"]
    are_w, are_h = x1 - x0, y1 - y0
    gap_min = 0.019 * are_h
    gap_max = 0.104 * are_h

    # 图列位置只看「有图题」的文字块宽度：无图题的块是加宽的，不能参与
    fig_w = [i["text_size"][0] for i in infos if i.get("fig_size")]
    refw = max(fig_w) if fig_w else max(i["text_size"][0] for i in infos)
    heights = [i["text_size"][1] for i in infos]
    n = len(infos)

    mode = layout.get("mode", "multi")
    per_page = layout.get("per_page")
    if mode == "one" or per_page == 1:
        page_sets = [[i] for i in range(n)]
        smax = SMAX_ONE
    elif isinstance(per_page, int) and per_page > 1:
        page_sets = [list(range(i, min(i + per_page, n))) for i in range(0, n, per_page)]
        smax = SMAX
    else:
        smax = SMAX
        page_sets = None

    rects = paper.get("rects") or [[x0, x1, y0, y1]]

    def fit_all(sets):
        """一组分页方案的统一字号：按每页自己那张纸算，取最小值。"""
        vals = []
        for pi, p in enumerate(sets):
            r = rects[pi % len(rects)]
            aw, ah = r[1] - r[0], r[3] - r[2]
            vals.append(_page_fit(p, infos, aw, ah, 0.019 * ah, smax))
        return min(vals) if vals else smax

    if page_sets is None:
        # 两个停止条件，谁先触发算谁：
        #   ① 达到目标字号 min_scale（默认 0.38）
        #   ② 再多分一页换来的字号提升不足 min_gain（默认 8%）——页数也是代价
        # 目标不可达时停在「最后一档有正收益的分页」，不会一路拆到一题一页
        # （过了拐点 s 反而下降，因为宽度约束接管了）。
        target = float(layout.get("min_scale") or MIN_READ)
        min_gain = float(layout.get("min_gain") or GAIN_MIN)
        best_s, best_pages, prev_s = -1.0, [[i] for i in range(n)], None
        for np_ in range(1, n + 1):
            cand = split_pages(heights, np_)
            s_cand = fit_all(cand)
            if prev_s is not None and s_cand < prev_s * (1 + min_gain):
                break
            best_s, best_pages, prev_s = s_cand, cand, s_cand
            if s_cand >= target:
                page_sets = cand
                break
        if page_sets is None:
            page_sets = best_pages

    # 全篇统一字号：每页都要放得下，所以取各页上限的最小值
    s = max(SMIN, min(fit_all(page_sets), smax))

    return dict(s=s, pages=page_sets, are_w=are_w, are_h=are_h,
                x0=x0, x1=x1, y0=y0, y1=y1, refw=refw, smax=smax,
                gap_min=gap_min, gap_max=gap_max, side=layout.get("figure_side", "right"))


def fit_scale(page_idx, infos, rect, smax, refw, want=None):
    """按某一页自己的可用区算字号缩放（换纸时每页面积略有差异，各页各自贴合）。

    want 给了就再取 min，保证全篇用同一个字号（看起来是同一支笔、同一个大小）。
    """
    x0, x1, y0, y1 = rect
    are_w, are_h = x1 - x0, y1 - y0
    s = _page_fit(page_idx, infos, are_w, are_h, 0.019 * are_h, smax)
    if want:
        s = min(s, want)
    return max(SMIN, min(s, smax))


def blocks_for(page_idx, infos, pl):
    """给一页算出每个块的 (文字宽高, 图宽高, 位置)。"""
    s, side = pl["s"], pl["side"]
    one = len(page_idx) == 1
    gap_x = 0.032 * pl["are_w"]
    out = []
    for i in page_idx:
        info = infos[i]
        tw_, th_ = info["text_size"][0] * s, info["text_size"][1] * s
        # 图列起点：多题一页时按「全篇最宽文字」对齐（版面整齐）；
        # 一题一页时按本题文字实际宽度，别让图被挤到纸边
        figx = pl["x0"] + tw_ + gap_x if one else pl["x0"] + pl["refw"] * s + gap_x
        fw = fh = 0
        if info.get("fig_size"):
            f0w, f0h = info["fig_size"]
            ar = f0w / f0h
            if side == "below":
                wcap = min(pl["are_w"], pl["are_w"] * 0.92)
                hcap = (pl["are_h"] * 0.5)
            else:
                wcap = min(pl["x1"] - figx, 0.326 * (pl["x1"] - pl["x0"]))
                hcap = th_ * (ONE_FIG_H_RATIO if one else FIG_H_RATIO)
            w = min(wcap, hcap * ar)
            h = w / ar
            if h > hcap:
                h, w = hcap, hcap * ar
            fw, fh = w, h
        bh = th_ if side != "below" else th_ + (fh + 0.02 * pl["are_h"] if fh else 0)
        out.append(dict(i=i, tw=tw_, th=th_, fw=fw, fh=fh, bh=bh, fx=figx))
    return out


def page_html(paper_png, parts, w, h):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#fff;overflow:hidden}}
#pg{{position:relative;width:{w}px;height:{h}px}}
#bg{{position:absolute;left:0;top:0;width:{w}px;height:{h}px}}</style></head>
<body><div id="pg"><img id="bg" src="{paper_png}">{''.join(parts)}</div></body></html>"""


def run(infos, paper, layout, workdir, outdir, edge_exe, scale=1.6,
        pdf=True, verbose=True):
    """infos: [{index, text_size, fig_size}]（按题目顺序）。返回生成的图片列表。

    paper 里若有 papers/rects（一组纸面），则第 i 页用第 i 张、并按该张自己的
    可用区排版；字号仍按 plan() 用交集算出来的 s，保证换哪张都不溢出。
    """
    pl = plan(infos, paper, layout)
    os.makedirs(outdir, exist_ok=True)
    pw, ph = paper["w"], paper["h"]
    bg_names = paper.get("papers") or [os.path.basename(paper.get("paper_png") or "paper.png")]
    rects = paper.get("rects") or [[paper["x0"], paper["x1"], paper["y0"], paper["y1"]]]
    gap_max = pl["gap_max"]

    for f in os.listdir(outdir):
        if f.startswith("第") and f.endswith(".png"):
            try:
                os.remove(os.path.join(outdir, f))
            except OSError:
                pass

    files, scales = [], []
    for pi, page_idx in enumerate(pl["pages"], 1):
        # 本页纸面与可用区
        paper_png = bg_names[(pi - 1) % len(bg_names)]
        rect = rects[(pi - 1) % len(rects)]
        plp = dict(pl)
        plp["x0"], plp["x1"], plp["y0"], plp["y1"] = rect
        plp["are_w"], plp["are_h"] = rect[1] - rect[0], rect[3] - rect[2]
        plp["gap_min"] = 0.019 * plp["are_h"]
        plp["gap_max"] = 0.104 * plp["are_h"]
        # 每页按自己的可用区定字号，但全篇取同一个值（want=pl["s"]），
        # 换纸时不会出现「这一页字大、下一页字小」
        plp["s"] = fit_scale(page_idx, infos, rect, pl["smax"], pl["refw"], want=pl["s"])

        bl = blocks_for(page_idx, infos, plp)
        total = sum(b["bh"] for b in bl)
        gap = ((plp["are_h"] - total) / (len(bl) - 1)) if len(bl) > 1 else 0
        gap = max(plp["gap_min"], min(gap, plp["gap_max"]))
        # 顶对齐：作业本是自上往下写的，多余空间应该留在页脚
        y = plp["y0"]

        parts = []
        for b in bl:
            parts.append(f'<img src="blk/text{b["i"] + 1}.png" style="position:absolute;'
                         f'left:{plp["x0"]:.1f}px;top:{y:.1f}px;width:{b["tw"]:.1f}px">')
            if b["fw"]:
                if pl["side"] == "below":
                    fx = plp["x0"] + (plp["are_w"] - b["fw"]) / 2
                    fy = y + b["th"] + 0.02 * plp["are_h"]
                else:
                    fx = b["fx"]
                    fy = y + (b["th"] - b["fh"]) / 2
                parts.append(f'<img src="blk/fig{b["i"] + 1}.png" style="position:absolute;'
                             f'left:{fx:.1f}px;top:{fy:.1f}px;width:{b["fw"]:.1f}px">')
            y += b["bh"] + gap

        html_path = os.path.join(workdir, f"sheet{pi}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page_html(paper_png, parts, pw, ph))
        out_png = os.path.join(outdir, f"第{pi}页.png")
        edge.shot(edge_exe, html_path, out_png, pw, ph, scale=scale)
        if verbose:
            print(f"  第 {pi} 页：{len(bl)} 题，缩放 {plp['s']:.3f}，题距 {gap:.0f}px，"
                  f"纸面 {paper_png}", flush=True)
        scales.append(round(plp["s"], 4))
        files.append(out_png)

    meta = dict(scale_paper=pl["s"], pages=[[i + 1 for i in p] for p in pl["pages"]],
                x0=pl["x0"], x1=pl["x1"], y0=pl["y0"], y1=pl["y1"],
                refw=pl["refw"], side=pl["side"],
                papers=bg_names[:len(files)], rects=[list(r) for r in rects],
                scales=scales)
    with open(os.path.join(outdir, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    if pdf:
        html = ['<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<style>@page{{size:{pw}px {ph}px;margin:0}}',
                'html,body{margin:0}img{display:block;width:100%;page-break-after:always}',
                'img:last-child{page-break-after:auto}</style></head><body>']
        for p in files:
            html.append(f'<img src="{os.path.basename(p)}">')
        html.append("</body></html>")
        hp = os.path.join(outdir, "_pdf.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write("".join(html))
        out_pdf = os.path.join(outdir, "手写稿.pdf")
        size = edge.pdf(edge_exe, hp, out_pdf)
        if verbose:
            print(f"  PDF：{out_pdf}（{size / 1e6:.1f} MB）", flush=True)
    return files
