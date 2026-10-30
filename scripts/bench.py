#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拆块性能基准：把「解码 → 去横线 → 包围盒 → 出 RGBA」真实跑一遍。

用法： python bench.py <一整页 PNG> [--pure]

--pure 强制关掉 Pillow 走纯标准库轨，用来对比两条轨的耗时（结果必须一致）。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

import imgfast      # noqa: E402
import pngtool      # noqa: E402

OLD_PER_PAGE = 19.0   # 旧版逐像素实现的历史实测值（去掉辅助线+包围盒+出图）


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[1]
    if "--pure" in argv:
        pngtool.HAVE_PIL = False
    data = open(path, "rb").read()
    track = "Pillow 加速轨" if pngtool.HAVE_PIL else "纯标准库轨"

    t = time.perf_counter()
    w, h, grey = pngtool.decode_grey(data)
    t_dec = time.perf_counter() - t

    t = time.perf_counter()
    m = imgfast.mask(grey, imgfast.DARK)
    rules = imgfast.find_rule_rows(m, w, h)
    grey = imgfast.clear_rows(grey, w, h, rules)
    t_rules = time.perf_counter() - t

    t = time.perf_counter()
    mi = imgfast.mask(grey, imgfast.INK)
    box = imgfast.bbox(mi, w, h, 0, h)
    t_box = time.perf_counter() - t

    t = time.perf_counter()
    rgba, bw, bh = imgfast.cut_rgba(grey, w, h, box, pad=6)
    t_cut = time.perf_counter() - t

    total = t_dec + t_rules + t_box + t_cut
    print(f"图像 {w}x{h}  {w * h / 1e6:.1f}M 像素  {len(data) / 1e6:.1f}MB  [{track}]")
    print(f"  解码            {t_dec:6.3f}s")
    print(f"  去辅助线        {t_rules:6.3f}s   剔除 {len(rules)} 行")
    print(f"  墨迹包围盒      {t_box:6.3f}s   {box}")
    print(f"  出 RGBA         {t_cut:6.3f}s   {bw}x{bh}")
    print(f"  ------------------------------")
    print(f"  单页合计        {total:6.2f}s")
    print(f"  旧版单页实测    {OLD_PER_PAGE:6.2f}s  →  提速 {OLD_PER_PAGE / max(total, 1e-6):.0f}×")
    print(f"  14 题串行 ≈ {total * 14:.0f}s；8 进程并行 ≈ {total * 14 / 8:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
