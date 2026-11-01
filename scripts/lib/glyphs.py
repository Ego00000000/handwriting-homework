# -*- coding: utf-8 -*-
"""字形支持检测与降级替换。

手写接口遇到字体没有的字符会**直接跳过**（不画方块、不报错）：
x² 变 x、(P→Q)∧(Q→R) 变 (P Q) (Q R)，下游拆块/排版/校验完全看不出来。
所以渲染前必须先知道「这个字体能不能写这个字」，再决定替换还是报错。

探测方法：把候选字符一行一个排成几页，逐行统计墨迹像素。
没有字形的那一行是空的（服务端连空白都不留），据此判定。
结果按字体缓存到 skill 根目录的 glyphs.json，只需探一次。
"""
import json
import os
import time

import api
import imgfast
import pngtool

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # skill 根目录
CACHE = os.path.join(HERE, "glyphs.json")
MAPFILE = os.path.join(HERE, "glyph_map.json")

PROBE_FS, PROBE_LS, PROBE_TOP = 72, 112, 90
PROBE_PER_PAGE = 32
INK_MIN = 6                     # 一行里暗像素少于这个数，就认为没有字形

# 候选：上下标、带圈数字、数学/逻辑/箭头/希腊字母/温度单位/全角符号/标点/括号
# 注意：不在这张表里、又不是汉字/ASCII 的字符，一律按「未知」报错，绝不默认放行
CANDIDATES = (
    "²³¹⁰⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ"
    "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"
    "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳⑴⑵⑶⑷⑸"
    "×÷≈≤≥≠±√∞∫∬∭∑∏∂∇"
    "∠△▽⊥∥∟⌒⊙∈∉∪∩⊂⊃⊆⊇∀∃¬∧∨⊕⊗∅"
    "→←↑↓↔⇒⇔⇐⇑⇓"
    "∵∴"
    "‰℃°′″‱§¶†‡•·…—–～　、。〃々〇"
    "〈〉《》「」『』【】〔〕〖〗〘〙"
    "！＂＃＄％＆＇（）＊＋，－．／：；＜＝＞？＠［＼］＾＿｀｛｜｝"
    "ΩΔΠΣΦΨΛΘΓΞαβγδεζηθικλμνξπρστυφχψω"
    "ᵃᵇᵏᵐᵖˣ"
    "Az1.%,;()"
)

_USED = {}

# 两种字体互查：一号（青叶）几乎不支持数学符号，二号（云烟）支持得多的多
_OTHER = {"青叶手写体.ttf": "云烟体.ttf", "云烟体.ttf": "青叶手写体.ttf"}


def _other_hint(font_ttf, chars):
    other = _OTHER.get(font_ttf)
    if not other or not chars:
        return ""
    info = (_load_cache().get("fonts") or {}).get(other)
    if not info:
        return ""
    ok = [c for c in dict.fromkeys(chars) if c in (info.get("supported") or [])]
    if not ok:
        return ""
    return (f"\n  提示：{other[:-4]}（另一种字体）能写 {' '.join(ok)}；"
            f"数学/物理类作业建议直接用 --font 二号字体。")


def _map():
    try:
        with open(MAPFILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"fonts": {}}


def _save_cache(data):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def probe(font_ttf, verbose=True):
    """逐字探测某字体支持哪些字符。返回 {字符: 是否支持}。"""
    res = {}
    chars = list(dict.fromkeys(CANDIDATES))
    for s in range(0, len(chars), PROBE_PER_PAGE):
        chunk = chars[s:s + PROBE_PER_PAGE]
        height = PROBE_TOP + len(chunk) * PROBE_LS + 120
        imgs = api.render("\n".join(chunk), font=font_ttf, font_size=PROBE_FS,
                          line_spacing=PROBE_LS, width=2481, height=height,
                          margins=(PROBE_TOP, 120, 150, 150),
                          params={"isUnderlined": "false"})
        if not imgs:
            raise RuntimeError("字形探测：接口没有返回图片")
        w, h, grey = pngtool.decode_grey(imgs[0])
        m = imgfast.mask(grey, imgfast.DARK)
        for i, ch in enumerate(chunk):
            y0 = PROBE_TOP + i * PROBE_LS
            y1 = min(h, y0 + PROBE_LS)
            ink = m[y0 * w:y1 * w].count(1) if y0 < h else 0
            res[ch] = ink >= INK_MIN
        if verbose:
            print(f"    字形探测 {min(len(chars), s + PROBE_PER_PAGE)}/{len(chars)}…", flush=True)
    return res


def ensure(font_ttf, verbose=True, refresh=False):
    """取某字体的字形表（有缓存就用缓存）。失败返回 None（调用方降级为不替换）。"""
    data = _load_cache()
    if not refresh:
        info = (data.get("fonts") or {}).get(font_ttf)
        if info and info.get("unsupported") is not None:
            return info
    if verbose:
        print(f"    首次使用 {font_ttf}，探测字形支持（约 3 次接口调用）…", flush=True)
    try:
        res = probe(font_ttf, verbose=verbose)
    except Exception as ex:
        print(f"    ! 字形探测失败（{ex}）：本次不做替换，符号可能被吞掉", flush=True)
        return None
    if not all(res.get(c) for c in "A1%"):
        print("    ! 字形探测结果异常（基线字符 A/1/% 未识别），本次不做替换", flush=True)
        return None
    info = dict(font=font_ttf, checked=time.strftime("%Y-%m-%d %H:%M"),
                unsupported=[c for c, ok in res.items() if not ok],
                supported=[c for c, ok in res.items() if ok])
    data.setdefault("fonts", {})[font_ttf] = info
    _save_cache(data)
    if verbose:
        print(f"    字形：{len(info['supported'])} 个可用，"
              f"{len(info['unsupported'])} 个缺字形", flush=True)
    return info


def unsupported_of(font_ttf, verbose=True):
    info = ensure(font_ttf, verbose=verbose)
    return set(info.get("unsupported") or []) if info else set()


def _assume_ok(ch):
    """ASCII 与汉字默认能写（字体本身就是中文字体），其余一律要靠探测结果。"""
    o = ord(ch)
    return (o < 0x80
            or 0x3400 <= o <= 0x4DBF      # 扩展 A
            or 0x4E00 <= o <= 0x9FFF      # 基本汉字
            or 0xF900 <= o <= 0xFAFF)     # 兼容汉字


def sanitize(text, font_ttf, strict=False, allow_drop=False, tag="", verbose=True):
    """把字体写不出来的字符换成可写的写法；换不了就报错。

    判据：ASCII 与汉字放行；其余字符必须出现在该字体的 supported 表里，
    没探测过的（新符号、生僻符号）按「未知」报错 —— 宁可报错，也不能让它悄悄消失。

    返回 (新文本, 用到的替换 {字符: 次数}, 未能处理的字符列表)。
    """
    info = ensure(font_ttf, verbose=verbose)
    if info is None:
        return text, {}, []
    ok = set(info.get("supported") or [])
    if not ok:
        return text, {}, []
    cfg = _map()
    rep = {} if strict else (cfg.get("replace") or {})
    block = cfg.get("block") or {}

    out, used, missed = [], {}, []
    for ch in text:
        if _assume_ok(ch) or ch in ok:
            out.append(ch)
        elif ch in rep:
            out.append(rep[ch])
            used[ch] = used.get(ch, 0) + 1
            _USED[ch] = _USED.get(ch, 0) + 1
        else:
            missed.append(ch)

    if missed and not allow_drop:
        uniq = list(dict.fromkeys(missed))
        detail = "、".join(f"{c}({block.get(c, '无推荐写法')})" for c in uniq)
        raise ValueError(
            f"{tag}含 {len(missed)} 处手写字体写不出来的字符：{detail}。\n"
            f"  这些字会被服务端直接跳过（不是报错），公式会变成空白。\n"
            f"  处理办法：改写这几处，或把它们加进 "
            f"{os.path.basename(MAPFILE)} 的 replace 里；\n"
            f"  确认可以丢弃时用 --allow-drop，或加 --glyph-strict 让所有缺字形字符都报错。"
            + _other_hint(font_ttf, uniq))
    return "".join(out), used, missed


def summary():
    """本次运行自动替换的汇总，给 build 结束打一行。"""
    if not _USED:
        return ""
    items = "、".join(f"{c} {n} 处" for c, n in
                     sorted(_USED.items(), key=lambda kv: -kv[1]))
    return f"自动替换 {sum(_USED.values())} 处（{items}）"


def reset():
    _USED.clear()


def fmt_table(font_ttf, verbose=True, refresh=False):
    """给 hw.py glyphcheck 打印用。"""
    return ensure(font_ttf, verbose=verbose, refresh=refresh)
