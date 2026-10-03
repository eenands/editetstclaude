"""Modelo de keyframes do After Effects (FASE 14) — fonte única para AE e preview.

Segmento Bezier 1D entre k0 e k1 (dt = t1 - t0), como no Graph Editor do AE:
  P0 = (t0, v0)
  P1 = (t0 + out_infl0*dt, v0 + out_speed0*out_infl0*dt)
  P2 = (t1 - in_infl1*dt,  v1 - in_speed1*in_infl1*dt)
  P3 = (t1, v1)
speed em unidades/segundo; influência em fração (o JSX converte para %).
Com influência 1/3 e velocidades iguais às da rampa nos extremos, a cúbica
reproduz EXATAMENTE uma rampa linear de velocidade (elevação de grau da quadrática).
"""
from dataclasses import asdict, dataclass

import numpy as np

THIRD = 1.0 / 3.0


@dataclass
class Key:
    f: float                 # frame da comp (pode ser fracionário só em casos especiais)
    v: object                # float ou lista (escala [s, s])
    in_speed: float = 0.0
    out_speed: float = 0.0
    in_infl: float = THIRD
    out_infl: float = THIRD
    interp_in: str = "BEZIER"    # BEZIER | LINEAR | HOLD
    interp_out: str = "BEZIER"

    def to_dict(self):
        d = asdict(self)
        d["v"] = [round(float(x), 7) for x in self.v] if isinstance(self.v, (list, tuple)) else round(float(self.v), 7)
        for k in ("in_speed", "out_speed", "f"):
            d[k] = round(float(d[k]), 6)
        for k in ("in_infl", "out_infl"):
            d[k] = round(float(d[k]), 9)
        return d

    @staticmethod
    def from_dict(d):
        return Key(**d)


def _seg_eval(k0, k1, t, fps, dim=None):
    t0, t1 = k0.f / fps, k1.f / fps
    v0 = k0.v[dim] if dim is not None else k0.v
    v1 = k1.v[dim] if dim is not None else k1.v
    dt = t1 - t0
    if k0.interp_out == "HOLD":
        return v0
    if k0.interp_out == "LINEAR" and k1.interp_in == "LINEAR":
        return v0 + (v1 - v0) * (t - t0) / dt
    so = (v1 - v0) / dt if k0.interp_out == "LINEAR" else k0.out_speed
    si = (v1 - v0) / dt if k1.interp_in == "LINEAR" else k1.in_speed
    oi = THIRD if k0.interp_out == "LINEAR" else k0.out_infl
    ii = THIRD if k1.interp_in == "LINEAR" else k1.in_infl
    x = np.array([t0, t0 + oi * dt, t1 - ii * dt, t1])
    y = np.array([v0, v0 + so * oi * dt, v1 - si * ii * dt, v1])
    lo, hi = 0.0, 1.0
    for _ in range(40):     # x(u) é monótona quando oi + ii <= 1
        u = 0.5 * (lo + hi)
        xu = ((1 - u) ** 3) * x[0] + 3 * ((1 - u) ** 2) * u * x[1] + 3 * (1 - u) * u * u * x[2] + u ** 3 * x[3]
        if xu < t:
            lo = u
        else:
            hi = u
    u = 0.5 * (lo + hi)
    return ((1 - u) ** 3) * y[0] + 3 * ((1 - u) ** 2) * u * y[1] + 3 * (1 - u) * u * u * y[2] + u ** 3 * y[3]


def evaluate(keys, frame, fps):
    """Valor da propriedade no frame (float) — mesma semântica do AE (antes do 1º key: valor do 1º)."""
    if not keys:
        return None
    t = frame / fps
    if t <= keys[0].f / fps:
        return keys[0].v
    if t >= keys[-1].f / fps:
        return keys[-1].v
    i = int(np.searchsorted([k.f for k in keys], frame, side="right")) - 1
    k0, k1 = keys[i], keys[i + 1]
    if isinstance(k0.v, (list, tuple)):
        return [_seg_eval(k0, k1, t, fps, d) for d in range(len(k0.v))]
    return _seg_eval(k0, k1, t, fps)


def validate_keys(keys):
    """Erros que o AE rejeitaria ou que quebrariam a curva."""
    errs = []
    for a, b in zip(keys, keys[1:]):
        if b.f <= a.f:
            errs.append(f"keys fora de ordem/duplicados em f={b.f}")
        if a.interp_out == "BEZIER" and b.interp_in == "BEZIER" and a.out_infl + b.in_infl > 1.0 + 1e-9:
            errs.append(f"influências somam >100% entre f={a.f} e f={b.f} (curva não monótona no tempo)")
    for k in keys:
        for infl in (k.in_infl, k.out_infl):
            if not (0.001 <= infl <= 1.0):
                errs.append(f"influência fora de [0.1%,100%] em f={k.f}")
    return errs


# ---------------- presets de easing (FASE 14: nada linear por padrão) ----------------

def punch(f0, rest, peak, attack, release, fps, settle_ratio=0.12):
    """Scale punch: rest -> peak (fast-out) -> settle (overshoot de retorno) -> rest (ease-in exponencial)."""
    d = peak - rest
    a_s = attack / fps
    settle = rest + settle_ratio * d
    f1, f2, f3 = f0 + attack, f0 + attack + max(2, int(round(release * 0.45))), f0 + attack + release
    return [
        Key(f0, rest, out_speed=2.2 * d / a_s, out_infl=0.15, in_speed=0.0),
        Key(f1, peak, in_speed=0.0, in_infl=0.55, out_speed=0.0, out_infl=0.35),
        Key(f2, settle, in_speed=-0.8 * d / ((f2 - f1) / fps), in_infl=0.3, out_speed=-0.25 * d / ((f3 - f2) / fps), out_infl=0.3),
        Key(f3, rest, in_speed=0.0, in_infl=0.6),
    ]


def pulse(f0, peak, attack, decay, fps, rest=0.0):
    """Flash/blur/distorção: sobe rápido, decai exponencialmente."""
    f1, f2 = f0 + attack, f0 + attack + decay
    d = peak - rest
    return [
        Key(f0, rest, out_speed=d / (attack / fps), out_infl=THIRD, interp_out="LINEAR" if attack <= 1 else "BEZIER"),
        Key(f1, peak, in_speed=d / (attack / fps) * 0.5, in_infl=0.2, out_speed=-2.5 * d / (decay / fps), out_infl=0.2,
            interp_in="LINEAR" if attack <= 1 else "BEZIER"),
        Key(f2, rest, in_speed=0.0, in_infl=0.7),
    ]


def hold_steps(frames_values):
    """Degraus (glitch, zoom-cut): interpolação HOLD."""
    return [Key(f, v, interp_in="HOLD", interp_out="HOLD") for f, v in frames_values]
