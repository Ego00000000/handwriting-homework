# -*- coding: utf-8 -*-
"""handwrite.sixiangjia.de 客户端：提交任务、轮询、取图、查字体。

接口要点（实测）：
  · font_option 必须传「带 .ttf 的完整文件名」，纯字符串精确匹配；
  · width / height 必须同时传，否则后端要求自带 background_image；
  · preview=true 返回 JSON+base64，preview=false 返回 zip；
  · 队列满返回 503 queue_full 且带 estimated_wait_seconds，应据此退避。
"""
import base64
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from uuid import uuid4

BASE = "https://handwrite.sixiangjia.de"

# 站点其实有 5 种字体，这里只开放两种，并统一叫「一号 / 二号」
FONTS = [
    dict(alias="一号字体", ttf="青叶手写体.ttf", note="笔画粗、墨色最实"),
    dict(alias="二号字体", ttf="云烟体.ttf", note="笔画细、略向右斜"),
]

DEFAULT_FONT = "一号字体"

_ALIAS = {
    "一号字体": "一号字体", "一号": "一号字体", "1号字体": "一号字体", "1号": "一号字体",
    "①": "一号字体", "①号": "一号字体",
    "二号字体": "二号字体", "二号": "二号字体", "2号字体": "二号字体", "2号": "二号字体",
    "②": "二号字体", "②号": "二号字体",
}
for _f in FONTS:                     # 也认真实文件名（含/不含 .ttf），兼容老 job
    _ALIAS[_f["ttf"]] = _f["alias"]
    _ALIAS[_f["ttf"][:-4]] = _f["alias"]


def list_fonts():
    """可用的两种字体（按一号 / 二号顺序）。"""
    return [dict(f) for f in FONTS]


def font_names():
    return "、".join(f'{f["alias"]}（{f["ttf"][:-4]}）' for f in FONTS)


def resolve_font(name):
    """把用户写法解析成 FONTS 里的一项；非法就抛错并列出可选项。"""
    key = (name or "").strip()
    alias = _ALIAS.get(key) or _ALIAS.get(key.replace(" ", ""))
    if not alias:
        raise ValueError(f"字体只能是 {font_names()}，收到：{name!r}")
    for f in FONTS:
        if f["alias"] == alias:
            return dict(f)
    raise ValueError(f"字体只能是 {font_names()}，收到：{name!r}")


def server_fonts(timeout=10):
    """站点实际提供的 ttf 列表（只用来在 doctor 里核对，不参与校验）。"""
    for attempt in (1, 2):
        try:
            st, raw = _request(f"{BASE}/api/fonts_info", timeout=timeout * attempt)
            if st == 200:
                data = _json(raw)
                if isinstance(data, list) and data:
                    return [x for x in data if isinstance(x, str)]
        except Exception:
            if attempt == 1:
                time.sleep(1)
    return []


def _multipart(fields):
    boundary = "----hw" + uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode()
    body += f"--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def _request(url, data=None, timeout=180, method=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "Mozilla/5.0 (compatible; handwriting-homework/1.0)")
    if data is not None:
        body, ctype = _multipart(data)
        req.data = body
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _retry(fn, tries=4, wait=3, what=""):
    last = None
    for i in range(tries):
        try:
            return fn()
        except _Retryable as e:
            last = e
            time.sleep(max(e.wait, wait * (i + 1)))
        except Exception as e:          # 网络抖动等也重试
            last = e
            time.sleep(wait * (i + 1))
    raise RuntimeError(f"{what or '请求'}失败（已重试 {tries} 次）：{last}") from last


class _Retryable(Exception):
    def __init__(self, msg, wait=0):
        super().__init__(msg)
        self.wait = wait


def probe(timeout=8):
    """连通性探测。返回 (ok, detail)。"""
    try:
        st, raw = _request(f"{BASE}/api/version", timeout=timeout)
        if st == 200:
            return True, _json(raw).get("version", "?")
        return False, f"HTTP {st}"
    except Exception as e:
        return False, str(e)


def render(text, font="青叶手写体.ttf", font_size=106, line_spacing=176,
           width=2481, height=3507, margins=(150, 150, 110, 110),
           params=None, timeout=300, verbose=False):
    """渲染一页手写文字，返回 PNG 字节列表。"""
    top, bottom, left, right = margins
    f = {
        "text": text,
        "font_option": font,
        "font_size": str(font_size),
        "line_spacing": str(line_spacing),
        "fill": "(0, 0, 0, 255)",
        "width": str(width),
        "height": str(height),
        "top_margin": str(top),
        "bottom_margin": str(bottom),
        "left_margin": str(left),
        "right_margin": str(right),
        "line_spacing_sigma": "0",      # 行距必须稳，后面要按行基准线对齐
        "font_size_sigma": "2",
        "word_spacing_sigma": "2",
        "perturb_x_sigma": "3",
        "perturb_y_sigma": "3",
        "perturb_theta_sigma": "0.04",
        "word_spacing": "1",
        "end_chars": "",
        "preview": "true",
        "strikethrough_length_sigma": "2",
        "strikethrough_angle_sigma": "2",
        "strikethrough_width_sigma": "2",
        "strikethrough_probability": "0",   # 否则会随机冒出「划掉的错字」
        "strikethrough_width": "8",
        "ink_depth_sigma": "8",      # 30 会让个别字整字发灰（逐字随机墨深），8 保留浓淡又不跳
        "pdf_save": "false",
        "isUnderlined": "true",
        "enableEnglishSpacing": "true",
        "full_preview": "false",
    }
    if params:
        f.update(params)

    def submit():
        st, raw = _request(f"{BASE}/api/generate_handwriting", data=f)
        js = _json(raw)
        if st == 200 and js.get("task_id"):
            return js["task_id"]
        if js.get("status") == "queue_full":
            raise _Retryable("队列已满", wait=js.get("estimated_wait_seconds", 60))
        if st == 429:
            raise _Retryable("服务端 CPU 过载")
        raise RuntimeError(f"提交失败 HTTP {st}：{raw[:200]!r}")

    tid = _retry(submit, what="提交手写任务")
    if verbose:
        print(f"    task {tid}", flush=True)

    waited = 0
    while waited < timeout:
        time.sleep(2)
        waited += 2
        st, raw = _request(f"{BASE}/api/generate_handwriting/task/{tid}", timeout=60)
        js = _json(raw)
        status = js.get("task_status")
        if status == "completed":
            break
        if status == "failed":
            raise RuntimeError(f"渲染失败：{js.get('error_message')}")
        if st >= 500:
            continue
    else:
        raise TimeoutError(f"渲染超时（{timeout}s），task={tid}")

    st, raw = _request(f"{BASE}/api/generate_handwriting/task/{tid}/result", timeout=180)
    if st != 200:
        raise RuntimeError(f"取图失败 HTTP {st}：{raw[:200]!r}")
    if raw[:1] == b"{":
        data = _json(raw)
        return [base64.b64decode(x) for x in data.get("images", [])]
    if raw[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(raw))
        names = sorted(n for n in z.namelist() if n.lower().endswith((".png", ".jpg", ".jpeg")))
        return [z.read(n) for n in names]
    return [raw]
