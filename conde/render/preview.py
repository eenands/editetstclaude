"""FASE 63 — render de PREVIEW da timeline (proxy do After Effects, não substitui o render do AE).

Segundo interpretador do mesmo timeline.json: mesmo time remap e mesmas curvas Bezier
(conde.decision.curves), cadeia de câmera calculada com os espelhos Python das
expressions (validados contra as expressions reais no harness). Aproximações
declaradas: Pixel Motion → mistura de frames; Turbulent Displace/Optics/Glow/
Brightness&Contrast/Vibrance → equivalentes OpenCV; fonte → DejaVu Sans Bold;
blur de texto omitido.
"""
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..decision.curves import Key, evaluate
from ..decision.timemap import TimeMap
from ..generate import expressions as X
from ..io_ffmpeg import iter_frames, scaled_size

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


class Timeline:
    def __init__(self, tl):
        self.tl = tl
        self.fps = tl["comp"]["fps"]
        self.W, self.H = tl["comp"]["width"], tl["comp"]["height"]
        self.n = tl["comp"]["duration_frames"]
        self.L = {L["name"]: L for L in tl["layers"]}
        self._kc = {}
        ctl = self.L["CTRL_MASTER"]
        self.ctrl = {e["name"]: e["params"]["ADBE Slider Control-0001"] for e in ctl["effects"]}

    def keys(self, layer, prop):
        k = (layer, prop)
        if k not in self._kc:
            L = self.L.get(layer)
            ks = L["keys"].get(prop) if L else None
            self._kc[k] = [Key.from_dict(x) for x in ks] if ks else None
        return self._kc[k]

    def val(self, layer, prop, f, default=None):
        ks = self.keys(layer, prop)
        if ks:
            return evaluate(ks, f, self.fps)
        L = self.L.get(layer)
        if L and prop.startswith("effect:"):
            _, en, pr = prop.split(":", 2)
            for e in L["effects"]:
                if e["name"] == en and pr in e["params"]:
                    return e["params"][pr]
        if L and prop.startswith("transform."):
            tr = L.get("transform", {})
            k2 = prop.split(".")[1]
            if k2 in tr:
                return tr[k2]
        return default

    def baked(self, layer, prop, f):
        b = self.L[layer]["baked"].get(prop)
        if not b:
            return 0.0
        i = int(min(len(b["values"]) - 1, max(0, round(f - b["start"]))))
        return b["values"][i]

    def k(self, *names):
        v = self.ctrl.get("GLOBAL_INTENSITY", 1.0)
        for n in names:
            v *= self.ctrl.get(n, 1.0)
        return v

    def active(self, prefix, f):
        return [L for n, L in self.L.items() if n.startswith(prefix) and L["in"] <= f < L["out"]]


# ------------------------------------------------------------------ geometria
def T(x, y):
    return np.array([[1, 0, x], [0, 1, y], [0, 0, 1]], float)


def R(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)


def Sc(s):
    return np.array([[s, 0, 0], [0, s, 0], [0, 0, 1]], float)


def camera(tlx, f):
    """Cadeia CAMERA_POSITION · ROTATION · ZOOM · SHAKE (espaço da comp). Retorna (M_full, M_rot)."""
    fps = tlx.fps
    cx, cy = tlx.W / 2, tlx.H / 2
    px = tlx.val("CAMERA_POSITION", "transform.x", f, 0.0) or 0.0
    py = tlx.val("CAMERA_POSITION", "transform.y", f, 0.0) or 0.0
    M_pos = T(px * tlx.k("TRANSITION_INTENSITY"), py * tlx.k("TRANSITION_INTENSITY"))
    rot = tlx.val("CAMERA_ROTATION", "transform.rotation", f, 0.0) or 0.0
    M_rot = M_pos @ T(cx, cy) @ R(rot) @ T(-cx, -cy)
    punch = tlx.val("CAMERA_ZOOM", "transform.scale", f, [100.0, 100.0])
    punch = punch[0] if isinstance(punch, (list, tuple)) else punch
    s = X.zoom_eval(punch, tlx.k("ZOOM_INTENSITY"),
                    tlx.val("CTRL_CAMERA", "effect:DRIFT_SCALE:ADBE Slider Control-0001", f, 100.0),
                    tlx.val("CTRL_CAMERA", "effect:CUT_ZOOM:ADBE Slider Control-0001", f, 100.0),
                    tlx.baked("CTRL_AUDIO", "effect:BASS:ADBE Slider Control-0001", f),
                    tlx.val("CTRL_AUDIO", "effect:GATE_BASS_TO_ZOOM:ADBE Slider Control-0001", f, 0.0),
                    tlx.k("AUDIO_REACT_INTENSITY"))
    foc = tlx.val("CAMERA_FOCUS", "transform.position", f, [cx, cy])
    Fp = np.linalg.inv(M_rot) @ np.array([foc[0], foc[1], 1.0])
    M_zoom = M_rot @ T(Fp[0], Fp[1]) @ Sc(s / 100.0) @ T(-Fp[0], -Fp[1])
    if not hasattr(tlx, "_marks"):
        tlx._marks = [(m["frame"] / fps, m["comment"]) for m in tlx.L["MARKERS_IMPACT"]["markers"]]
    amb = tlx.val("CTRL_CAMERA", "effect:AMBIENT_SHAKE:ADBE Slider Control-0001", f, 0.0)
    ox, oy, rr = X.shake_eval(tlx._marks, f / fps, amb, tlx.k("SHAKE_INTENSITY"))
    M_full = M_zoom @ T(cx + ox, cy + oy) @ R(rr) @ T(-cx, -cy)
    return M_full, M_rot


# ------------------------------------------------------------------ leitor de fonte com cache
class SourceReader:
    def __init__(self, path, w, h, fps_str, keep=240):
        self.it = iter_frames(path, w, h, fps=fps_str)
        self.cache, self.next_idx, self.keep, self.last = {}, 0, keep, None

    def get(self, i):
        while self.next_idx <= i:
            try:
                fr = next(self.it)
            except StopIteration:
                break
            self.cache[self.next_idx] = fr
            self.last = fr
            self.next_idx += 1
            old = self.next_idx - self.keep
            self.cache.pop(old, None)
        return self.cache.get(i, self.last)


# ------------------------------------------------------------------ efeitos (equivalentes OpenCV)
def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def lin_to_srgb(x):
    x = np.clip(x, 0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def dir_blur(img, length, screen_angle):
    L = int(max(1, round(length)))
    if L < 2:
        return img
    k = np.zeros((L | 1, L | 1), np.float32)
    c = (L | 1) // 2
    dx, dy = np.cos(np.radians(screen_angle)) * c, np.sin(np.radians(screen_angle)) * c
    cv2.line(k, (int(round(c - dx)), int(round(c - dy))), (int(round(c + dx)), int(round(c + dy))), 1.0, 1)
    return cv2.filter2D(img, -1, k / k.sum(), borderType=cv2.BORDER_REFLECT)


_NOISE = {}


def turbulent(img, amount_px, seed):
    h, w = img.shape[:2]
    if (w, h, seed) not in _NOISE:
        rng = np.random.default_rng(seed)
        nx = cv2.resize(rng.standard_normal((h // 24 + 2, w // 24 + 2)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        ny = cv2.resize(rng.standard_normal((h // 24 + 2, w // 24 + 2)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)
        _NOISE[(w, h, seed)] = (nx, ny)
    nx, ny = _NOISE[(w, h, seed)]
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    a = np.float32(amount_px)   # NumPy 2: escalar float64 promoveria os mapas para float64
    return cv2.remap(img, (gx + nx * a).astype(np.float32), (gy + ny * a).astype(np.float32), cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REFLECT)


def lens(img, fov):
    if fov < 0.5:
        return img
    h, w = img.shape[:2]
    k = fov / 100.0 * 0.6
    gx, gy = np.meshgrid(np.linspace(-1, 1, w, dtype=np.float32), np.linspace(-1, 1, h, dtype=np.float32))
    r2 = gx * gx + gy * gy
    f = 1 / (1 + k * r2)
    return cv2.remap(img, ((gx * f + 1) * (w - 1) / 2).astype(np.float32), ((gy * f + 1) * (h - 1) / 2).astype(np.float32),
                     cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def glitch(img, amount, noise_amt, f):
    if amount <= 0.5:
        return img
    rng = np.random.default_rng(1000 + f)
    out = img.copy()
    h, w = img.shape[:2]
    for _ in range(8):
        y0 = int(rng.integers(0, h - 4))
        bh = int(rng.integers(4, max(5, h // 10)))
        sh = int(rng.normal(0, amount))
        out[y0:y0 + bh] = np.roll(img[y0:y0 + bh], sh, axis=1)
    if noise_amt > 0:
        out = np.clip(out + rng.normal(0, noise_amt / 100.0, out.shape).astype(np.float32), 0, 1)
    return out


def glow(img, threshold, radius, intensity):
    if intensity <= 0.01:
        return img
    Y = img @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    m = np.clip((Y - threshold) / (1 - threshold + 1e-6), 0, 1)[..., None]
    b = cv2.GaussianBlur(img * m, (0, 0), max(1.0, radius))
    return np.clip(img + b * intensity * 0.6, 0, 1)


def vignette_mask(w, h, inset, feather):
    gx, gy = np.meshgrid(np.linspace(-1, 1, w, dtype=np.float32), np.linspace(-1, 1, h, dtype=np.float32))
    r = np.sqrt(gx ** 2 + gy ** 2) / (1 - 2 * inset)
    soft = feather / (w / 2)
    return np.clip((r - (1 - soft)) / (2 * soft), 0, 1)


# ------------------------------------------------------------------ gráficos e texto
def _trimmed(points, start, end):
    pts = np.array(points, float)
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    tot = seg.sum()
    if tot <= 0:
        return []
    a, b = tot * start / 100, tot * end / 100
    out, acc = [], 0.0
    for i, L in enumerate(seg):
        p0, p1 = pts[i], pts[i + 1]
        s0, s1 = max(a, acc), min(b, acc + L)
        if s1 > s0:
            out.append((p0 + (p1 - p0) * (s0 - acc) / L, p0 + (p1 - p0) * (s1 - acc) / L))
        acc += L
    return out


def draw_shape_layer(tlx, L, f, ov, M, sp):
    """Desenha camada de shape em overlay RGBA float (espaço da comp → preview via M)."""
    pos = L.get("transform", {}).get("position", [0.0, 0.0])
    track_expr = L["expressions"].get("transform.position")
    if track_expr and "TRACK_" in track_expr:
        tn = track_expr.split('thisComp.layer("')[1].split('"')[0]
        pos = tlx.baked(tn, "transform.position", f) if tlx.L[tn]["in"] <= f < tlx.L[tn]["out"] else pos
    op = (tlx.val(L["name"], "transform.opacity", f, 100.0) or 0) / 100.0
    sc = tlx.val(L["name"], "transform.scale", f, [100.0, 100.0])
    s_l = (sc[0] if isinstance(sc, (list, tuple)) else sc) / 100.0
    Mp = Sc(sp) @ M @ T(pos[0], pos[1]) @ Sc(s_l)
    shape = L["shape"]
    for g in shape["groups"]:
        items = {it["type"]: it for it in g["items"]}
        if shape.get("particles"):
            ph = g["physics"]
            t = max(0.0, (f - L["in"]) / tlx.fps)
            e = (1 - np.exp(-ph["drag"] * t)) / ph["drag"]
            p = np.array([ph["vx"] * e, ph["vy"] * e + 0.5 * ph["g"] * t * t, 1.0])
            a = 100 * max(0.0, 1 - t / ph["life"]) * tlx.k("PARTICLE_INTENSITY") / 100.0
            r = items["ellipse"]["size"][0] / 2 * max(0.05, 1 - t / ph["life"])
            q = Mp @ p
            cv2.circle(ov, (int(q[0]), int(q[1])), max(1, int(r * sp)), (*items["fill"]["color"], a * op), -1, cv2.LINE_AA)
            continue
        gname = g["name"]
        if "ellipse" in items and "stroke" in items:
            size = tlx.val(L["name"], f"shape:{gname}:ellipse.size", f, items["ellipse"]["size"])
            wdt = tlx.val(L["name"], f"shape:{gname}:stroke.width", f, items["stroke"]["width"])
            q = Mp @ np.array([0, 0, 1.0])
            if wdt * sp >= 0.5:
                cv2.ellipse(ov, (int(q[0]), int(q[1])), (max(1, int(size[0] / 2 * sp * s_l)), max(1, int(size[1] / 2 * sp * s_l))),
                            0, 0, 360, (*items["stroke"]["color"], op), max(1, int(wdt * sp)), cv2.LINE_AA)
        if "rect" in items and "fill" in items:
            rs, ctr = items["rect"]["size"], items["rect"].get("center", [0, 0])
            p0 = Mp @ np.array([ctr[0] - rs[0] / 2, ctr[1] - rs[1] / 2, 1.0])
            p1 = Mp @ np.array([ctr[0] + rs[0] / 2, ctr[1] + rs[1] / 2, 1.0])
            cv2.rectangle(ov, (int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1])), (*items["fill"]["color"], op), -1)
        if "path" in items and "stroke" in items:
            ts = tlx.val(L["name"], f"shape:{gname}:trim.start", f, 0.0) if "trim" in items else 0.0
            te = tlx.val(L["name"], f"shape:{gname}:trim.end", f, 100.0) if "trim" in items else 100.0
            for a_, b_ in _trimmed(items["path"]["points"], ts, te):
                pa, pb = Mp @ np.array([*a_, 1.0]), Mp @ np.array([*b_, 1.0])
                cv2.line(ov, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (*items["stroke"]["color"], op),
                         max(1, int(items["stroke"]["width"] * sp)), cv2.LINE_AA)


_FONTS = {}


def font(size):
    size = max(6, int(round(size)))
    if size not in _FONTS:
        _FONTS[size] = ImageFont.truetype(FONT, size)
    return _FONTS[size]


def _sel_amount(i, n, pct_start, pct_end):
    a, b = i / n * 100, (i + 1) / n * 100
    lo, hi = max(a, pct_start), min(b, pct_end)
    return max(0.0, hi - lo) / (b - a)


def draw_text_layer(tlx, L, f, sp, M_rot, canvas):
    Ts = L["text"]
    fps = tlx.fps
    k_in = [Key(Ts["in_frames"][0], 0.0, out_infl=0.1), Key(Ts["in_frames"][1], 100.0, in_infl=0.85)]
    k_out = [Key(Ts["out_frames"][0], 0.0, out_infl=0.6), Key(Ts["out_frames"][1], 100.0, in_infl=0.2)]
    st_in = evaluate(k_in, f, fps)
    en_out = evaluate(k_out, f, fps)
    text = Ts["text"]
    units = text.split(" ") if Ts["based_on"] == "words" else list(text)
    n = len(units)
    size = Ts["size"] * sp
    sc = tlx.val(L["name"], "transform.scale", f, [100.0, 100.0])
    s_l = (sc[0] if isinstance(sc, (list, tuple)) else sc) / 100.0
    fnt = font(size * s_l)
    adv = []
    for u in units:
        w_ = fnt.getlength(u + (" " if Ts["based_on"] == "words" else "")) + Ts["tracking"] / 1000 * size * s_l
        adv.append(w_)
    total = sum(adv)
    pos = L["transform"]["position"]
    q = Sc(sp) @ M_rot @ np.array([pos[0], pos[1], 1.0])
    x0 = q[0] - {"LEFT": 0, "RIGHT": total, "CENTER": total / 2}[Ts["justify"]]
    y0 = q[1]
    an = Ts["animator"]
    kt = tlx.k("TEXT_INTENSITY")
    col = tuple(int(c * 255) for c in Ts["color"])
    layer_img = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(layer_img), ImageDraw.Draw(shadow)
    x = x0
    for i, u in enumerate(units):
        a_in = _sel_amount(i, n, st_in, 100.0)       # selecionado pelo ANIM_IN = ainda escondido
        a_out = _sel_amount(i, n, 0.0, en_out)       # selecionado pelo ANIM_OUT = já saiu
        amt = max(a_in, a_out)
        alpha = (1 - amt) + amt * an["opacity"] / 100.0
        dy = amt * an["position"][1] * sp * kt
        us = 1 + amt * (an["scale"] / 100.0 - 1)
        fu = font(size * s_l * us) if abs(us - 1) > 0.02 else fnt
        if alpha > 0.01:
            a8 = int(255 * alpha)
            off = Ts["shadow"]["distance"] * sp
            ds.text((x + off, y0 + dy + off), u, font=fu, fill=(0, 0, 0, int(a8 * Ts["shadow"]["opacity"] / 100)), anchor="ls")
            d.text((x, y0 + dy), u, font=fu, fill=(*col, a8), anchor="ls")
        x += adv[i]
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(1, Ts["shadow"]["softness"] * sp / 2)))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(layer_img)


# ------------------------------------------------------------------ render principal
def render(tl_json, source_path, out_path, width=960, debug=False, log=print):
    tlx = Timeline(tl_json)
    fps, n = tlx.fps, tlx.n
    pw, ph = scaled_size(tlx.W, tlx.H, width)
    sp = pw / tlx.W
    pre = tl_json["precomps"]["PRE_SOURCE_REMAP"]
    src = tl_json["source"]
    tm = TimeMap(fps, src["frames"], [Key.from_dict(k) for k in pre["timeremap"]], src_fps=src["fps"])
    reader = SourceReader(source_path, pw, ph, f"{src['fps']}")
    out_path = Path(out_path)
    tmp_video = out_path.with_suffix(".video.mp4")
    enc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{pw}x{ph}",
                            "-r", f"{fps}", "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", str(tmp_video)], stdin=subprocess.PIPE)
    vig = None
    metrics = []
    corr = [L for L in tlx.L.values() if L["name"].startswith("COLOR_CORR_")]
    t0 = time.time()
    events = tl_json["events"]
    for f in range(n):
        M, M_rot = camera(tlx, f)
        # amostragem temporal: Force Motion Blur nas rampas + motion blur de camada nas transformações
        shutter = tlx.val("VIDEO", "effect:FORCE_MB:3", f, 0.0) if tlx.keys("VIDEO", "effect:FORCE_MB:3") else 0.0
        subs = [0.0]
        Mn, _ = camera(tlx, f + 0.25)
        corner = np.array([[0, 0, 1], [tlx.W, tlx.H, 1], [tlx.W, 0, 1]], float).T
        moving = np.abs((Mn - M) @ corner).max() * sp > 1.5
        if (shutter or 0) > 20 or moving:
            span = max(0.5 if moving else 0.0, (shutter or 0) / 360.0)
            subs = list(np.linspace(-span / 2, span / 2, 4))
        acc = np.zeros((ph, pw, 3), np.float32)
        for sub in subs:
            st = tm.src_time(f + sub) * src["fps"]
            i0 = int(np.floor(st + 1e-6))
            fr = reader.get(min(src["frames"] - 1, i0)).astype(np.float32) / 255.0
            frac = st - i0
            if pre.get("frame_blending") and frac > 0.05 and tm.speed(f) < 0.999:   # proxy do Pixel Motion
                fr2 = reader.get(min(src["frames"] - 1, i0 + 1)).astype(np.float32) / 255.0
                fr = fr * (1 - frac) + fr2 * frac
            Mf = M if sub == 0.0 else camera(tlx, f + sub)[0]
            A = (Sc(sp) @ Mf @ Sc(1 / sp))[:2]
            acc += cv2.warpAffine(fr, A, (pw, ph), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        img = (acc / len(subs)).astype(np.float32)
        rgb_px = tlx.val("CTRL_VFX", "effect:RGB_SPLIT_PX:ADBE Slider Control-0001", f, 0.0) * tlx.k("VFX_INTENSITY")
        if tlx.active("VFX_RGB_SPLIT_", f) and abs(rgb_px) > 0.2:
            s_ = int(round(rgb_px * sp))
            img = np.dstack([np.roll(img[..., 0], -s_, axis=1), img[..., 1], np.roll(img[..., 2], s_, axis=1)])
        bl = tlx.val("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0002", f, 0.0) * tlx.k("VFX_INTENSITY")
        if bl > 1:
            ang = tlx.val("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0001", f, 90.0) - 90.0
            img = dir_blur(img, bl * sp, ang)
        for L in tlx.active("VFX_DISTORT_", f):
            if any(e["name"] == "LENS" for e in L["effects"]):
                img = lens(img, tlx.val(L["name"], "effect:LENS:1", f, 0.0) * tlx.k("VFX_INTENSITY", "TRANSITION_INTENSITY"))
            else:
                img = turbulent(img, tlx.val(L["name"], "effect:DISPLACE:2", f, 0.0) * tlx.k("VFX_INTENSITY") * 0.35 * sp, L["in"])
        for L in tlx.active("VFX_GLITCH_", f):
            img = glitch(img, tlx.val(L["name"], "effect:DISPLACE:2", f, 0.0) * tlx.k("VFX_INTENSITY") * sp,
                         tlx.val(L["name"], "effect:NOISE:1", f, 0.0), f)
        for L in corr:
            if L["in"] <= f < L["out"]:
                p = next(e for e in L["effects"] if e["name"] == "EXPOSURE")["params"]
                g = np.array([2 ** p["5"], 2 ** p["8"], 2 ** p["11"]], np.float32)
                img = lin_to_srgb(srgb_to_lin(img) * g).astype(np.float32)
        ci = tlx.ctrl.get("COLOR_INTENSITY", 1.0)
        look = {e["name"]: e["params"] for e in tlx.L["COLOR_LOOK"]["effects"]}
        c = look["CONTRAST"]["2"] / 100.0
        styl = 0.5 + (img - 0.5) * (1 + 0.8 * c)
        hsv = cv2.cvtColor(np.clip(styl, 0, 1).astype(np.float32), cv2.COLOR_RGB2HSV)
        vib = look["VIBRANCE"]["1"] / 100.0
        hsv[..., 1] = np.clip(hsv[..., 1] * (1 + vib * (1 - hsv[..., 1])), 0, 1)
        styl = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        img = img * (1 - ci) + styl * ci
        V = tlx.L["COLOR_VIGNETTE"]
        if vig is None:
            mk = V["masks"][0]
            vig = vignette_mask(pw, ph, mk["inset"], mk["feather"] * sp)[..., None]
        img = img * (1 - vig * V["transform"]["opacity"] / 100.0 * ci)
        gk = tlx.val("VFX_GLOW", "effect:GLOW:4", f, 0.0)
        tr = tlx.baked("CTRL_AUDIO", "effect:TREBLE:ADBE Slider Control-0001", f) * \
            tlx.val("CTRL_AUDIO", "effect:GATE_TREBLE_TO_GLOW:ADBE Slider Control-0001", f, 0.0) * tlx.k("AUDIO_REACT_INTENSITY")
        gp = {e["name"]: e["params"] for e in tlx.L["VFX_GLOW"]["effects"]}["GLOW"]
        img = glow(img, gp["2"] / 100.0, gp["3"] * sp, (gk + tr * 0.6) * tlx.k("GLOW_INTENSITY"))
        op = (tlx.val("VFX_FLASH", "transform.opacity", f, 0.0) or 0.0) * tlx.k("VFX_INTENSITY") / 100.0
        if op > 0.002:
            tint = tlx.val("VFX_FLASH", "effect:TINT:2", f, [1, 1, 1])
            img = img + np.array(tint[:3], np.float32) * op
        img = np.clip(img, 0, 1).astype(np.float32)
        # overlays: partículas/brackets/anéis (pai CAMERA_SHAKE) e linhas/placas (pai CAMERA_ROTATION)
        shp = [L for L in tlx.L.values() if L["kind"] == "shape" and L["in"] <= f < L["out"]]
        if shp:
            ov_add = np.zeros((ph, pw, 4), np.float32)
            ov_norm = np.zeros((ph, pw, 4), np.float32)
            for L in shp:
                Mpar = M if L.get("parent") == "CAMERA_SHAKE" else M_rot
                draw_shape_layer(tlx, L, f, ov_add if L["blend"] == "ADD" else ov_norm, Mpar, sp)
            img = np.clip(img + ov_add[..., :3] * ov_add[..., 3:4], 0, 1)
            a = np.clip(ov_norm[..., 3:4], 0, 1)
            img = img * (1 - a) + ov_norm[..., :3] * a
        txt = tlx.active("TEXT_", f)
        frame8 = (img * 255).astype(np.uint8)
        if txt or debug:
            canvas = Image.fromarray(frame8).convert("RGBA")
            for L in txt:
                draw_text_layer(tlx, L, f, sp, M_rot, canvas)
            if debug:
                act = [e for e in events if e["start_frame"] <= f < e["end_frame"] and e["category"] not in ("COLOR", "AUDIO_SYNC", "CAMERA", "TRACKING")]
                d = ImageDraw.Draw(canvas)
                lab = f"f{f:05d} {f / fps:6.2f}s  E={tl_json['energy_per_frame'][f]:.2f}  " + \
                      " ".join(f"{e['id']}:{e.get('recipe', e['category'])}" for e in act[:3])
                d.rectangle([0, ph - 18, pw, ph], fill=(0, 0, 0, 170))
                d.text((6, ph - 15), lab, font=font(12), fill=(255, 255, 255, 255))
            frame8 = np.array(canvas.convert("RGB"))
        Y = frame8.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], np.float32) / 255.0
        metrics.append({"frame": f, "luma": float(Y.mean()), "thumb": cv2.resize(frame8, (pw // 4, ph // 4), interpolation=cv2.INTER_AREA)})
        enc.stdin.write(frame8.tobytes())
    enc.stdin.close()
    enc.wait()
    elapsed = time.time() - t0
    # áudio: música = original contínuo; diálogo = mesmos cortes do vídeo
    au = tl_json["audio"]["timeremap"]
    if au:
        segs, ks = [], [Key.from_dict(k) for k in au]
        for a_, b_ in zip(ks, ks[1:]):
            if a_.interp_out != "HOLD":
                segs.append((a_.v, b_.v + 1 / fps))
        filt = "+".join(f"between(t,{a:.4f},{b:.4f})" for a, b in segs)
        af = ["-af", f"aselect='{filt}',asetpts=N/SR/TB"]
    else:
        af = []
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(tmp_video), "-i", str(source_path), "-map", "0:v",
                    "-map", "1:a?", *af, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(out_path)], check=True)
    tmp_video.unlink()
    for m in metrics:
        m["diff"] = 0.0
    for a_, b_ in zip(metrics, metrics[1:]):
        b_["diff"] = float(np.mean(np.abs(b_["thumb"].astype(np.int16) - a_["thumb"].astype(np.int16))) / 255.0)
    log(f"[63] preview {pw}x{ph} @ {fps:g} fps: {n} frames em {elapsed:.1f}s ({n / max(elapsed, 1e-6):.1f} fps) → {out_path.name}")
    return {"path": str(out_path), "size": [pw, ph], "frames": n, "seconds": round(elapsed, 2), "metrics": metrics}
