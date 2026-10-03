"""Acesso a mídia via FFmpeg/ffprobe (pipes, sem arquivos temporários)."""
import json
import re
import subprocess
from fractions import Fraction

import numpy as np


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw)


def ffprobe(path):
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)]).stdout
    return json.loads(out)


def parse_rate(s):
    if not s or s in ("0/0", "N/A"):
        return None
    return Fraction(s)


def read_audio(path, sr=22050, channels=1):
    """Decodifica o áudio inteiro como float32 [-1, 1]. Retorna (amostras, sr)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn",
                        "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, check=True)
    x = np.frombuffer(p.stdout, np.float32)
    if channels > 1:
        x = x.reshape(-1, channels)
    return x, sr


def iter_frames(path, width, height, fps=None, pix_fmt="rgb24"):
    """Itera frames decodificados e redimensionados numa grade CFR.

    fps (Fraction/str) força grade constante — fontes VFR (celular) viram CFR
    na mesma taxa que o After Effects vai interpretar.
    """
    vf = []
    if fps is not None:
        vf.append(f"fps={fps}")
    vf.append(f"scale={width}:{height}:flags=area")
    ch = {"rgb24": 3, "gray": 1}[pix_fmt]
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", ",".join(vf),
           "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    size = width * height * ch
    try:
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            a = np.frombuffer(buf, np.uint8)
            yield a.reshape(height, width, ch) if ch > 1 else a.reshape(height, width)
    finally:
        p.stdout.close()
        p.wait()


def loudness(path):
    """EBU R128 via filtro ebur128: LUFS integrado, LRA, true peak."""
    p = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    txt = p.stderr
    tail = txt[txt.rfind("Summary:"):] if "Summary:" in txt else ""
    res = {}
    for key, pat in (("integrated_lufs", r"I:\s+(-?[\d.]+|-inf) LUFS"),
                     ("lra_lu", r"LRA:\s+(-?[\d.]+) LU"),
                     ("true_peak_dbtp", r"Peak:\s+(-?[\d.]+|-inf) dBFS")):
        m = re.search(pat, tail)
        if m:
            v = m.group(1)
            res[key] = None if v == "-inf" else float(v)
    return res


def scaled_size(w, h, target_w):
    tw = int(target_w) - int(target_w) % 2
    th = int(round(h * tw / w))
    th -= th % 2
    return tw, max(th, 2)
