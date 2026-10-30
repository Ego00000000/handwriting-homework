# -*- coding: utf-8 -*-
"""中文折行：按显示宽度断行，标点不放行首、行末不留守尾符、避免孤字成行。

服务端会按自己的排版规则再折一次，所以这里的预算要略保守；
最终行数以渲染回来的墨迹行为准（拆块阶段会实测校验）。
"""

HEAD_FORBIDDEN = "，。；：、）】》”’！？%"
TAIL_FORBIDDEN = "=：（【《“‘"
PUNCT = HEAD_FORBIDDEN + ",.;:"


def char_width(ch, cjk=1.0, ascii_=0.62):
    return cjk if ord(ch) > 0x2000 else ascii_


def line_width(s, cjk=1.0, ascii_=0.62):
    return sum(char_width(c, cjk, ascii_) for c in s)


def wrap(text, budget=17.0, cjk=1.0, ascii_=0.62, mode="hard"):
    """返回折行后的文本（保留原有换行）。"""
    if mode == "none":
        return text
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        cur, w = "", 0.0
        for ch in para:
            cw = char_width(ch, cjk, ascii_)
            if w + cw > budget:
                if ch in HEAD_FORBIDDEN:          # 标点不落行首：挤在本行末尾
                    out.append(cur + ch)
                    cur, w = "", 0.0
                    continue
                cut = len(cur)
                for k in range(len(cur) - 1, max(-1, len(cur) - 6), -1):
                    if cur[k] in PUNCT:           # 就近在标点后断开
                        cut = k + 1
                        break
                else:
                    k = len(cur)                  # 不切断西文单词
                    while k > 0 and cur[k - 1].isascii() and cur[k - 1].isalnum():
                        k -= 1
                    if 0 < k < len(cur):
                        cut = k
                while cut > 1 and cur[cut - 1] in TAIL_FORBIDDEN:
                    cut -= 1
                out.append(cur[:cut])
                cur = cur[cut:]
                w = line_width(cur, cjk, ascii_)
            cur += ch
            w += cw
        if cur:
            out.append(cur)

    # 孤字修正：末行太短就从上一行借两个字过来
    for i in range(len(out) - 1, 0, -1):
        if 0 < line_width(out[i], cjk, ascii_) < 3 and len(out[i - 1]) > 4:
            out[i] = out[i - 1][-2:] + out[i]
            out[i - 1] = out[i - 1][:-2]
    return "\n".join(out)


def count_lines(text):
    return text.count("\n") + 1 if text.strip() else 0


def wrap_fit(text, budget=17.0, max_lines=None, cjk=1.0, ascii_=0.62):
    """在预算内尽量少行：行数超限时逐档放宽 budget（最多 6 档）。"""
    b = budget
    for _ in range(6):
        out = wrap(text, b, cjk, ascii_)
        if max_lines is None or count_lines(out) <= max_lines:
            return out, b
        b *= 0.94
    return out, b
