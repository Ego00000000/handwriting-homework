# -*- coding: utf-8 -*-
"""手绘风曲线图（SVG）+ 图库注册表。

设计上只做两件事：把数据坐标映射成画面坐标、给每段线加一点随机抖动，
所有坐标都带种子，所以同一张图每次画出来完全一样（可回归比对）。

标签不在这里画 —— text() 只把坐标记进 LABELS，由 stage_page 换成手写贴片。
"""
import random

INK = "#1a1a1a"

LABELS = []
SCALE = 1.0


def reset(scale=1.0):
    """清空标签、还原线宽缩放（旧版只清 LABELS，导致 SCALE 会跨图污染）。"""
    global SCALE
    del LABELS[:]
    SCALE = scale


class H:
    """一块手绘坐标系：把 (x,y) 数据坐标映射到 w×h 画面。"""

    def __init__(self, w, h, xmax, ymin, ymax, pl=90, pr=40, pt=30, pb=60, seed=1, ox=0, oy=0):
        self.ox, self.oy = ox, oy
        self.w, self.h = w, h
        self.xmax, self.ymin, self.ymax = xmax, ymin, ymax
        self.pl, self.pr, self.pt, self.pb = pl, pr, pt, pb
        self.rnd = random.Random(seed)

    def X(self, x):
        return self.pl + x / self.xmax * (self.w - self.pl - self.pr)

    def Y(self, y):
        return self.h - self.pb - (y - self.ymin) / (self.ymax - self.ymin) * (self.h - self.pb - self.pt)

    def _jitter(self, amp):
        return (self.rnd.random() - 0.5) * 2 * amp

    def _poly(self, pts, color=INK, lw=6, amp=1.2, dash=None):
        lw = lw * SCALE
        out = []
        for i, (px, py) in enumerate(pts):
            jx, jy = self._jitter(amp), self._jitter(amp)
            out.append(("M" if i == 0 else "L") + f" {px + jx:.1f},{py + jy:.1f}")
        ds = f' stroke-dasharray="{dash}"' if dash else ""
        return (f'<path d="{" ".join(out)}" fill="none" stroke="{color}" stroke-width="{lw}" '
                f'stroke-linecap="round" stroke-linejoin="round"{ds}/>')

    def line(self, xa, ya, xb, yb, color=INK, lw=6, amp=1.0, dash=None, n=14):
        lw = lw * SCALE
        pts = [(self.X(xa) + (self.X(xb) - self.X(xa)) * i / n,
                self.Y(ya) + (self.Y(yb) - self.Y(ya)) * i / n) for i in range(n + 1)]
        return self._poly(pts, color, lw, amp, dash)

    def curve(self, f, x0, x1, color=INK, lw=6, amp=1.3, n=90, yclip=None, dash=None):
        pts = []
        for i in range(n + 1):
            x = x0 + (x1 - x0) * i / n
            y = f(x)
            if yclip and (y > yclip[1] or y < yclip[0]):
                continue
            pts.append((self.X(x), self.Y(y)))
        return self._poly(pts, color, lw, amp, dash)

    def dot(self, x, y, r=9, color=INK):
        return f'<circle cx="{self.X(x):.1f}" cy="{self.Y(y):.1f}" r="{r}" fill="{color}"/>'

    def text(self, x, y, s, size=48, color=INK, anchor="start", rot=0):
        LABELS.append(dict(t=s, x=self.X(x) + self.ox, y=self.Y(y) + self.oy,
                           size=size, anchor=anchor, rot=rot))
        return ""

    def axes(self, xlab="", ylab=""):
        s = [self.line(0, 0, self.xmax * 0.995, 0, lw=6),
             self.line(0, 0, 0, self.ymax * 0.995, lw=6)]
        ax, ay = self.X(self.xmax * 0.995), self.Y(0)
        s.append(f'<path d="M {ax-20:.1f},{ay-11:.1f} L {ax+6:.1f},{ay:.1f} L {ax-20:.1f},{ay+11:.1f}" '
                 f'fill="none" stroke="{INK}" stroke-width="{6*SCALE:.1f}" stroke-linecap="round"/>')
        bx, by = self.X(0), self.Y(self.ymax * 0.995)
        s.append(f'<path d="M {bx-11:.1f},{by+20:.1f} L {bx:.1f},{by-6:.1f} L {bx+11:.1f},{by+20:.1f}" '
                 f'fill="none" stroke="{INK}" stroke-width="{6*SCALE:.1f}" stroke-linecap="round"/>')
        if xlab:
            LABELS.append(dict(t=xlab, x=ax + self.ox, y=ay + 58 + self.oy,
                               size=46, anchor="end", rot=0))
        return "".join(s)

    def svg(self, body, w=None, h=None):
        w, h = w or self.w, h or self.h
        return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
                f'xmlns="http://www.w3.org/2000/svg">{body}</svg>')


# ---------------- 生产函数 ----------------
a, b = 3.0, 0.25
TP = lambda L: a * L ** 2 - b * L ** 3
MP = lambda L: 2 * a * L - 3 * b * L ** 2
AP = lambda L: a * L - b * L ** 2

# ---------------- 成本函数 ----------------
TFC = 60.0
MC = lambda Q: 0.15 * Q ** 2 - 3 * Q + 20
TVC = lambda Q: 0.05 * Q ** 3 - 1.5 * Q ** 2 + 20 * Q
TC = lambda Q: TVC(Q) + TFC
AVC = lambda Q: 0.05 * Q ** 2 - 1.5 * Q + 20
AC = lambda Q: AVC(Q) + TFC / Q
AFC = lambda Q: TFC / Q
QAC = 17.05


def _g(body, dx=0, dy=0):
    return f'<g transform="translate({dx},{dy})">' + "".join(body) + "</g>"


def fig_tp_mp(w=980, h=760):
    """上：TP；下：MP"""
    top_h = int(h * 0.52)
    bot_h = h - top_h - 20
    t = H(w, top_h, 10.15, 0, 70, pl=95, pr=45, pt=34, pb=56, seed=7)
    body = [t.axes("L", "")]
    body.append(t.curve(TP, 0, 10.0, lw=7.5, yclip=(0, 70)))
    body.append(t.text(9.0, TP(9.0) + 5.5, "TP", size=50, anchor="end", rot=-4))
    body.append(t.line(4, 0, 4, TP(4), lw=3, amp=0.8, dash="14 12", n=16))
    body.append(t.line(0, TP(4), 4, TP(4), lw=3, amp=0.8, dash="14 12", n=16))
    body.append(t.dot(4, TP(4), 8))
    body.append(t.line(8, 0, 8, TP(8), lw=3, amp=0.8, dash="14 12", n=16))
    body.append(t.dot(8, TP(8), 8))
    d = H(w, bot_h, 10.15, -17, 17, pl=95, pr=45, pt=26, pb=56, seed=11, oy=top_h + 20)
    body.append(f'<g transform="translate(0,{top_h + 20})">')
    body.append(d.axes("L", ""))
    body.append(d.curve(MP, 0, 10.05, lw=7.5, yclip=(-17, 17)))
    body.append(d.text(9.1, MP(9.1) - 5.0, "MP", size=50, anchor="end", rot=-4))
    body.append(d.dot(4, MP(4), 8))
    body.append(d.line(4, 0, 4, MP(4), lw=3, amp=0.8, dash="14 12", n=14))
    body.append("</g>")
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + "".join(body) + "</svg>")


def fig_ap_mp(w=980, h=760):
    """MP 与 AP 同图"""
    p = H(w, h, 10.15, -17, 17, pl=95, pr=45, pt=34, pb=64, seed=3)
    body = [p.axes("L", "")]
    body.append(p.curve(MP, 0, 10.05, lw=7.5, yclip=(-17, 17)))
    body.append(p.curve(AP, 0, 10.05, lw=7.5, yclip=(-17, 17)))
    body.append(p.text(9.5, MP(9.5) - 4.2, "MP", size=50, anchor="end", rot=-4))
    body.append(p.text(9.35, AP(9.35) + 2.4, "AP", size=50, anchor="start", rot=-4))
    body.append(p.dot(6, AP(6), 9))
    body.append(p.line(6, 0, 6, AP(6), lw=3, amp=0.8, dash="14 12", n=16))
    body.append(p.line(0, AP(6), 6, AP(6), lw=3, amp=0.8, dash="14 12", n=18))
    body.append(p.dot(4, MP(4), 7))
    return p.svg("".join(body))


def fig_isoquant(w=980, h=760):
    """三条等产量线"""
    p = H(w, h, 12.2, 0, 12.6, pl=100, pr=60, pt=40, pb=70, seed=21)
    body = [p.axes("L", "")]
    for Q in (9, 25, 49):
        body.append(p.curve(lambda L, Q=Q: Q / L, Q / 12.6, 12.0, lw=7.5, yclip=(0, 12.6)))
    return p.svg("".join(body))


def fig_optcombo(w=980, h=820):
    """等成本线与等产量线相切于 E"""
    p = H(w, h, 11.7, 0, 10.2, pl=100, pr=60, pt=40, pb=70, seed=33)
    body = [p.axes("L", "")]
    body.append(p.curve(lambda L: 20 / L, 20 / 10.2, 11.6, lw=7.5, yclip=(0, 10.2)))
    body.append(p.curve(lambda L: 9 / L, 9 / 10.2, 11.6, lw=5, amp=1.1, yclip=(0, 10.2)))
    body.append(p.line(0, 8, 10, 0, lw=6.5, amp=1.0, n=18))
    body.append(p.text(-0.3, 8.4, "A", size=48, anchor="end", rot=-3))
    body.append(p.text(10.1, -0.8, "B", size=48, anchor="middle", rot=-3))
    body.append(p.dot(1.29, 9 / 1.29, 8))
    body.append(p.dot(8.71, 9 / 8.71, 8))
    body.append(p.dot(5, 4, 10))
    body.append(p.text(5.35, 4.7, "E", size=50, rot=-4))
    body.append(p.line(5, 0, 5, 4, lw=3, amp=0.8, dash="14 12", n=14))
    body.append(p.line(0, 4, 5, 4, lw=3, amp=0.8, dash="14 12", n=16))
    body.append(p.text(4.7, -0.9, "L*", size=46, anchor="end", rot=-3))
    body.append(p.text(-0.3, 3.5, "K*", size=46, anchor="end", rot=-3))
    body.append(p.text(10.3, 2.6, "Q*", size=48, anchor="end", rot=-4))
    body.append(p.text(9.4, 1.05, "Q0", size=46, anchor="middle", rot=-3))
    return p.svg("".join(body))


def fig_totalcost(w=980, h=600):
    """TFC / TVC / TC"""
    p = H(w, h, 31, 0, 740, pl=95, pr=55, pt=40, pb=70, seed=41)
    body = [p.axes("Q", "")]
    body.append(p.curve(TVC, 0, 30, lw=7.5))
    body.append(p.curve(TC, 0, 30, lw=7.5))
    body.append(p.curve(lambda Q: TFC, 0, 30, lw=7.0, amp=0.9))
    body.append(p.text(28.6, 138, "TFC", size=44, anchor="end", rot=-3))
    body.append(p.text(26.0, TVC(26.0) - 120, "TVC", size=44, rot=-4))
    body.append(p.text(20.4, TC(20.4) + 120, "TC", size=44, rot=-4))
    body.append(p.dot(10, TVC(10), 8))
    return p.svg("".join(body))


def fig_avgcost(w=980, h=680):
    """AFC / AVC / AC / MC"""
    p = H(w, h, 33, 0, 76, pl=95, pr=55, pt=40, pb=70, seed=47)
    body = [p.axes("Q", "")]
    body.append(p.curve(AFC, 1.2, 32.6, lw=5.5, amp=1.1, dash="18 14"))
    body.append(p.curve(AVC, 0.6, 32.6, lw=7.5))
    body.append(p.curve(AC, 1.2, 32.6, lw=7.5))
    body.append(p.curve(MC, 0.6, 32.6, lw=7.5, yclip=(0, 76)))
    body.append(p.text(32.4, 7.0, "AFC", size=40, anchor="end", rot=-3))
    body.append(p.text(32.4, 17.0, "AVC", size=40, anchor="end", rot=-3))
    body.append(p.text(32.4, 31.5, "AC", size=40, anchor="end", rot=-3))
    body.append(p.text(30.0, 72.0, "MC", size=40, anchor="end", rot=-3))
    return p.svg("".join(body))


def fig_avc_ac_mc(w=980, h=680):
    """AVC / AC / MC 与两个最低点"""
    p = H(w, h, 33, 0, 70, pl=95, pr=55, pt=40, pb=70, seed=53)
    body = [p.axes("Q", "")]
    body.append(p.curve(AVC, 0.6, 32.6, lw=7.5))
    body.append(p.curve(AC, 1.4, 32.6, lw=7.5))
    body.append(p.curve(MC, 0.6, 32.6, lw=7.5, yclip=(0, 70)))
    body.append(p.text(31.8, AVC(31.8) - 9, "AVC", size=40, anchor="end", rot=-3))
    body.append(p.text(32.4, 31.5, "AC", size=40, anchor="end", rot=-3))
    body.append(p.text(27.4, MC(27.4) + 8, "MC", size=40, anchor="end", rot=-3))
    body.append(p.dot(15, AVC(15), 9))
    body.append(p.dot(QAC, AC(QAC), 9))
    body.append(p.line(15, 0, 15, AVC(15), lw=3, amp=0.8, dash="14 12", n=14))
    body.append(p.line(QAC, 0, QAC, AC(QAC), lw=3, amp=0.8, dash="14 12", n=14))
    body.append(p.text(14.3, -4.0, "Q1", size=42, anchor="end", rot=-3))
    body.append(p.text(18.0, -4.0, "Q2", size=42, rot=-3))
    return p.svg("".join(body))


def fig_mirror_avg(w=1000, h=560):
    """左：MP/AP；右：AVC/MC"""
    half = w // 2
    h1 = H(half, h, 10.15, -17, 17, pl=80, pr=30, pt=40, pb=64, seed=61)
    b1 = [h1.axes("L", "")]
    b1.append(h1.curve(MP, 0, 10.05, lw=6.5, yclip=(-17, 17)))
    b1.append(h1.curve(AP, 0, 10.05, lw=6.5, yclip=(-17, 17)))
    b1.append(h1.text(8.6, MP(8.6) - 6, "MP", size=44, anchor="end", rot=-4))
    b1.append(h1.text(6.6, AP(6.6) + 9.0, "AP", size=42, anchor="middle", rot=-4))
    b1.append(h1.dot(4, MP(4), 7))

    h2 = H(half, h, 33, 0, 64, pl=90, pr=34, pt=40, pb=64, seed=67, ox=half)
    b2 = [h2.axes("Q", "")]
    b2.append(h2.curve(AVC, 0.6, 32.6, lw=6.5))
    b2.append(h2.curve(MC, 0.6, 32.6, lw=6.5, yclip=(0, 64)))
    b2.append(h2.text(24.0, AVC(24.0) - 6, "AVC", size=44, anchor="middle", rot=-3))
    b2.append(h2.text(25.6, MC(25.6) + 7, "MC", size=44, rot=-4))
    b2.append(h2.dot(15, AVC(15), 7))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + _g(b1) + _g(b2, half) + "</svg>")


def fig_mirror_total(w=1000, h=560):
    """左：TP；右：TVC/TC"""
    half = w // 2
    h1 = H(half, h, 10.15, 0, 74, pl=80, pr=30, pt=40, pb=64, seed=71)
    b1 = [h1.axes("L", "")]
    b1.append(h1.curve(TP, 0, 10.0, lw=6.5, yclip=(0, 74)))
    b1.append(h1.text(9.0, TP(9.0) - 9, "TP", size=44, anchor="end", rot=-4))
    b1.append(h1.dot(4, TP(4), 7))
    b1.append(h1.dot(8, TP(8), 7))

    h2 = H(half, h, 31, 0, 740, pl=90, pr=34, pt=40, pb=64, seed=79, ox=half)
    b2 = [h2.axes("Q", "")]
    b2.append(h2.curve(TVC, 0, 30, lw=6.5))
    b2.append(h2.curve(TC, 0, 30, lw=6.5))
    b2.append(h2.text(25.4, TVC(25.4) - 110, "TVC", size=44, rot=-4))
    b2.append(h2.text(22.4, TC(22.4) + 105, "TC", size=44, rot=-4))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + _g(b1) + _g(b2, half) + "</svg>")


def fig_lac_lmc(w=980, h=680):
    """LAC 与 LMC（含短期的包络示意）"""
    LAC = lambda Q: 0.05 * Q ** 2 - 3 * Q + 85
    LMC = lambda Q: 0.15 * Q ** 2 - 6 * Q + 85
    p = H(w, h, 49, 0, 110, pl=95, pr=55, pt=40, pb=70, seed=83)
    body = [p.axes("Q", "")]
    for q0 in (18, 42):
        f = lambda Q, q0=q0: LAC(q0) + (0.1 * q0 - 3) * (Q - q0) + 0.10 * (Q - q0) ** 2
        body.append(p.curve(f, 8, 48, lw=4.5, amp=1.0, yclip=(0, 110)))
    body.append(p.curve(lambda Q: 40 + 0.10 * (Q - 30) ** 2, 8, 48, lw=4.5, amp=1.0, yclip=(0, 110)))
    body.append(p.curve(LAC, 6, 48, lw=7.5))
    body.append(p.curve(LMC, 6, 48, lw=7.5, yclip=(0, 110)))
    body.append(p.dot(30, 40, 9))
    body.append(p.line(30, 0, 30, 40, lw=3, amp=0.8, dash="14 12", n=14))
    body.append(p.text(29.2, -6.0, "Q*", size=44, anchor="end", rot=-3))
    body.append(p.text(46.8, LAC(46.8) + 15, "LAC", size=42, anchor="end", rot=-3))
    body.append(p.text(19.0, 11.0, "LMC", size=42, anchor="middle", rot=-4))
    return p.svg("".join(body))


def fig_min_iso(w=820, h=620):
    """固定比例（直角）等产量线"""
    p = H(w, h, 42, 0, 21, pl=95, pr=55, pt=40, pb=70, seed=91)
    body = [p.axes("L", "")]
    for L0, lab in [(20, "Q=20"), (10, "Q=10")]:
        K0 = L0 / 2
        body.append(p.line(L0, 0.3, L0, K0, lw=7.5))
        body.append(p.line(L0, K0, 40, K0, lw=7.5))
        body.append(p.text(41, K0 + 0.9, lab, size=42, anchor="end", rot=-3))
    body.append(p.dot(20, 10, 10))
    body.append(p.line(20, 0, 20, 10, lw=3, amp=0.8, dash="14 12", n=12))
    body.append(p.line(0, 10, 20, 10, lw=3, amp=0.8, dash="14 12", n=14))
    body.append(p.text(19.0, -1.6, "20", size=40, anchor="end", rot=-2))
    body.append(p.text(-1.0, 9.4, "10", size=40, anchor="end", rot=-2))
    return p.svg("".join(body))


# ---------------- 市场结构与均衡（微观·完全竞争 / 不完全竞争） ----------------
def _panel(w, h, xmax, ymin, ymax, dx=0, dy=0, seed=1, pl=72, pr=26, pt=28, pb=58):
    return H(w, h, xmax, ymin, ymax, pl=pl, pr=pr, pt=pt, pb=pb, seed=seed, ox=dx, oy=dy)


def _mini_panel(dx, dy, w, h, case, seed, ymax=34, xlab=""):
    """短期均衡小面板：AC / AVC / MC 与一条价格线。case = (P, Q)。"""
    P, Q = case
    p = H(w, h, 33, 0, ymax, pl=58, pr=90, pt=40, pb=64, seed=seed, ox=dx, oy=dy)
    b = [p.axes(xlab, "")]
    b.append(p.curve(AVC, 0.6, 32.6, lw=5.0, yclip=(0, ymax)))
    b.append(p.curve(AC, 1.4, 32.6, lw=5.0, yclip=(0, ymax)))
    b.append(p.curve(MC, 0.6, 32.6, lw=5.0, yclip=(0, ymax)))
    b.append(p.line(0, P, Q, P, lw=6.0, amp=0.7))
    b.append(p.dot(Q, P, 6))
    return p, b


def fig_pc_five(w=1150, h=780):
    """完全竞争企业短期均衡的 5 种情况（3 列 2 行，横向版式，避免被页底裁切）"""
    cw, ch = w // 3, h // 2
    cases = [(24.0, 21.26), (12.45, 17.05), (10.5, 16.06), (8.75, 15.0), (7.0, 13.65)]
    body = []
    for i, case in enumerate(cases):
        dx, dy = (i % 3) * cw, (i // 3) * ch
        p, b = _mini_panel(dx, dy, cw, ch, case, 101 + i, xlab=("Q" if i == 0 else ""))
        b.append(p.text(-1.6, case[0] + 1.0, "P", size=28, anchor="end", rot=-3))
        if i == 0:
            b.append(p.text(34.4, 28.6, "AC", size=26, anchor="start", rot=-3))
            b.append(p.text(34.4, 21.2, "AVC", size=26, anchor="start", rot=-3))
            b.append(p.text(5.4, 29.6, "MC", size=26, anchor="start", rot=-3))
        body.append(_g(b, dx, dy))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + "".join(body) + "</svg>")


def fig_pc_supply(w=1180, h=740):
    """左：企业短期供给（SMC 上 AVC 最低点以上）；右：市场短期供给"""
    half = w // 2
    lw, lh = half, h
    p1 = _panel(lw, lh, 33, 0, 32, seed=131)
    b1 = [p1.axes("Q", "")]
    b1.append(p1.curve(AVC, 0.6, 32.6, lw=5.5))
    b1.append(p1.curve(MC, 0.6, 15.0, lw=4.0, amp=1.0, dash="12 10"))
    b1.append(p1.curve(MC, 15.0, 32.6, lw=8.0, yclip=(0, 32)))
    b1.append(p1.line(0, 8.75, 32.4, 8.75, lw=3.2, amp=0.7, dash="14 12"))
    b1.append(p1.line(0, 24, 21.26, 24, lw=6.0, amp=0.7))
    b1.append(p1.dot(21.26, 24, 7))
    b1.append(p1.line(21.26, 0, 21.26, 24, lw=3, amp=0.8, dash="14 12"))
    b1.append(p1.text(32.4, 19.4, "AVC", size=36, anchor="end", rot=-3))
    b1.append(p1.text(25.4, 29.8, "MC", size=36, anchor="start", rot=-3))
    b1.append(p1.text(-1.4, 25.4, "P", size=36, anchor="end", rot=-3))

    p2 = _panel(half, lh, 33, 0, 32, dx=half, seed=137)
    S = lambda Q: 8.75 + 0.35 * (Q - 2) + 0.012 * (Q - 2) ** 2
    b2 = [p2.axes("Q", "")]
    b2.append(p2.curve(S, 2.0, 32.0, lw=8.0, yclip=(0, 32)))
    b2.append(p2.line(0, 8.75, 32.4, 8.75, lw=3.2, amp=0.7, dash="14 12"))
    b2.append(p2.text(27.6, S(27.6) - 3.4, "S", size=38, anchor="middle", rot=-6))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + _g(b1) + _g(b2, half) + "</svg>")


def fig_pc_longrun(w=980, h=740):
    """长期均衡：P = LMC = LAC 最低点"""
    LAC = lambda Q: 0.05 * Q ** 2 - 3 * Q + 85
    LMC = lambda Q: 0.15 * Q ** 2 - 6 * Q + 85
    p = H(w, h, 50, 0, 105, pl=88, pr=48, pt=36, pb=66, seed=149)
    body = [p.axes("Q", "")]
    body.append(p.curve(LAC, 8, 48, lw=6.5))
    body.append(p.curve(LMC, 8, 48, lw=6.5, yclip=(0, 105)))
    body.append(p.line(0, 40, 30, 40, lw=6.0, amp=0.7))
    body.append(p.dot(30, 40, 8))
    body.append(p.line(30, 0, 30, 40, lw=3, amp=0.8, dash="14 12"))
    body.append(p.text(46.6, LAC(46.6) + 4, "LAC", size=40, anchor="end", rot=-4))
    body.append(p.text(8.2, 50.0, "LMC", size=32, anchor="middle", rot=-4))
    body.append(p.text(-1.6, 42.0, "P", size=40, anchor="end", rot=-3))
    return p.svg("".join(body))


def fig_mono_tr_mr(w=1180, h=740):
    """左：d=AR 与 MR；右：倒 U 形的 TR"""
    half = w // 2
    p1 = _panel(half, h, 42, 0, 46, seed=163)
    b1 = [p1.axes("Q", "")]
    b1.append(p1.line(0, 40, 40, 0, lw=6.5))
    b1.append(p1.curve(lambda Q: 40 - 2 * Q, 0, 24, lw=6.5, yclip=(0, 46)))
    b1.append(p1.text(33.0, 12.0, "d=AR", size=38, anchor="start", rot=-4))
    b1.append(p1.text(21.6, 4.0, "MR", size=38, anchor="start", rot=-4))

    p2 = _panel(half, h, 42, 0, 460, dx=half, seed=167)
    TR = lambda Q: 40 * Q - Q * Q
    b2 = [p2.axes("Q", "")]
    b2.append(p2.curve(TR, 0, 40, lw=6.5, yclip=(0, 460)))
    b2.append(p2.line(20, 0, 20, 400, lw=3, amp=0.8, dash="14 12"))
    b2.append(p2.dot(20, 400, 8))
    b2.append(p2.text(11.5, 295.0, "TR", size=38, anchor="middle", rot=-4))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'xmlns="http://www.w3.org/2000/svg">' + _g(b1) + _g(b2, half) + "</svg>")


def fig_monodcomp_dd(w=900, h=720):
    """垄断竞争厂商的 d 与 D 两条需求曲线"""
    p = H(w, h, 40, 0, 46, pl=88, pr=52, pt=36, pb=66, seed=173)
    body = [p.axes("Q", "")]
    body.append(p.line(0, 30, 40, 8.7, lw=5.5))
    body.append(p.line(0, 44, 29.9, 0, lw=6.5))
    body.append(p.dot(15, 22, 8))
    body.append(p.line(15, 0, 15, 22, lw=3, amp=0.8, dash="14 12"))
    body.append(p.line(0, 22, 15, 22, lw=3, amp=0.8, dash="14 12"))
    body.append(p.text(35.4, 13.6, "d", size=40, anchor="middle", rot=-5))
    body.append(p.text(20.5, 4.5, "D", size=40, anchor="middle", rot=-3))
    return p.svg("".join(body))


# ---------------- 图库注册表 ----------------
# name -> (画法, 默认画布宽, 默认画布高, 说明)
REGISTRY = {
    "pc_five":      (fig_pc_five,      1150,  780, "完全竞争短期均衡的 5 种情况"),
    "pc_supply":    (fig_pc_supply,    1180,  740, "企业（SMC 段）与市场短期供给曲线"),
    "pc_longrun":   (fig_pc_longrun,    980,  740, "长期均衡：P=LMC=LAC 最低点"),
    "mono_tr_mr":   (fig_mono_tr_mr,   1180,  740, "垄断：d=AR 与 MR，及倒 U 形 TR"),
    "monodcomp_dd": (fig_monodcomp_dd,  900,  720, "垄断竞争：主观 d 与实际 D 相交"),
    "tp_mp":        (fig_tp_mp,        1180, 1216, "上 TP / 下 MP，标注拐点与最大点"),
    "ap_mp":        (fig_ap_mp,        1100, 1000, "MP 与 AP 同图，标出交点"),
    "isoquant":     (fig_isoquant,     1180, 1180, "三条等产量线（越远产量越高）"),
    "optcombo":     (fig_optcombo,     1100, 1000, "等成本线与等产量线相切于 E"),
    "totalcost":    (fig_totalcost,    1000,  610, "TFC / TVC / TC"),
    "avgcost":      (fig_avgcost,      1000,  700, "AFC / AVC / AC / MC"),
    "avc_ac_mc":    (fig_avc_ac_mc,    1050,  700, "AVC / AC / MC 及两处最低点"),
    "mirror_avg":   (fig_mirror_avg,   1200,  610, "左 MP/AP，右 AVC/MC（平均量镜像）"),
    "mirror_total": (fig_mirror_total, 1200,  610, "左 TP，右 TVC/TC（总量镜像）"),
    "lac_lmc":      (fig_lac_lmc,      1050,  700, "LAC 与 LMC，含 Q* 处相切"),
    "min_iso":      (fig_min_iso,       900,  690, "固定比例（直角）等产量线"),
}


def names():
    return sorted(REGISTRY)


def draw(name, w=None, h=None):
    """按名字画图，返回 (svg, labels)。"""
    if name not in REGISTRY:
        raise ValueError(f"没有这张图：{name!r}。可选：{', '.join(names())}")
    fn, dw, dh = REGISTRY[name][:3]
    reset()
    svg = fn(w or dw, h or dh)
    return svg, list(LABELS)
