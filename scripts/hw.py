#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手写作业生成器 —— 唯一入口。

用法：
  hw.py doctor  [--edge PATH] [--offline]
  hw.py fonts   [--offline]
  hw.py paper   --paper 纸.jpg [--job job.json] [--clean-lines auto|on|off]
  hw.py build   --job job.json [--out-dir DIR] [--font 字体] [--scale 1.6]
  hw.py render|extract|layout --job job.json
  hw.py verify  --out-dir DIR
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import api          # noqa: E402
import edge         # noqa: E402
import figures      # noqa: E402
import imgfast      # noqa: E402
import paper as paperlib  # noqa: E402
import pngtool      # noqa: E402
import stage_extract  # noqa: E402
import stage_layout   # noqa: E402
import stage_page     # noqa: E402
import cwrap as tw  # noqa: E402

DEFAULTS = dict(
    font="一号字体",
    font_size=106,
    line_spacing=176,
    margin=[150, 150, 110, 110],
    budget=17.0,
    cjk=1.0,
    ascii=0.62,
    page_w=2481,
    page_h=3507,
    scale=1.6,
    jobs=min(8, os.cpu_count() or 4),
    # 无图题「写满整行」：只加宽画布（字号/行距不动），每行多写字；
    # 落到纸上的字大小由排版缩放统一，所以和有图题完全一致。
    wide_no_fig=True,
    wide_ratio=1.29,        # 宽画布 = page_w × 该系数（2481 → 3200）
)

# 内置纸面组（12 张白纸照片，按文件名顺序一页一张循环使用）
PAPER_DIR = os.path.normpath(os.path.join(HERE, "..", "papers"))
PAPER_EXT = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def default_papers(paper_dir=None):
    """返回默认纸面组（绝对路径，按文件名排序 = 使用顺序）。"""
    d = paper_dir or PAPER_DIR
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, n) for n in sorted(os.listdir(d))
            if n.lower().endswith(PAPER_EXT) and not n.startswith(".")]


# ---------------------------------------------------------------- job
def _job_from_dir(path):
    qs = []
    for name in sorted(os.listdir(path)):
        if not name.lower().endswith((".md", ".txt")):
            continue
        with open(os.path.join(path, name), encoding="utf-8") as f:
            raw = f.read().strip("\n")
        figs = []
        first = raw.split("\n", 1)[0].strip()
        if first.startswith("<!--") and "figure:" in first:
            figs = [x.strip() for x in first.split("figure:")[1].split("-->")[0].split(",") if x.strip()]
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        qs.append(dict(text=raw, figures=figs))
    if not qs:
        raise SystemExit(f"{path} 里没有 .md/.txt 题面文件")
    return dict(questions=qs)


def load_job(path=None, text_dir=None, paper=None, font=None, paper_dir=None):
    if text_dir:
        job = _job_from_dir(text_dir)
        job["paper"] = paper
        job.setdefault("layout", {})
    else:
        if not path or not os.path.exists(path):
            raise SystemExit(f"找不到 job 文件：{path}")
        with open(path, encoding="utf-8") as f:
            job = json.load(f)
        base = os.path.dirname(os.path.abspath(path))
        # 相对路径按 job 文件所在目录解析
        for key in ("paper",):
            if job.get(key) and not os.path.isabs(job[key]):
                job[key] = os.path.normpath(os.path.join(base, job[key]))
        if job.get("paper_dir") and not os.path.isabs(job["paper_dir"]):
            job["paper_dir"] = os.path.normpath(os.path.join(base, job["paper_dir"]))
        for q in job.get("questions", []):
            for spec in q.get("figures") or []:
                if isinstance(spec, dict) and spec.get("file") and not os.path.isabs(spec["file"]):
                    spec["file"] = os.path.normpath(os.path.join(base, spec["file"]))

    if paper_dir:
        job["paper_dir"] = paper_dir
    if paper:
        job["paper"] = paper
    if font:
        job["font"] = font
    if not job.get("paper"):
        # 没指定纸面就用内置纸面组（12 张白纸，按顺序一页一张）
        ps = default_papers(job.get("paper_dir"))
        if not ps:
            raise SystemExit("job 里没有 paper 字段，也没用 --paper 指定纸面图片"
                             f"（默认纸面目录 {job.get('paper_dir') or PAPER_DIR} 里也没有可用图片）")
        job["papers"] = ps
        job["paper"] = ps[0]
    if not job.get("questions"):
        raise SystemExit("job 里没有 questions")
    return job


def _derive_wide(o):
    """算出「无图题宽画布」的尺寸与每行字数。

    字号、行距、边距一个都不改，只是把画布加宽，于是同一行能多放字；
    排版时全篇共用一个缩放系数，所以落到纸上的字与有图题一样大。
    """
    o["page_w_wide"] = int(round(o["page_w"] * float(o["wide_ratio"])))
    avail = o["page_w_wide"] - o["margin"][2] - o["margin"][3]     # 左/右
    # 留 1 个字余量，防止服务端再折行时把字挤到画布外
    o["budget_wide"] = max(float(o["budget"]), avail / float(o["font_size"]) - 1.0)
    return o


def opts_of(job, args):
    o = dict(DEFAULTS)
    o["font"] = job.get("font") or o["font"]      # 保持别名，真正渲染前再解析
    lay = job.get("layout") or {}
    for k_src, k_dst in (("font_size", "font_size"), ("line_spacing", "line_spacing"),
                         ("margin", "margin"), ("budget", "budget"),
                         ("page_w", "page_w"), ("page_h", "page_h"),
                         ("wide_no_fig", "wide_no_fig"), ("wide_ratio", "wide_ratio")):
        if k_src in lay:
            o[k_dst] = lay[k_src]
    if getattr(args, "scale", None):
        o["scale"] = args.scale
    if getattr(args, "jobs", None):
        o["jobs"] = args.jobs
    if getattr(args, "no_wide", False):
        o["wide_no_fig"] = False
    return _derive_wide(o)


def workdir_of(args, job):
    name = job.get("name") or "job"
    out = args.out_dir or os.path.join(os.getcwd(), "out", name)
    return os.path.abspath(out), os.path.join(os.path.abspath(out), "work")


# ---------------------------------------------------------------- 子命令
def cmd_doctor(args):
    print("== 环境自检 ==")
    ok_all = True

    print(f"Python      : {sys.version.split()[0]}  ({'OK' if sys.version_info >= (3, 8) else '需要 >=3.8'})")
    ok_all &= sys.version_info >= (3, 8)

    try:
        e = edge.find(args.edge)
        print(f"浏览器      : {e}  OK")
    except Exception as ex:
        print(f"浏览器      : 未找到\n  {ex}")
        ok_all = False

    if args.offline:
        print("接口        : 跳过（--offline）")
    else:
        ok, detail = api.probe()
        print(f"接口        : {'OK 版本 ' + str(detail) if ok else '不可用：' + str(detail)}")
        ok_all &= ok
        online = api.server_fonts() if ok else []
        for f in api.list_fonts():
            hit = "" if not online else ("  OK" if f["ttf"] in online else "  !! 站点已无此字体")
            print(f"字体        : {f['alias']} = {f['ttf'][:-4]}（{f['note']}）{hit}")

    for mod in ("PIL", "numpy"):
        try:
            m = __import__(mod)
            print(f"{mod:<12}: 可用（{getattr(m, '__version__', '?')}，可走加速轨）")
        except Exception:
            print(f"{mod:<12}: 未安装（走零依赖轨，不影响功能）")

    print(f"CPU 逻辑核  : {os.cpu_count()}")
    print("结论        : " + ("可以开始生成" if ok_all else "有阻塞项，先按上面的提示处理"))
    return 0 if ok_all else 1


def cmd_fonts(args):
    print("可用字体（按顺序编号）：")
    for f in api.list_fonts():
        mark = "  ← 默认" if f["alias"] == api.DEFAULT_FONT else ""
        print(f'  {f["alias"]}  ({f["ttf"][:-4]})  {f["note"]}{mark}')
    print("\n--font 一号字体 / --font 2号 都可以；只开放这两种。")
    if not args.offline:
        online = api.server_fonts()
        if online:
            miss = [f["ttf"] for f in api.list_fonts() if f["ttf"] not in online]
            print("站点核对：" + ("两种都在线" if not miss else f"站点缺少 {miss}"))
    return 0


def cmd_papers(args):
    ps = default_papers(getattr(args, "paper_dir", None))
    if not ps:
        print(f"纸面目录为空：{getattr(args, 'paper_dir', None) or PAPER_DIR}")
        return 1
    print(f"内置纸面组（{len(ps)} 张，按此顺序一页一张循环）：")
    for i, p in enumerate(ps, 1):
        print(f"  {i:>2}. {os.path.basename(p)}")
    print(f"\n目录：{os.path.dirname(ps[0])}")
    return 0


def cmd_paper(args):
    job = None
    if args.job:
        job = load_job(args.job)
    src = args.paper or (job or {}).get("paper")
    if not src:
        raise SystemExit("需要 --paper（或 --job 里带 paper）")
    out = os.path.abspath(args.out_dir or os.path.join(os.getcwd(), "out", "paper"))
    work = os.path.join(out, "work")
    info = paperlib.prepare(src, work, mode=args.clean_lines)
    info["paper_png"] = os.path.join(work, "paper.png")
    with open(os.path.join(work, "paper.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in info.items() if k != "paper_png"},
                     ensure_ascii=False, indent=1))
    print(f"\n清理后纸面：{info['paper_png']}")
    return 0


def _ensure_paper(job, work, clean, reuse=True, verbose=True):
    """纸面预处理（单张或多张一组）。多张时一页一张、循环使用。"""
    papers = job.get("papers") or [job["paper"]]
    pj = os.path.join(work, "paper.json")
    sig = [os.path.abspath(p) for p in papers]
    if reuse and os.path.exists(pj):
        with open(pj, encoding="utf-8") as f:
            info = json.load(f)
        names = info.get("papers") or ["paper.png"]
        have = all(os.path.exists(os.path.join(work, n)) for n in names)
        if have and info.get("sources", [info.get("source")]) == sig:
            if verbose:
                print(f"  纸面：复用 {info['w']}x{info['h']}（{len(names)} 张）", flush=True)
            return info

    os.makedirs(work, exist_ok=True)
    if len(papers) > 1:
        info = paperlib.prepare_set(papers, work, mode=clean, verbose=verbose)
    else:
        info = paperlib.prepare(papers[0], work, mode=clean, verbose=verbose)
        info["sources"] = sig
    with open(pj, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    return info


def cmd_build(args):
    t0 = time.time()
    job = load_job(args.job, args.text_dir, args.paper, args.font,
                   getattr(args, "paper_dir", None))
    o = opts_of(job, args)
    out, work = workdir_of(args, job)

    font_info = api.resolve_font(o["font"])            # 别名 -> 真实 ttf
    o["font"] = font_info["ttf"]
    e = edge.find(args.edge)
    os.makedirs(out, exist_ok=True)

    _ps = job.get("papers") or [job["paper"]]
    print(f"[1/4] 纸面  {len(_ps)} 张，第1页用 {os.path.basename(_ps[0])}"
          + ("" if len(_ps) == 1 else "（按顺序逐页循环）"), flush=True)
    paper = _ensure_paper(job, work, args.clean_lines, reuse=not args.force)

    print(f"[2/4] 渲染 {len(job['questions'])} 题"
          f"（字体 {font_info['alias']} / {font_info['ttf'][:-4]}）", flush=True)
    if o["wide_no_fig"]:
        n_no = sum(1 for q in job["questions"] if not q.get("figures"))
        if n_no:
            print(f"      无图题 {n_no} 道：画布 {o['page_w']}→{o['page_w_wide']}，"
                  f"每行约 {o['budget_wide']:.0f} 字（字号 {o['font_size']} 不变）", flush=True)
    t = time.time()
    pages = stage_page.run(job, o, work, e, force=args.force)
    print(f"      用时 {time.time() - t:.1f}s", flush=True)

    print("[3/4] 拆块", flush=True)
    t = time.time()
    infos = stage_extract.run(pages, os.path.join(work, "blk"),
                              top=o["margin"][0], ls=o["line_spacing"],
                              jobs=o["jobs"], force=args.force)
    print(f"      用时 {time.time() - t:.1f}s", flush=True)

    print(f"[4/4] 排版  {paper['w']}x{paper['h']}", flush=True)
    order = [infos[str(i + 1)] for i in range(len(job["questions"]))]
    lay = dict(job.get("layout") or {})
    if args.one_per_page:
        lay["mode"] = "one"
    lay.setdefault("mode", "multi")
    lay.setdefault("figure_side", "right")
    files = stage_layout.run(order, paper, lay, work, out, e,
                             scale=o["scale"], pdf=not args.no_pdf)

    if not args.keep_pages:
        for p in pages:
            try:
                os.remove(p["page"])
            except OSError:
                pass

    print(f"\n完成：{len(files)} 页，总用时 {time.time() - t0:.1f}s")
    print(f"输出目录：{out}")
    for f in files:
        print(f"  {os.path.basename(f)}")
    return 0


def _stages(args, which):
    job = load_job(args.job, args.text_dir, args.paper, args.font,
                   getattr(args, "paper_dir", None))
    o = opts_of(job, args)
    out, work = workdir_of(args, job)
    o["font"] = api.resolve_font(o["font"])["ttf"]
    e = edge.find(args.edge)
    os.makedirs(out, exist_ok=True)

    if which in ("render", "all"):
        stage_page.run(job, o, work, e, force=args.force)
    if which in ("extract", "all"):
        with open(os.path.join(work, "pages.json"), encoding="utf-8") as f:
            pages = json.load(f)
        stage_extract.run(pages, os.path.join(work, "blk"), top=o["margin"][0],
                          ls=o["line_spacing"], jobs=o["jobs"], force=args.force)
    if which in ("layout", "all"):
        paper = _ensure_paper(job, work, args.clean_lines, reuse=True)
        with open(os.path.join(work, "blk", "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        order = [meta[str(i + 1)] for i in range(len(job["questions"]))]
        stage_layout.run(order, paper, job.get("layout") or {}, work, out, e,
                         scale=o["scale"], pdf=not args.no_pdf)
    return 0


def cmd_verify(args):
    out = os.path.abspath(args.out_dir or os.path.join(os.getcwd(), "out", "job"))
    if not os.path.isdir(out):
        print(f"输出目录不存在：{out}")
        return 1
    pages = [f for f in os.listdir(out) if f.startswith("第") and f.endswith(".png")]
    if not pages:
        print(f"{out} 下没有成品页图")
        return 1
    pages.sort(key=lambda s: int("".join(c for c in s if c.isdigit()) or 0))
    lj = os.path.join(out, "layout.json")
    if os.path.exists(lj):
        with open(lj, encoding="utf-8") as f:
            meta = json.load(f)
        print(f"分页：{meta.get('pages')}  缩放 {meta.get('scale_paper'):.3f}")
    print(f"检查 {len(pages)} 页：{out}")
    bad = 0
    for name in pages:
        p = os.path.join(out, name)
        w, h = edge.read_size(p)
        note = ""
        if args.deep:
            _, _, grey = pngtool.decode_grey(open(p, "rb").read())
            rules = imgfast.find_rule_rows(imgfast.mask(grey, imgfast.DARK), w, h)
            if rules:
                note = f"  !! 残留整行黑线 {len(rules)} 条（行号 {rules[:5]}）"
                bad += 1
        print(f"  {name}  {w}x{h}{note}")
    if bad:
        print(f"\n发现 {bad} 页有问题")
        return 1
    print("\n全部通过：尺寸一致" + ("、无整行残留黑线" if args.deep else ""))
    return 0


# ---------------------------------------------------------------- argparse
def main(argv=None):
    p = argparse.ArgumentParser(prog="hw.py", description="手写作业生成器")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, need_job=True, force=False):
        if need_job:
            sp.add_argument("--job", help="job.json 路径")
            sp.add_argument("--text-dir", help="题面目录（每个 .md/.txt 一题）")
            sp.add_argument("--paper", help="纸面图片（覆盖 job 里的 paper）")
            sp.add_argument("--paper-dir", help="纸面目录：按文件名顺序一页一张循环使用"
                                                "（默认就是内置的 12 张白纸）")
        sp.add_argument("--out-dir", help="输出目录（默认 ./out/<job名>）")
        sp.add_argument("--font", help="一号字体（默认）或 二号字体；也认 1号/2号")
        sp.add_argument("--scale", type=float, help="出图倍率，默认 1.6")
        sp.add_argument("--jobs", type=int, help="拆块并行进程数")
        sp.add_argument("--edge", help="浏览器可执行文件路径")
        sp.add_argument("--offline", action="store_true", help="不访问网络（只用内置字体表）")
        if force:
            sp.add_argument("--force", action="store_true", help="忽略缓存与断点，全部重跑")
            sp.add_argument("--keep-pages", action="store_true", help="保留 work/pages 下的整页图")
            sp.add_argument("--no-pdf", action="store_true", help="只出图片，不出 PDF")
            sp.add_argument("--clean-lines", default="auto", choices=["auto", "on", "off"],
                            help="纸面横线处理：auto 自动判断 / on 强制擦 / off 不动")

    d = sub.add_parser("doctor", help="环境自检")
    d.add_argument("--edge")
    d.add_argument("--offline", action="store_true")
    d.set_defaults(func=cmd_doctor)

    f = sub.add_parser("fonts", help="列出可用字体")
    f.add_argument("--offline", action="store_true")
    f.set_defaults(func=cmd_fonts)

    pl = sub.add_parser("papers", help="列出内置纸面组（按使用顺序）")
    pl.add_argument("--paper-dir")
    pl.set_defaults(func=cmd_papers)

    pa = sub.add_parser("paper", help="纸面预处理（擦线 + 探测可用区）")
    pa.add_argument("--paper")
    pa.add_argument("--job")
    pa.add_argument("--out-dir")
    pa.add_argument("--clean-lines", default="auto", choices=["auto", "on", "off"])
    pa.set_defaults(func=cmd_paper)

    b = sub.add_parser("build", help="一次跑完：渲染 → 拆块 → 排版")
    common(b, force=True)
    b.add_argument("--one-per-page", action="store_true", help="一题一页")
    b.add_argument("--no-wide", action="store_true",
                   help="无图题不加宽画布（回到「每行固定 17 字」的老版式）")
    b.set_defaults(func=cmd_build)

    for name, fn in (("render", "render"), ("extract", "extract"), ("layout", "layout")):
        sp = sub.add_parser(name, help=f"只跑 {name} 阶段")
        common(sp, force=True)
        sp.add_argument("--no-wide", action="store_true",
                        help="无图题不加宽画布（回到老版式）")
        sp.set_defaults(func=lambda a, w=fn: _stages(a, w))

    v = sub.add_parser("verify", help="校验成品")
    v.add_argument("--out-dir")
    v.add_argument("--deep", action="store_true", help="逐像素检查是否残留整行黑线")
    v.set_defaults(func=cmd_verify)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as ex:
        print(f"\n错误：{ex}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
