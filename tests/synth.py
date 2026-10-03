"""Gera um clipe sintético com gabarito (ground truth) conhecido.

Não é material do usuário: serve para medir cada detector do pipeline contra
respostas certas (cortes, direção de câmera, flash que NÃO é corte, BPM, kicks,
snares, riser, drop, silêncio, cor subexposta).

Uso: python -m tests.synth [saida.mp4]
"""
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

W, H, FPS, DUR = 1280, 720, 30, 16.0
SR = 48000
N_FRAMES = int(DUR * FPS)

# (inicio, fim_exclusivo, tint_rgb, velocidade do CONTEÚDO em px/frame (dx, dy), descrição)
SHOTS = [
    (0, 90, (1.00, 0.62, 0.30), (0, 0), "estatico quente, sujeito andando para a direita"),
    (90, 212, (0.35, 0.55, 1.00), (-14, 0), "pan para a direita (conteudo vai para a esquerda)"),
    (212, 270, (0.40, 1.00, 0.45), (0, 18), "tilt para cima (conteudo desce)"),
    (270, 390, (1.00, 0.35, 0.90), (26, 0), "pan rapido para a esquerda (conteudo vai para a direita)"),
    (390, 480, (0.45, 0.55, 0.85), (0, 0), "estatico escuro, dominante azul (subexposto)"),
]
FLASH_FRAMES = (330, 331)  # mudança de iluminação, NÃO é corte


def _texture(rng, w, h, tint):
    base = rng.random((h // 8 + 1, w // 8 + 1)).astype(np.float32)
    base = cv2.resize(base, (w, h), interpolation=cv2.INTER_CUBIC)
    base = cv2.GaussianBlur(base, (0, 0), 3)
    img = np.clip(0.25 + 0.5 * base, 0, 1)
    img = np.dstack([img * t for t in tint])
    # formas de alto contraste = features para tracking/fluxo
    for _ in range(int(w * h / 9000)):
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        s = int(rng.integers(6, 28))
        c = [float(v) for v in (rng.random(3) * 0.6 + 0.4 * np.array(tint))]
        if rng.random() < 0.5:
            cv2.rectangle(img, (x, y), (x + s, y + s), c, -1)
        else:
            cv2.circle(img, (x, y), s // 2, c, -1)
    return img


def _subject(img, cx, cy, r, color, ring=None):
    """Sujeito com textura própria (pontos que andam junto) — rastreável como um sujeito real."""
    cv2.circle(img, (cx, cy), r, color, -1)
    if ring is not None:
        cv2.circle(img, (cx, cy), r, ring, 6)
    dark = tuple(float(c) * 0.35 for c in color)
    for (ox, oy) in ((-30, -25), (25, -30), (-10, 20), (30, 15), (0, -5), (-35, 10)):
        cv2.rectangle(img, (cx + ox - 5, cy + oy - 5), (cx + ox + 5, cy + oy + 5), dark, -1)


def make_frames():
    rng = np.random.default_rng(7)
    canvases = []
    for (a, b, tint, (vx, vy), _) in SHOTS:
        n = b - a
        cw = W + abs(vx) * n + 64
        ch = H + abs(vy) * n + 64
        canvases.append(_texture(rng, cw, ch, tint))
    for f in range(N_FRAMES):
        si = next(i for i, s in enumerate(SHOTS) if s[0] <= f < s[1])
        a, b, tint, (vx, vy), _ = SHOTS[si]
        k = f - a
        cv_ = canvases[si]
        n = b - a
        # janela de câmera: conteúdo se move (vx,vy) => janela anda (-vx,-vy)
        x0 = 32 + (abs(vx) * n if vx > 0 else 0) - vx * k
        y0 = 32 + (abs(vy) * n if vy > 0 else 0) - vy * k
        img = cv_[y0:y0 + H, x0:x0 + W].copy()
        if si == 0:   # sujeito andando para a direita
            _subject(img, 300 + 3 * k, 400, 70, (0.97, 0.95, 0.9), ring=(0.1, 0.1, 0.1))
        if si == 1:   # sujeito seguido pela câmera (quase parado na tela)
            _subject(img, 620 + k, 380, 80, (1.0, 0.92, 0.35))
        if si == 4:   # cena escura com dominante azul
            img = img * np.array([0.38, 0.42, 0.62], np.float32)
            _subject(img, 860, 360, 90, (0.30, 0.33, 0.45))
        if f in FLASH_FRAMES:
            img = np.clip(img * 0.25 + 0.85, 0, 1)
        yield (np.clip(img, 0, 1) * 255).astype(np.uint8)


def _env(n, decay_s):
    t = np.arange(n) / SR
    return np.exp(-t / decay_s)


def make_audio():
    rng = np.random.default_rng(11)
    n = int(DUR * SR)
    out = np.zeros(n, np.float64)

    def add(t0, sig):
        i = int(round(t0 * SR))
        j = min(n, i + len(sig))
        out[i:j] += sig[: j - i]

    def kick(amp=0.8):
        m = int(0.25 * SR)
        t = np.arange(m) / SR
        f = 45 + 105 * np.exp(-t / 0.03)
        ph = 2 * np.pi * np.cumsum(f) / SR
        return amp * np.sin(ph) * _env(m, 0.07)

    def noise_burst(m_s, decay, lo, hi, amp):
        m = int(m_s * SR)
        x = rng.standard_normal(m)
        X = np.fft.rfft(x)
        fr = np.fft.rfftfreq(m, 1 / SR)
        X[(fr < lo) | (fr > hi)] = 0
        x = np.fft.irfft(X, m)
        x /= np.max(np.abs(x)) + 1e-9
        return amp * x * _env(m, decay)

    def snare():
        m = int(0.2 * SR)
        t = np.arange(m) / SR
        body = 0.35 * np.sin(2 * np.pi * 190 * t) * _env(m, 0.04)
        return body + noise_burst(0.2, 0.05, 1200, 7000, 0.55)

    # pad 0–3 s
    t = np.arange(int(3.0 * SR)) / SR
    pad = sum(np.sin(2 * np.pi * f * t) for f in (220, 277.2, 329.6)) * 0.03
    pad *= np.minimum(1, np.minimum(t / 0.5, (3.0 - t) / 0.3))
    add(0.0, pad)
    # hats em colcheias até 13 s
    for i in range(int(13.0 / 0.25)):
        tt = i * 0.25
        add(tt, noise_burst(0.04, 0.012, 6000, 16000, 0.08 if tt < 3 else 0.12))
    kicks = [3.0 + 0.5 * i for i in range(20)]
    for tt in kicks:
        add(tt, kick())
    snares = [9.5, 10.5, 11.5, 12.5]
    for tt in snares:
        add(tt, snare())
    # riser 7–9 s
    m = int(2.0 * SR)
    t = np.arange(m) / SR
    sweep = np.sin(2 * np.pi * np.cumsum(300 * (10 ** (t / 2.0))) / SR)
    riser = (0.18 * sweep + noise_burst(2.0, 1e9, 2000, 12000, 0.12)) * (t / 2.0) ** 2
    add(7.0, riser)
    # sub bass pós-drop 9–13 s, por batida
    for i in range(8):
        m = int(0.45 * SR)
        t = np.arange(m) / SR
        add(9.0 + 0.5 * i, 0.28 * np.sin(2 * np.pi * 55 * t) * np.minimum(1, (0.45 - t) / 0.05))
    # impacto final 14 s
    add(14.0, kick(1.0))
    add(14.0, noise_burst(1.5, 0.35, 40, 4000, 0.45))
    m = int(1.6 * SR)
    t = np.arange(m) / SR
    add(14.0, 0.35 * np.sin(2 * np.pi * 38 * t) * _env(m, 0.5))
    out += rng.standard_normal(n) * 1e-4   # piso de ruído ~ -80 dBFS
    out = np.clip(out, -1, 1)
    stereo = np.stack([out, out * 0.97], axis=1).astype(np.float32)
    truth = {
        "bpm": 120.0,
        "kicks": kicks + [14.0],
        "snares": snares,
        "drop": 9.0,
        "riser": [7.0, 9.0],
        "silence": [13.0, 14.0],
        "impact_final": 14.0,
    }
    return stereo, truth


def main(out_path):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    audio, atruth = make_audio()
    wav = out_path.with_suffix(".f32")
    audio.tofile(wav)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(wav),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path),
    ]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fr in make_frames():
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg falhou")
    wav.unlink()
    truth = {
        "fps": FPS, "width": W, "height": H, "frames": N_FRAMES,
        "cuts": [s[0] for s in SHOTS[1:]],
        "non_cut_flash": FLASH_FRAMES[0],
        "subjects": {"0": {"start": [300, 400], "velocity": [3, 0]},
                     "1": {"start": [620, 380], "velocity": [1, 0]},
                     "4": {"start": [860, 360], "velocity": [0, 0]}},
        "shots": [{"start": a, "end": b, "content_velocity": list(v), "desc": d}
                  for (a, b, _, v, d) in SHOTS],
        "audio": atruth,
    }
    out_path.with_suffix(".truth.json").write_text(json.dumps(truth, indent=2))
    print(f"ok: {out_path} + {out_path.with_suffix('.truth.json')}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tests/fixtures/synthetic.mp4")
