# -*- coding: utf-8 -*-
"""阶段 C：把文字块/图形块自适应排到纸面上，出图片和 PDF。

所有尺寸都从纸面的实际可用区按比例派生，所以换任何尺寸的纸都不会溢出。
"""
import json
import os
import random

import edge

SMAX = 0.40          # 多题一页时的字号缩放上限（观感约束，与纸面无关）
SMAX_ONE = 0.62      # 一题一页时允许更大
SMIN = 0.30          # 低于它只告警、不再强行放大（强行放大必然顶出纸边）
SFLOOR = 0.15        # 缩放的绝对地板：再小就没法看了，此时应该报错而不是硬排
MIN_READ = 0.38      # 目标字号：分页后统一 s 低于它，就多分一页。0.40 在多数组面下不可达
                     # （无图题文字块约 2900px，纸面可写宽约 1100px，宽度天花板 ≈ 0.38）
GAIN_MIN = 0.08      # 多分一页至少要把字号提升 8%，否则不值：页数多一页是看得见的代价
FIG_H_RATIO = 1.22   # 图高上限 = 文字块高 × 此值
ONE_FIG_H_RATIO = 1.55
LS_DEFAULT = 176     # 行距（与 hw.py DEFAULTS 一致，用来把像素高度换算成行数）


def split_pages(heights, npages):
    """把序列切成 npages 段，使各段和的最大值最小（DP，O(k·n²)）。

    旧版穷举全部切点，复杂度 C(n-1, k-1)：40 题试到第 9 页就要枚举 6152 万种组合，
    77 题切 30 页是 C(76,29)≈8e20，实际等于永久卡死。DP 版毫秒级。
    """
    n = len(heights)
    if npages <= 1:
        return [list(range(n))]
    if npages >= n:
        return [[i] for i in range(n)]
    if n * n * npages > 40_000_000:      # 超大规模退化到二分+贪心，避免内存爆掉
        return _split_greedy(heights, npages)

    pre = [0] * (n + 1)
    for i, h in enumerate(heights):
        pre[i + 1] = pre[i] + h

    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(npages + 1)]
    dp[0][0] = 0
    for k in range(1, npages + 1):
        prev, cur = dp[k - 1], dp[k]
        for i in range(k, n + 1):
            best = INF
            for j in range(k - 1, i):
                v = prev[j]
                if v == INF:
                    continue
                seg = pre[i] - pre[j]
                v = seg if seg > v else v
                if v < best:
                    best = v
            cur[i] = best

    # 回溯：从后往前挑「最大的可行切点」，让靠前的页装满、末页轻一点
    out, i = [], n
    for k in range(npages, 0, -1):
        j = k - 1
        for c in range(i - 1, k - 2, -1):
            if max(dp[k - 1][c], pre[i] - pre[c]) == dp[k][i]:
                j = c
                break
        out.append(list(range(j, i)))
        i = j
    return out[::-1]


def _split_greedy(heights, npages):
    """二分答案 + 贪心（O(n log Σh)），规模特别大时的退路。"""
    n = len(heights)
    lo, hi = max(heights), sum(heights)

    def need(cap):
        cnt, cur = 1, 0
        for h in heights:
            if cur + h > cap:
                cnt += 1
                cur = h
            else:
                cur += h
        return cnt

    while lo < hi:
        mid = (lo + hi) // 2
        if need(mid) <= npages:
            hi = mid
        else:
            lo = mid + 1

    out, cur, s = [], [], 0
    for i, h in enumerate(heights):
        if cur and s + h > lo:
            out.append(cur)
            cur, s = [i], h
        else:
            cur.append(i)
            s += h
    if cur:
        out.append(cur)
    while len(out) < npages:             # 段数不足时把最长段对半分
        j = max(range(len(out)), key=lambda k: sum(heights[i] for i in out[k]))
        seg = out[j]
        if len(seg) < 2:
            break
        mid = len(seg) // 2
        out[j:j + 1] = [seg[:mid], seg[mid:]]
    return out


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


def plan(infos, paper, layout, ls=LS_DEFAULT):
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
    one = mode == "one" or per_page == 1
    if one:
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

    want_pages = layout.get("pages")

    if page_sets is None:
        if isinstance(want_pages, int) and want_pages > 0:
            # --pages：直接按指定页数分，字号由这次分页反算（不再走两个停止条件）
            page_sets = split_pages(heights, min(want_pages, n))
        else:
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

    # 全篇统一字号：每页都要放得下，所以取各页上限的最小值。
    s = min(fit_all(page_sets), smax)

    # 「加页收益不足 8% 就不加」是为了省页数，但它只该在字号尚可读时生效：
    # 一旦 s 掉到 SMIN 以下，"多一页"的代价已经不能和"字小到看不清"相比了。
    # 这里忽略收益门槛，二分出第一个能让 s 回到 SMIN 的页数。
    if s < SMIN and not want_pages and not one and not isinstance(per_page, int):
        lo, hi = len(page_sets), n
        if fit_all(split_pages(heights, hi)) >= SMIN:
            while lo < hi:
                mid = (lo + hi) // 2
                if fit_all(split_pages(heights, mid)) >= SMIN:
                    hi = mid
                else:
                    lo = mid + 1
            page_sets = split_pages(heights, lo)
            s = min(fit_all(page_sets), smax)

    notes = []
    if s < SMIN:
        notes.append(f"内容偏多：字号被压到 {s:.3f}（低于舒适下限 {SMIN:.2f}，共 {len(page_sets)} 页）；"
                     f"一题一页也放不下更大，建议拆题或删减内容")
    s = max(s, SFLOOR)

    # 单题高度检查：一块超出纸面时旧版会静默写到纸外、被截图裁掉
    for info in infos:
        th = info["text_size"][1] * s
        if th > are_h + 1:
            idx = info.get("index") or (infos.index(info) + 1)
            lines_now = int(round(info["text_size"][1] / float(ls)))
            lines_max = int(are_h / (s * float(ls)))
            raise ValueError(
                f"第 {idx} 题单题高度 {th / are_h:.2f} 页，放不进一张纸（内容会被裁掉）。\n"
                f"  当前约 {lines_now} 行，这张纸最多 {lines_max} 行。\n"
                f"  处理办法：拆成两题、删减内容，或加 --pages 让全篇字号再小一点"
                f"（当前缩放 {s:.3f}）。")

    return dict(s=s, pages=page_sets, are_w=are_w, are_h=are_h,
                x0=x0, x1=x1, y0=y0, y1=y1, refw=refw, smax=smax,
                gap_min=gap_min, gap_max=gap_max, side=layout.get("figure_side", "right"),
                note="；".join(notes))


def fit_scale(page_idx, infos, rect, smax, refw, want=None):
    """按某一页自己的可用区算字号缩放（换纸时每页面积略有差异，各页各自贴合）。

    want 给了就再取 min，保证全篇用同一个字号（看起来是同一支笔、同一个大小）。
    """
    x0, x1, y0, y1 = rect
    are_w, are_h = x1 - x0, y1 - y0
    s = _page_fit(page_idx, infos, are_w, are_h, 0.019 * are_h, smax)
    if want:
        s = min(s, want)
    return max(SFLOOR, min(s, smax))


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
            if w > 1 and h > 1:          # 图列被文字占满时宁可不贴图，也不出负尺寸
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


def _pdf_copies(files, workdir, dpi=200, quality=82, verbose=True):
    """给 PDF 单独压一份 JPEG（不动交付的 PNG）。

    旧版直接把 2304×3072 的 PNG 塞进 HTML，30 页 146MB，微信/邮件都不好发。
    """
    try:
        from PIL import Image
    except Exception:
        if verbose:
            print("      没装 Pillow，PDF 用原图（体积会比较大）", flush=True)
        return None
    d = os.path.join(workdir, "_pdf")
    os.makedirs(d, exist_ok=True)
    out = []
    for p in files:
        im = Image.open(p).convert("RGB")
        if dpi and dpi < 300:
            w = max(1, int(round(im.width * dpi / 300.0)))
            h = max(1, int(round(im.height * dpi / 300.0)))
            im = im.resize((w, h), Image.LANCZOS)
        q = os.path.join(d, os.path.splitext(os.path.basename(p))[0] + ".jpg")
        im.save(q, quality=quality, optimize=True, progressive=True)
        out.append(q)
    return out


def _file_url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def run(infos, paper, layout, workdir, outdir, edge_exe, scale=1.6,
        pdf=True, verbose=True, ls=LS_DEFAULT, pdf_dpi=200, pdf_quality=82):
    """infos: [{index, text_size, fig_size}]（按题目顺序）。返回生成的图片列表。

    paper 里若有 papers/rects（一组纸面），则第 i 页用第 i 张、并按该张自己的
    可用区排版；字号仍按 plan() 用交集算出来的 s，保证换哪张都不溢出。
    """
    pl = plan(infos, paper, layout, ls=ls)
    if verbose and pl.get("note"):
        print(f"  ! {pl['note']}", flush=True)
    os.makedirs(outdir, exist_ok=True)
    pw, ph = paper["w"], paper["h"]
    bg_names = paper.get("papers") or [os.path.basename(paper.get("paper_png") or "paper.png")]
    rects = paper.get("rects") or [[paper["x0"], paper["x1"], paper["y0"], paper["y1"]]]
    gap_target = float(layout.get("gap_target") or 0.048)     # 目标题距 = 该比例 × 页高

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
        # 题距：目标值 1.5 行左右 + ±12% 的自然抖动，多余的空间留给页脚，
        # 而不是把题距硬拉到上限（旧版题距在 31~180px 之间跳，同一册里忽紧忽松）
        rnd = random.Random(pi * 7919)
        want_gap = gap_target * plp["are_h"] * (1 + rnd.uniform(-0.12, 0.12))
        gap = max(plp["gap_min"], min(gap, want_gap, plp["gap_max"]))
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
                refw=pl["refw"], side=pl["side"], note=pl.get("note", ""),
                papers=bg_names[:len(files)], rects=[list(r) for r in rects],
                scales=scales)
    with open(os.path.join(outdir, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    if pdf:
        imgs = None
        if pdf_dpi and pdf_dpi < 300:
            imgs = _pdf_copies(files, workdir, dpi=pdf_dpi, quality=pdf_quality, verbose=verbose)
        html = ['<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<style>@page{{size:{pw}px {ph}px;margin:0}}',
                'html,body{margin:0}img{display:block;width:100%;page-break-after:always}',
                'img:last-child{page-break-after:auto}</style></head><body>']
        for i, p in enumerate(files):
            src = _file_url(imgs[i]) if imgs else os.path.basename(p)
            html.append(f'<img src="{src}">')
        html.append("</body></html>")
        hp = os.path.join(outdir, "_pdf.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write("".join(html))
        out_pdf = os.path.join(outdir, "手写稿.pdf")
        size = edge.pdf(edge_exe, hp, out_pdf)
        if verbose:
            print(f"  PDF：{out_pdf}（{size / 1e6:.1f} MB）", flush=True)
    return files
