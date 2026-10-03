"""Modelo de keyframes e time remap: exatidão, neutralidade, monotonicidade."""
import numpy as np

from conde.decision.curves import evaluate, punch, pulse, validate_keys
from conde.decision.timemap import TimeMap, profile_keys, ramp_with_hit, trim_map

FPS = 30.0


def test_bezier_keys_reproduce_linear_speed_ramp_exactly():
    res = ramp_with_hit(a=100, b=190, tau=150, m_src_s=155 / FPS, fps=FPS, s_slow=0.5, hold=8, r=6)
    assert res is not None
    prof, v1, v2 = res
    keys = profile_keys(prof, 100 / FPS, FPS)
    for f in np.arange(100, 190, 0.37):
        analytic = 100 / FPS + prof.integral_to(f, FPS)
        assert abs(evaluate(keys, f, FPS) - analytic) < 1e-6
    # o instante-fonte pedido aparece no frame-saída pedido; bordas neutras
    assert abs(evaluate(keys, 150, FPS) - 155 / FPS) < 1e-9
    assert abs(evaluate(keys, 190, FPS) - 190 / FPS) < 1e-9
    assert v1 > 1.0 and v2 > 0.5


def test_timemap_window_neutral_and_monotonic():
    tm = TimeMap(FPS, 480)
    prof, _, _ = ramp_with_hit(100, 190, 150, 158 / FPS, FPS, 0.45, 8, 6)
    tm.insert_window(100, 190, profile_keys(prof, 100 / FPS, FPS), {"id": "RAMP_001"})
    assert tm.check() == []
    assert tm.src_frame(50) == 50 and tm.src_frame(300) == 300
    assert tm.src_frame(150) == 158
    assert abs(tm.speed(154) - 0.45) < 0.01   # dentro do hold do slow-mo [150,158)


def test_infeasible_ramp_rejected():
    # pedir para mostrar um instante muito à frente exige velocidade > vmax
    assert ramp_with_hit(100, 130, 110, 200 / FPS, FPS, 0.5, 4, 3) is None


def test_trim_map_jump_cuts():
    tm, n_out = trim_map(FPS, 300, [(0, 100), (130, 300)])
    assert n_out == 270
    assert tm.src_frame(99) == 99 and tm.src_frame(100) == 130 and tm.src_frame(269) == 299


def test_presets_valid_and_shaped():
    k = punch(10, 100.0, 110.0, 3, 9, FPS)
    assert validate_keys(k) == []
    vals = [evaluate(k, f, FPS) for f in range(10, 23)]
    assert abs(max(vals) - 110.0) < 0.5 and abs(vals[-1] - 100.0) < 1e-6
    assert int(np.argmax(vals)) in (2, 3, 4)
    p = pulse(5, 1.0, 1, 8, FPS)
    assert validate_keys(p) == [] and abs(evaluate(p, 6, FPS) - 1.0) < 1e-9 and abs(evaluate(p, 14, FPS)) < 1e-9
