"""FASES 10–12 e 29 — mapa de tempo saída → fonte (time remapping).

Música: o áudio NÃO é remapeado; toda operação de vídeo é NEUTRA na janela
(saída == fonte nas bordas, velocidade 1) para manter os cortes no beat.
Diálogo: cortes de silêncio (jump cuts) remapeiam vídeo e áudio juntos.
"""
import numpy as np

from .curves import THIRD, Key, evaluate


class Profile:
    """Perfil de velocidade linear por partes: breakpoints (frame_saida, velocidade)."""

    def __init__(self, pts):
        self.pts = pts

    def integral(self, fps):
        tot = 0.0
        for (fa, va), (fb, vb) in zip(self.pts, self.pts[1:]):
            tot += (fb - fa) / fps * (va + vb) / 2
        return tot

    def integral_to(self, frame, fps):
        tot = 0.0
        for (fa, va), (fb, vb) in zip(self.pts, self.pts[1:]):
            if frame <= fa:
                break
            fe = min(frame, fb)
            ve = va + (vb - va) * (fe - fa) / (fb - fa)
            tot += (fe - fa) / fps * (va + ve) / 2
        return tot


def ramp_with_hit(a, b, tau, m_src_s, fps, s_slow, hold, r, vmax=6.0, vmin=0.3):
    """Rampa neutra em [a,b] (frames) que exibe o instante-fonte m_src no frame-saída tau.

    perfil: 1 ->v1 (r) | v1 | v1->s_slow (r) | s_slow (hold) | s_slow->v2 (r) | v2 | v2->1 (r)
    v1 resolve f(tau) = m_src; v2 resolve f(b) = b (neutralidade).
    Retorna (Profile, v1, v2) ou None se inviável.
    """
    p = [a, a + r, tau - r, tau, tau + hold, tau + hold + r, b - r, b]
    if any(y < x for x, y in zip(p, p[1:])) or tau - r < a + r or b - r < tau + hold + r:
        return None

    def prof(v1, v2):
        return Profile([(p[0], 1.0), (p[1], v1), (p[2], v1), (p[3], s_slow), (p[4], s_slow),
                        (p[5], v2), (p[6], v2), (p[7], 1.0)])

    # integral até tau é afim em v1
    i0 = prof(0.0, 1.0).integral_to(tau, fps)
    i1 = prof(1.0, 1.0).integral_to(tau, fps)
    target = m_src_s - a / fps
    if abs(i1 - i0) < 1e-9:
        return None
    v1 = (target - i0) / (i1 - i0)
    j0 = prof(v1, 0.0).integral(fps)
    j1 = prof(v1, 1.0).integral(fps)
    v2 = ((b - a) / fps - j0) / (j1 - j0)
    if not (vmin <= v1 <= vmax and vmin <= v2 <= vmax):
        return None
    return prof(v1, v2), float(v1), float(v2)


def profile_keys(profile, src_offset_s, fps, base_key_before=None):
    """Keys de Time Remap: em cada breakpoint, in/out speed = velocidade do perfil, influência 1/3
    (rampa linear de velocidade exata). Valores em segundos da fonte."""
    keys = []
    for f, v in profile.pts:
        val = src_offset_s + profile.integral_to(f, fps)
        keys.append(Key(f, val, in_speed=v, out_speed=v, in_infl=THIRD, out_infl=THIRD))
    return keys


class TimeMap:
    """Mapa saída→fonte (segundos) como lista de Keys de Time Remap do AE."""

    def __init__(self, fps, n_src, keys=None, src_fps=None):
        self.fps = fps                      # fps da comp (frames de saída)
        self.src_fps = src_fps or fps       # fps da fonte (amostragem do time remap)
        self.n_src = n_src
        dur = n_src / self.src_fps
        self.keys = keys or [Key(0, 0.0, in_speed=1, out_speed=1, interp_in="LINEAR", interp_out="LINEAR"),
                             Key(n_src * fps / self.src_fps, dur, in_speed=1, out_speed=1, interp_in="LINEAR", interp_out="LINEAR")]
        self.windows = []

    def src_time(self, out_frame):
        return float(evaluate(self.keys, out_frame, self.fps))

    def src_frame(self, out_frame):
        """Frame da fonte exibido no frame de saída (amostragem do AE: floor com tolerância)."""
        return min(self.n_src - 1, int(np.floor(self.src_time(out_frame) * self.src_fps + 1e-6)))

    def speed(self, out_frame, h=0.25):
        return (self.src_time(out_frame + h) - self.src_time(out_frame - h)) * self.fps / (2 * h)

    def free(self, a, b):
        return all(b <= w["start_frame"] or a >= w["end_frame"] for w in self.windows)

    def insert_window(self, a, b, new_keys, info):
        """Substitui o trecho [a,b] (identidade) por keys novos. Exige janela livre."""
        assert self.free(a, b)
        inner = [k for k in self.keys if not (a <= k.f <= b)]
        self.keys = sorted(inner + new_keys, key=lambda k: k.f)
        # keys vizinhas lineares continuam válidas: fora das janelas a velocidade é 1
        info.update(start_frame=a, end_frame=b)
        self.windows.append(info)
        self.windows.sort(key=lambda w: w["start_frame"])

    def check(self, tol_frames=0.02):
        """Monotonicidade, neutralidade nas bordas e sem estouro da fonte."""
        errs = []
        prev = -1e9
        n_out = int(round(self.keys[-1].f))
        for f in np.arange(0, n_out, 0.5):
            s = self.src_time(f)
            if s < prev - 1e-9:
                errs.append(f"mapa não monótono em f={f}")
                break
            prev = s
        if self.src_time(n_out) > self.n_src / self.src_fps + 1e-6:
            errs.append("time remap pede frames além do fim da fonte")
        for w in self.windows:
            if w.get("neutral", True):
                for edge in (w["start_frame"], w["end_frame"]):
                    if abs(self.src_time(edge) * self.fps - edge) > tol_frames:
                        errs.append(f"janela {w['id']} não é neutra na borda f={edge}")
        return errs


def trim_map(fps, n_src, keep_segments):
    """Diálogo: segmentos mantidos [(src_a, src_b) em frames] colados em sequência (jump cuts).
    Keys LINEAR (velocidade 1) com HOLD no último frame de cada trecho → salto no corte."""
    keys, out = [], 0
    for a, b in keep_segments:
        keys.append(Key(out, a / fps, in_speed=1, out_speed=1, interp_in="LINEAR", interp_out="LINEAR"))
        last = out + (b - a) - 1
        keys.append(Key(last, (b - 1) / fps, in_speed=1, out_speed=1, interp_in="LINEAR", interp_out="HOLD"))
        out += b - a
    keys.append(Key(out, keep_segments[-1][1] / fps, interp_in="HOLD", interp_out="HOLD"))
    tm = TimeMap(fps, n_src, keys)
    return tm, out
