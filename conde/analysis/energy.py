"""FASE 6 — curva de energia 0..1 por frame."""
import numpy as np
from scipy.ndimage import gaussian_filter1d

BANDS = [(0.35, "LOW"), (0.60, "MEDIUM"), (0.80, "HIGH"), (1.01, "EXTREME")]


def _rn(x):
    lo, hi = np.percentile(x, 2), np.percentile(x, 98)
    return np.clip((x - lo) / (hi - lo + 1e-9), 0, 1)


def band_of(e):
    return next(name for lim, name in BANDS if e < lim)


def energy_map(rows, cuts, audio, src_w, fps):
    n = len(rows)
    speed = np.array([np.hypot(r["dx"], r["dy"]) / src_w for r in rows])
    obj = np.array([r["obj"] / src_w for r in rows])
    hd = np.array([r["hist_d"] for r in rows])
    cut_imp = np.zeros(n)
    for c in cuts:
        cut_imp[c["frame"]] = 1.0
        speed[c["frame"]] = obj[c["frame"]] = hd[c["frame"]] = 0.0   # corte não é movimento
    v_motion = _rn(gaussian_filter1d(speed + 0.5 * obj, fps * 0.2))
    v_change = _rn(gaussian_filter1d(hd, fps * 0.2) + gaussian_filter1d(cut_imp, fps * 1.0) * 3)
    comps = {"visual_motion": v_motion, "visual_change": v_change}
    if audio:
        loud = np.array(audio["loud_norm_per_frame"][:n])
        ons = np.array(audio["onset_per_frame"][:n])
        dens = gaussian_filter1d(ons, fps * 0.5)
        comps["audio_loudness"] = loud
        comps["audio_onset_density"] = _rn(dens)
        raw = 0.35 * loud + 0.25 * comps["audio_onset_density"] + 0.25 * v_motion + 0.15 * v_change
    else:
        raw = 0.6 * v_motion + 0.4 * v_change
    fast = gaussian_filter1d(raw, fps * 0.15)
    slow = gaussian_filter1d(raw, fps * 1.0)
    e = _rn(0.65 * slow + 0.35 * fast)
    step = max(1, int(round(fps / 2)))
    summary = [{"time": round(k / fps, 3), "frame": k, "energy": round(float(e[k]), 3), "band": band_of(e[k])}
               for k in range(0, n, step)]
    return {
        "per_frame": np.round(e, 4).tolist(),
        "components_per_frame": {k: np.round(v, 4).tolist() for k, v in comps.items()},
        "weights": "0.35 loudness + 0.25 densidade de onsets + 0.25 movimento + 0.15 mudança visual"
                   if audio else "0.6 movimento + 0.4 mudança visual (sem áudio)",
        "summary_0_5s": summary,
        "band_share": {name: round(float(np.mean([band_of(x) == name for x in e])), 3) for _, name in BANDS},
    }
