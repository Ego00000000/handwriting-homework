# -*- coding: utf-8 -*-
"""无头 Edge：截图与打印 PDF。

旧流水线把 msedge.exe 路径硬编码在 5 个脚本里、且 check=False 静默失败，
这里统一成「显式参数 > 环境变量 > 三处常见路径 > 明确报错」，并且每次
截图都校验返回码、文件存在、实际像素尺寸。
"""
import os
import subprocess
import struct


def _candidates():
    local = os.environ.get("LOCALAPPDATA", "")
    return [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe") if local else "",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/microsoft-edge",
        "/usr/bin/google-chrome",
    ]


def find(explicit=None):
    """返回可用的浏览器可执行文件路径；找不到就抛错并列出尝试过的路径。"""
    for p in ([explicit] if explicit else []) + [os.environ.get("EDGE_PATH")] + _candidates():
        if p and os.path.isfile(p):
            return p
    tried = "\n  ".join(x for x in _candidates() if x)
    raise RuntimeError(
        "找不到 Edge/Chrome。请用 --edge 指定，或设置环境变量 EDGE_PATH。\n"
        f"已尝试：\n  {tried}"
    )


def _url(path):
    return "file:///" + os.path.abspath(path).replace("\\", "/")


def _run(cmd, timeout):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"浏览器超时（{timeout}s）：{' '.join(cmd[-2:])}")
    return p.returncode, (p.stderr or b"").decode("utf-8", "ignore")[-600:]


def read_size(path):
    """只读 IHDR 拿宽高，避免为了校验尺寸去解码整张图。"""
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"不是 PNG：{path}")
    w, h = struct.unpack(">II", head[16:24])
    return w, h


def shot(edge, html_path, out_png, w, h, scale=1.0, timeout=120, expect=None):
    """渲染 HTML 并截图，返回 (宽, 高)。"""
    if os.path.exists(out_png):
        os.remove(out_png)
    cmd = [edge, "--headless=new", "--disable-gpu", "--hide-scrollbars",
           "--disable-features=Translate", "--force-device-scale-factor=%s" % scale,
           f"--window-size={w},{h}", f"--screenshot={out_png}", _url(html_path)]
    rc, err = _run(cmd, timeout)
    if rc != 0:
        raise RuntimeError(f"截图失败（rc={rc}）：{err}")
    if not os.path.exists(out_png):
        raise RuntimeError(f"截图没有产出文件：{out_png}\n{err}")
    aw, ah = read_size(out_png)
    ew, eh = expect or (int(w * scale), int(h * scale))
    if abs(aw - ew) > 2 or abs(ah - eh) > 2:
        raise RuntimeError(f"截图尺寸异常：期望 {ew}x{eh}，实际 {aw}x{ah}")
    return aw, ah


def pdf(edge, html_path, out_pdf, timeout=180):
    if os.path.exists(out_pdf):
        os.remove(out_pdf)
    cmd = [edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
           f"--print-to-pdf={out_pdf}", _url(html_path)]
    rc, err = _run(cmd, timeout)
    if rc != 0 or not os.path.exists(out_pdf) or os.path.getsize(out_pdf) < 1024:
        raise RuntimeError(f"导出 PDF 失败（rc={rc}）：{err}")
    return os.path.getsize(out_pdf)
