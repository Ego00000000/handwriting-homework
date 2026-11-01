# -*- coding: utf-8 -*-
"""阶段 A：一题一页。

文字走手写接口（每行自带下划线，兼作行基准线）；图形用 SVG 画，
图上的标签再从「手写字条」里抠成透明贴片贴回去，保证和正文同一支笔。
"""
import hashlib
import json
import os

import api
import edge
import figures
import glyphs
import imgfast
import pngtool
import cwrap as tw

PAGE_W, PAGE_H = 2481, 3507      # A4 @300dpi
SHEET_FS, SHEET_LS = 64, 104     # 标签字条的字号/行距
LABEL_SHEET_W = 620


def page_html(text_png, blocks, w, h):
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>html,body{{margin:0;padding:0;background:#fff;overflow:hidden}}
#pg{{position:relative;width:{w}px;height:{h}px}}
#pg>img{{position:absolute;left:0;top:0;width:{w}px;height:{h}px}}</style></head>
<body><div id="pg"><img src="{text_png}">{''.join(blocks)}</div></body></html>"""


def _merge_into_slots(bands, count):
    """把带按行距归并回 count 个槽。

    服务端的手写笔锋偶尔断开，一行标签会被切成两条带（如 TVC 的 V 与 C 之间），
    按 gap 切行就会多出一行、整体错位。行距是固定的 SHEET_LS，
    所以按「带中心到首行中心的距离 / 行距」四舍五入就能定位它属于哪一行。
    """
    first = (bands[0][0] + bands[0][1]) / 2.0
    slots = [[] for _ in range(count)]
    for a, b in bands:
        i = int(round(((a + b) / 2.0 - first) / SHEET_LS))
        slots[max(0, min(count - 1, i))].append((a, b))
    if any(not s for s in slots):      # 有槽是空的，说明归并不可靠，原样返回让上层报错
        return bands
    return [(min(a for a, _ in s), max(b for _, b in s)) for s in slots]


def label_boxes(grey, w, h, count=None):
    """把一张字条按行切成 (x0,y0,x1,y1) 列表。"""
    m = imgfast.mask(grey, 200)
    bands = imgfast.bands(m, w, h, gap=6)
    if count and len(bands) != count:
        bands = _merge_into_slots(bands, count)
    out = []
    for a, b in bands:
        bb = imgfast.bbox(m, w, h, a, b + 1)
        if bb:
            out.append(bb)
    return out


def label_sheet(texts, path):
    """渲染一张含全部标签的手写字条，抠成透明 PNG。-> (w, h, boxes)"""
    imgs = api.render("\n".join(texts), font_size=SHEET_FS, line_spacing=SHEET_LS,
                      margins=(45, 45, 45, 45),
                      params={"isUnderlined": "false",
                              "width": str(LABEL_SHEET_W),
                              "height": str(160 + len(texts) * SHEET_LS)})
    w, h, grey = pngtool.decode_grey(imgs[0])
    rgba = imgfast.to_alpha_rgba(grey, w, h)
    with open(path, "wb") as f:
        f.write(pngtool.encode_rgba(w, h, rgba))
    return w, h, label_boxes(grey, w, h, len(texts))


def figure_block(spec, tag, fx, fy, workdir, img_dir=None, verbose=True):
    img_dir = img_dir or workdir
    """画一张图并返回 (html, 高度)。"""
    if isinstance(spec, str):
        spec = {"name": spec}
    if "file" in spec:
        src = spec["file"]
        size = spec.get("size")
        if not size:
            try:
                size = list(edge.read_size(src))
            except Exception:
                raise ValueError(f"外部图 {src} 需要显式给 size:[宽,高]")
        w, h = int(size[0]), int(size[1])
        name = f"ext_{tag}.png"
        with open(os.path.join(img_dir, name), "wb") as f:
            f.write(open(src, "rb").read())
        return (f'<img src="{name}" style="position:absolute;left:{fx}px;top:{fy}px;'
                f'width:{w}px;height:{h}px;z-index:4">', h)

    name = spec["name"]
    size = spec.get("size")
    fw, fh = (int(size[0]), int(size[1])) if size else (None, None)
    svg, labels = figures.draw(name, fw, fh)
    if fw is None:
        fw, fh = figures.REGISTRY[name][1], figures.REGISTRY[name][2]

    lname = f"lbl_{tag}.png"
    sw, sh, boxes = label_sheet([l["t"] for l in labels], os.path.join(img_dir, lname))
    if len(boxes) != len(labels):
        raise RuntimeError(
            f"标签行数不匹配：图 {name} 需要 {len(labels)} 个标签，字条切出 {len(boxes)} 行"
            f"（标签：{[l['t'] for l in labels]}）")

    out = [f'<div style="position:absolute;left:{fx}px;top:{fy}px;z-index:4">{svg}</div>']
    for l, (x0, y0, x1, y1) in zip(labels, boxes):
        k = l["size"] / (SHEET_FS * 0.50)
        iw, ih = (x1 - x0 + 1) * k, (y1 - y0 + 1) * k
        left = fx + l["x"]
        if l["anchor"] == "middle":
            left -= iw / 2
        elif l["anchor"] == "end":
            left -= iw
        top = fy + l["y"] - ih
        rot = (f"transform:rotate({l['rot']}deg);transform-origin:0 100%;" if l["rot"] else "")
        out.append(
            f'<div style="position:absolute;left:{left:.1f}px;top:{top:.1f}px;'
            f'width:{iw:.1f}px;height:{ih:.1f}px;overflow:hidden;z-index:6;{rot}">'
            f'<img src="{lname}" style="position:absolute;left:{-x0 * k:.1f}px;'
            f'top:{-y0 * k:.1f}px;width:{sw * k:.1f}px;height:{sh * k:.1f}px"></div>')
    return "".join(out), fh


def page_sig(txt, opts, page_w, page_h, budget):
    """渲染参数指纹：折行结果/字体/字号/行距/画布/每行字数 任一变化就重渲染。"""
    h = hashlib.md5()
    h.update(txt.encode("utf-8"))
    h.update(f"|{opts['font']}|{opts['font_size']}|{opts['line_spacing']}"
             f"|{page_w}x{page_h}|{round(float(budget), 2)}|{list(opts['margin'])}".encode())
    return h.hexdigest()[:10]


def _fig_height(spec):
    """图形块在本页画布上的高度（不画图也能算，用于渲染前预检）。"""
    if isinstance(spec, str):
        spec = {"name": spec}
    size = spec.get("size")
    if "file" in spec and not size:
        size = list(edge.read_size(spec["file"]))
    if size:
        return int(size[1])
    name = spec.get("name")
    if name not in figures.REGISTRY:
        raise ValueError(f"未知图形名：{name}（可用：{', '.join(sorted(figures.REGISTRY))}）")
    return int(figures.REGISTRY[name][2])


def check_room(index, figs, opts, lines, page_h, budget):
    """渲染前检查「文字行数」与「文字 + 图」是否放得进一张画布。

    旧版不检查，两种溢出都是静默的：
      · 行数超出画布高度 → 服务端直接裁掉超出的行（拆块量到的是裁完的墨迹，永远发现不了）；
      · 文字 + 图超出画布 → 图被画到画布外，拆块时才报「没检测到图形墨迹」，整个 build 中断。
    """
    top, bottom = opts["margin"][0], opts["margin"][1]
    ls = opts["line_spacing"]
    max_lines = int((page_h - top - 90) // ls)
    if lines > max_lines:
        raise ValueError(
            f"第 {index} 题文字 {lines} 行，超过一张画布 {max_lines} 行的上限，"
            f"超出的部分会被直接裁掉。\n"
            f"  每行约 {budget:.0f} 字，这张画布最多约 {int(max_lines * budget)} 字，"
            f"当前约 {int(lines * budget)} 字。\n"
            f"  处理办法：拆成两题（推荐），或压到 {max_lines} 行以内。")
    if not figs:
        return
    y_fig = top + lines * ls + 90
    need = sum(_fig_height(f) for f in figs) + 50 * len(figs)
    avail = page_h - bottom - y_fig
    if avail >= need:
        return
    fig_lines = max(0, int((page_h - bottom - 90 - need) // ls))
    raise ValueError(
        f"第 {index} 题：文字 {lines} 行 + {len(figs)} 张图（需 {need}px）放不进一张画布。\n"
        f"  画布高 {page_h}px，图要从 {y_fig}px 开始，只剩 {avail}px。\n"
        f"  配图题最多可写 {fig_lines} 行（约 {int(fig_lines * budget)} 字），当前 {lines} 行"
        f"（约 {int(lines * budget)} 字）。\n"
        f"  处理办法：把文字压到 {fig_lines} 行以内，或把图挪到下一题。")


def canvas_of(q, opts):
    """本题该用哪块画布：有图的用标准画布，无图的（默认）用加宽画布。

    只改画布宽度，字号/行距/边距都不动，所以纸上字一样大，只是每行多写几个字。
    """
    has_fig = bool(q.get("figures"))
    if opts.get("wide_no_fig") and not has_fig and opts.get("page_w_wide"):
        return (int(opts["page_w_wide"]), int(opts["page_h"]),
                float(opts["budget_wide"]))
    return int(opts["page_w"]), int(opts["page_h"]), float(opts["budget"])


def build(index, q, opts, workdir, edge_exe, force=False, verbose=True):
    """渲染第 index 题（从 1 开始）的一页，返回 (page_png, 折行后的文本)。"""
    pages_dir = os.path.join(workdir, "pages")
    os.makedirs(pages_dir, exist_ok=True)
    page_png = os.path.join(pages_dir, f"q{index}.png")

    page_w, page_h, budget = canvas_of(q, opts)
    raw = q.get("text", "")
    if opts.get("glyph_fix", True):
        # 字体写不出来的字符会被服务端静默跳过，先换掉或直接报错
        raw, _used, _missed = glyphs.sanitize(
            raw, opts["font"],
            strict=opts.get("glyph_strict", False),
            allow_drop=opts.get("allow_drop", False),
            tag=f"第 {index} 题", verbose=verbose)
    txt = tw.wrap(raw, budget, opts["cjk"], opts["ascii"])
    lines = txt.count("\n") + 1
    check_room(index, q.get("figures") or [], opts, lines, page_h, budget)
    sig = page_sig(txt, opts, page_w, page_h, budget)
    text_png = os.path.join(pages_dir, f"q{index}_text_{sig}.png")
    meta_path = os.path.join(pages_dir, f"q{index}.json")

    if not force and os.path.exists(page_png) and os.path.exists(text_png):
        ok = False
        try:
            with open(meta_path, encoding="utf-8") as f:
                ok = json.load(f).get("sig") == sig
        except Exception:
            ok = False
        if ok:
            try:
                ok = edge.read_size(page_png) == (page_w, page_h)
            except Exception:
                ok = False
        if ok:
            if verbose:
                print(f"  [{index}] 已有页图，跳过渲染", flush=True)
            return page_png, txt

    # 换过字号/画布会留下同题的旧文字图，先清掉
    for f in os.listdir(pages_dir):
        if f.startswith(f"q{index}_text_") and os.path.join(pages_dir, f) != text_png:
            try:
                os.remove(os.path.join(pages_dir, f))
            except OSError:
                pass

    if os.path.exists(text_png):
        if verbose:
            print(f"  [{index}] 复用手写文字图", flush=True)
    else:
        if verbose:
            tag = "" if page_w == opts["page_w"] else f"，画布 {page_w}"
            print(f"  [{index}] 渲染文字（{txt.count(chr(10)) + 1} 行{tag}）…", flush=True)
        imgs = api.render(txt, font=opts["font"], font_size=opts["font_size"],
                          line_spacing=opts["line_spacing"],
                          width=page_w, height=page_h,
                          margins=opts["margin"], verbose=verbose)
        if not imgs:
            raise RuntimeError(f"第 {index} 题：接口没有返回图片")
        with open(text_png, "wb") as f:
            f.write(imgs[0])

    y = opts["margin"][0] + lines * opts["line_spacing"] + 90
    html_blocks = []
    for j, spec in enumerate(q.get("figures") or [], 1):
        if isinstance(spec, str):
            spec = {"name": spec}
        s = spec.get("size")
        if "file" in spec and not s:
            s = list(edge.read_size(spec["file"]))
        fw = int(s[0]) if s else figures.REGISTRY[spec["name"]][1]
        fx = (page_w - fw) // 2
        h_html, fh = figure_block(spec, f"{index}_{j}", fx, y, workdir,
                                  img_dir=pages_dir, verbose=verbose)
        html_blocks.append(h_html)
        y += fh + 50

    html_path = os.path.join(pages_dir, f"q{index}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page_html(os.path.basename(text_png), html_blocks, page_w, page_h))

    edge.shot(edge_exe, html_path, page_png, page_w, page_h, scale=1.0)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(dict(sig=sig, page_w=page_w, page_h=page_h,
                       budget=round(budget, 3), lines=lines), f, ensure_ascii=False)
    if verbose:
        print(f"  [{index}] 页面完成 {page_w}x{page_h}", flush=True)
    return page_png, txt


def run(job, opts, workdir, edge_exe, only=None, force=False, verbose=True):
    """批量渲染，返回 [{index, text, page, figures}]。"""
    out = []
    for i, q in enumerate(job["questions"], 1):
        if only and i not in only:
            continue
        page, txt = build(i, q, opts, workdir, edge_exe, force=force, verbose=verbose)
        out.append(dict(index=i, text=txt, page=page,
                        figures=list(q.get("figures") or [])))
    with open(os.path.join(workdir, "pages.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    return out
