"""FASE 5 — áudio como controlador da edição.

Tudo em numpy/scipy (sem librosa):
  STFT -> SuperFlux (onsets) -> fluxo por banda (kick/snare/hat) ->
  tempo (autocorrelação + prior log-normal) -> beats (DP de Ellis 2007) ->
  downbeats -> drops/entradas/impactos -> risers -> silêncio -> seções -> voz.
As confianças são heurísticas (margem sobre limiar), não probabilidades calibradas.
"""
import numpy as np
from scipy.ndimage import maximum_filter1d, uniform_filter1d

from ..io_ffmpeg import read_audio

BANDS = {"bass": (30, 150), "lowmid": (150, 500), "mid": (500, 2500), "high": (2500, 11000)}


def _stft_mag(y, n_fft, hop):
    pad = n_fft // 2
    yp = np.pad(y, pad, mode="reflect")
    n = 1 + (len(yp) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(n)[:, None]
    win = np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(yp[idx] * win, axis=1)).astype(np.float32)


def _norm(x, pct=99):
    s = np.percentile(x, pct) if len(x) else 1.0
    return np.clip(x / (s + 1e-12), 0, None)


def _pick_peaks(env, fr, delta=0.06, floor=0.08):
    pre_max = post_max = max(1, int(0.03 * fr))
    pre_avg = post_avg = max(1, int(0.10 * fr))
    wait = max(1, int(0.045 * fr))
    mx = maximum_filter1d(env, size=pre_max + post_max + 1, mode="constant")
    avg = uniform_filter1d(env, size=pre_avg + post_avg + 1, mode="nearest")
    cand = np.flatnonzero((env == mx) & (env >= avg + delta) & (env >= floor))
    peaks, last = [], -10 ** 9
    for c in cand:
        if c - last >= wait:
            peaks.append(c)
            last = c
    return np.array(peaks, int)


def _refine_time(y, sr, t_coarse, hop, n_fft):
    """Refina o onset para o ponto de maior subida de energia (~3 ms).

    O pico de fluxo aparece quando o transiente ENTRA na janela da STFT, ou seja,
    até n_fft/2 amostras ANTES do transiente real: a busca é enviesada para frente.
    """
    a = int(max(0, t_coarse * sr - 1.0 * hop))
    b = int(min(len(y), t_coarse * sr + n_fft / 2 + 1.0 * hop))
    seg = y[a:b].astype(np.float64)
    w = 48
    if len(seg) < 4 * w:
        return t_coarse
    e = np.sqrt(uniform_filter1d(seg ** 2, size=w))
    step = 8
    rise = e[2 * step::step] - e[:-2 * step:step]
    i = int(np.argmax(rise))
    # primeira amostra em que a subida passa de 50% da subida máxima
    thr = 0.5 * rise[i]
    j = i
    while j > 0 and rise[j - 1] >= thr:
        j -= 1
    return (a + step * j + step) / sr


def _tempo(env, fr, lo=50, hi=220):
    x = env - env.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac = ac / (ac[0] + 1e-12)
    lags = np.arange(len(ac))
    lmin, lmax = int(fr * 60 / hi), int(fr * 60 / lo)
    lmax = min(lmax, len(ac) - 2)
    if lmax <= lmin + 2:
        return None, 0.0, 0.0
    seg = ac[lmin:lmax + 1]
    bpm = 60 * fr / np.maximum(lags[lmin:lmax + 1], 1)
    prior = np.exp(-0.5 * (np.log2(bpm / 120.0)) ** 2)
    i = int(np.argmax(seg * prior))
    li = lmin + i
    # interpolação parabólica para sub-hop
    if 0 < i < len(seg) - 1:
        y0, y1, y2 = seg[i - 1], seg[i], seg[i + 1]
        den = y0 - 2 * y1 + y2
        li = li + (0.5 * (y0 - y2) / den if den != 0 else 0)
    periodicity = float(seg[i])
    conf = float(np.clip((periodicity - 0.08) / 0.3, 0, 1))
    return 60 * fr / li, periodicity, conf


def _beat_track(env, fr, bpm, tightness=100.0):
    period = 60.0 * fr / bpm
    std = env.std() + 1e-12
    k = np.arange(-int(period), int(period) + 1)
    local = np.convolve(env / std, np.exp(-0.5 * (k * 32.0 / period) ** 2), mode="same")
    n = len(local)
    window = np.arange(-int(round(2 * period)), -int(round(period / 2)) + 1)
    txwt = -tightness * np.log(-window / period) ** 2
    cum = np.zeros(n)
    back = -np.ones(n, int)
    first = True
    for i in range(n):
        z = i + window
        ok = z >= 0
        if not ok.any():
            cum[i] = local[i]
            continue
        cand = cum[z[ok]] + txwt[ok]
        j = int(np.argmax(cand))
        cum[i] = local[i] + cand[j]
        if first and local[i] < 0.01 * local.max():
            back[i] = -1
        else:
            back[i] = z[ok][j]
            first = False
    mx = np.flatnonzero((cum[1:-1] > cum[:-2]) & (cum[1:-1] >= cum[2:])) + 1
    if len(mx) == 0:
        return np.array([], int), local
    med = np.median(cum[mx])
    last = int(mx[cum[mx] >= 0.5 * med][-1])
    beats = [last]
    while back[beats[-1]] >= 0:
        beats.append(back[beats[-1]])
    return np.array(beats[::-1], int), local


def _band_bins(freqs, lo, hi):
    return (freqs >= lo) & (freqs < hi)


def _regions(mask, min_len):
    out, start = [], None
    for i, v in enumerate(np.append(mask, False)):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def analyze_audio(path, fps, n_frames, sr=22050, n_fft=1024, hop=256):
    y, sr = read_audio(path, sr)
    if len(y) < n_fft * 4:
        return None
    fr = sr / hop
    S = _stft_mag(y, n_fft, hop)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    T = S.shape[0]
    t_hop = np.arange(T) / fr
    L = np.log1p(100.0 * S)
    ref = maximum_filter1d(L, size=3, axis=1)
    flux = np.zeros_like(L)
    flux[1:] = np.maximum(0, L[1:] - ref[:-1])
    env = _norm(flux.mean(axis=1))
    bflux = {b: _norm(flux[:, _band_bins(freqs, lo, hi)].mean(axis=1)) for b, (lo, hi) in BANDS.items()}
    P = S.astype(np.float64) ** 2
    bpow = {b: P[:, _band_bins(freqs, lo, hi)].sum(axis=1) for b, (lo, hi) in BANDS.items()}
    tot_pow = P.sum(axis=1) + 1e-12
    mh = _band_bins(freqs, 150, 11000)
    flat = np.exp(np.mean(np.log(P[:, mh] + 1e-12), axis=1)) / (P[:, mh].mean(axis=1) + 1e-12)

    # --- RMS por frame de vídeo ---
    spf = sr / fps
    rms_db = np.full(n_frames, -120.0)
    band_frame = {b: np.zeros(n_frames) for b in ("bass", "mid", "high")}
    env_frame = np.zeros(n_frames)
    for k in range(n_frames):
        a, b = int(k * spf), int((k + 1) * spf)
        seg = y[a:b]
        if len(seg):
            rms_db[k] = 20 * np.log10(np.sqrt(np.mean(seg.astype(np.float64) ** 2)) + 1e-9)
        ha, hb = int(k / fps * fr), max(int(k / fps * fr) + 1, int((k + 1) / fps * fr))
        if ha < T:
            env_frame[k] = env[ha:hb].max()
            for bn, src in (("bass", "bass"), ("mid", "mid"), ("high", "high")):
                band_frame[bn][k] = bpow[src][ha:hb].mean()
    p95 = np.percentile(rms_db, 95)
    floor_db = max(-80.0, np.percentile(rms_db, 5))
    loud_norm = np.clip((rms_db - floor_db) / max(1e-6, p95 - floor_db), 0, 1)

    def follower(x, release_s=0.12):
        x = _norm(np.sqrt(x))
        out = np.zeros_like(x)
        rel = np.exp(-1 / (release_s * fps))
        for i in range(len(x)):
            out[i] = max(x[i], out[i - 1] * rel if i else 0)
        return np.clip(out, 0, 1)

    react = {"bass": follower(band_frame["bass"]), "mid": follower(band_frame["mid"]),
             "treble": follower(band_frame["high"]), "amp": loud_norm.copy()}

    # --- tempo e beats ---
    bpm, periodicity, tconf = _tempo(env, fr)
    beats = []
    if bpm:
        bidx, local = _beat_track(env, fr, bpm)
        lmax = np.percentile(local, 99) + 1e-12
        for bi in bidx:
            t = bi / fr
            beats.append({"time": round(t, 4), "frame": int(round(t * fps)),
                          "strength": round(float(np.clip(local[bi] / lmax, 0, 1)), 3)})

    # --- onsets e classificação (pelo AUMENTO de potência por banda) ---
    peaks = _pick_peaks(env, fr)
    hf = _band_bins(freqs, 1000, 10000)

    def mean_rows(arr, t0, t1):
        a, b = max(0, int(round(t0 * fr))), min(T, max(int(round(t0 * fr)) + 1, int(round(t1 * fr))))
        return arr[a:b].mean(axis=0)

    raw = []
    for pk in peaks:
        t = _refine_time(y, sr, pk / fr, hop, n_fft)
        dP = np.maximum(0, mean_rows(P, t, t + 0.05) - mean_rows(P, t - 0.07, t - 0.015))
        dband = {b: float(dP[_band_bins(freqs, lo, hi)].sum()) for b, (lo, hi) in BANDS.items()}
        dhf = dP[hf] + 1e-12
        fl = float(np.exp(np.mean(np.log(dhf))) / dhf.mean())
        raw.append((pk, t, dband, fl))
    ref95 = {b: np.percentile([r[2][b] for r in raw], 95) + 1e-12 for b in BANDS} if raw else {}
    markers = []
    for pk, t, dband, fl in raw:
        act = {b: float(np.clip(dband[b] / ref95[b], 0, 1.5)) for b in BANDS}
        fidx = min(n_frames - 1, int(round(t * fps)))
        strength = float(np.clip(env[pk], 0, 1.5) / 1.5)
        intensity = float(np.clip(0.6 * min(1.0, env[pk]) + 0.4 * loud_norm[fidx], 0, 1))
        labels = []
        if act["bass"] >= 0.3:
            labels.append(("KICK", (act["bass"] - 0.3) / 0.3))
        if act["mid"] >= 0.3 and fl >= 0.15:
            labels.append(("SNARE", min(act["mid"] - 0.3, fl - 0.15) / 0.3))
        frac_high = dband["high"] / (sum(dband.values()) + 1e-12)
        if not labels and frac_high >= 0.5 and act["mid"] < 0.3 and act["bass"] < 0.3:
            labels.append(("HAT", (frac_high - 0.5) / 0.4))
        if not labels:
            labels.append(("ONSET", 0.0))
        for lab, margin in labels:
            markers.append({
                "time": round(t, 4), "frame": fidx, "type": lab,
                "intensity": round(intensity * (0.35 if lab == "HAT" else 1.0), 3),
                "confidence": round(float(np.clip(0.5 + margin, 0.05, 0.99)), 2),
                "strength": round(strength, 3),
                "bands": {k: round(v, 3) for k, v in act.items()}, "flatness": round(fl, 3),
            })

    if beats and markers:
        ot = np.array([m["time"] for m in markers])
        for b in beats:
            j = int(np.argmin(np.abs(ot - b["time"])))
            if abs(ot[j] - b["time"]) <= 0.06:
                b["time"] = float(ot[j])
                b["frame"] = int(round(b["time"] * fps))
                b["snapped"] = True

    # --- silêncio ---
    sil_thr = float(np.clip(p95 - 40, -65, -40))
    silences = []
    for a, b in _regions(rms_db < sil_thr, max(1, int(0.40 * fps))):
        silences.append({"start": round(a / fps, 3), "end": round(b / fps, 3),
                         "start_frame": a, "end_frame": b, "threshold_db": round(sil_thr, 1)})
        markers.append({"time": round(a / fps, 4), "frame": a, "type": "SILENCE",
                        "intensity": 0.0, "confidence": 0.95, "duration": round((b - a) / fps, 3)})

    # --- risers (crescendo sustentado na banda alta ou no RMS) ---
    hi_db = 10 * np.log10(uniform_filter1d(bpow["high"], size=max(1, int(0.5 * fr))) + 1e-12)
    rms_hop_db = 10 * np.log10(uniform_filter1d(tot_pow, size=max(1, int(0.5 * fr))) + 1e-12)
    win, step = int(1.0 * fr), max(1, int(0.1 * fr))
    rise = np.zeros(T, bool)
    tt = np.arange(win) / fr
    for s in range(0, T - win, step):
        for sig in (hi_db, rms_hop_db):
            seg = sig[s:s + win]
            c = np.polyfit(tt, seg, 1)
            r2 = 1 - np.var(seg - np.polyval(c, tt)) / (np.var(seg) + 1e-12)
            if c[0] > 5.0 and r2 > 0.75:
                rise[s:s + win] = True
    risers = []
    for a, b in _regions(rise, int(1.2 * fr)):
        risers.append({"start": round(a / fr, 3), "end": round(b / fr, 3),
                       "start_frame": int(round(a / fr * fps)), "end_frame": int(round(b / fr * fps))})
        markers.append({"time": round(a / fr, 4), "frame": int(round(a / fr * fps)), "type": "RISER",
                        "intensity": 0.6, "confidence": 0.6, "duration": round((b - a) / fr, 3)})

    # --- seções (novidade de energia) ---
    hop_s = 0.1
    grid = np.arange(0, T / fr, hop_s)
    feat = np.stack([np.interp(grid, t_hop, rms_hop_db),
                     np.interp(grid, t_hop, 10 * np.log10(bpow["bass"] / tot_pow + 1e-9)),
                     np.interp(grid, t_hop, 10 * np.log10(bpow["high"] / tot_pow + 1e-9))], axis=1)
    feat = (feat - feat.mean(0)) / (feat.std(0) + 1e-9)
    hw = int(1.5 / hop_s)
    nov = np.zeros(len(grid))
    for i in range(hw, len(grid) - hw):
        nov[i] = np.linalg.norm(feat[i:i + hw].mean(0) - feat[i - hw:i].mean(0))
    sections = []
    if nov.max() > 0:
        nn = nov / nov.max()
        mx = maximum_filter1d(nn, size=int(3.0 / hop_s))
        for i in np.flatnonzero((nn == mx) & (nn > 0.35)):
            t = grid[i]
            if beats:
                bt = min(beats, key=lambda b: abs(b["time"] - t))
                if abs(bt["time"] - t) < 0.25:
                    t = bt["time"]
            sections.append({"time": round(float(t), 3), "frame": int(round(t * fps)),
                             "novelty": round(float(nn[i]), 3)})

    # --- downbeats (4/4): kicks fortes, backbeats com caixa, seções no "1" ---
    if len(beats) >= 8:
        bt = np.array([b["time"] for b in beats])
        bi_hop = np.clip((bt * fr).astype(int), 0, T - 1)
        bass_b = np.array([bflux["bass"][max(0, i - 2):i + 3].max() for i in bi_hop])
        mid_b = np.array([bflux["mid"][max(0, i - 2):i + 3].max() for i in bi_hop])
        sec_b = np.zeros(len(bt))
        for s in sections:
            j = int(np.argmin(np.abs(bt - s["time"])))
            if abs(bt[j] - s["time"]) < 0.3:
                sec_b[j] += s["novelty"]
        scores = []
        for p in range(4):
            idx = np.arange(len(bt))
            ph = (idx - p) % 4
            sc = (bass_b[ph == 0].mean() + mid_b[(ph == 1) | (ph == 3)].mean()
                  - mid_b[(ph == 0) | (ph == 2)].mean() + 2.0 * sec_b[ph == 0].sum())
            scores.append(sc)
        order = np.argsort(scores)[::-1]
        best = int(order[0])
        dconf = float(np.clip((scores[order[0]] - scores[order[1]]) / (abs(scores[order[0]]) + 1e-9), 0, 1))
        for i, b in enumerate(beats):
            b["is_downbeat"] = (i - best) % 4 == 0
        downbeat_conf = round(dconf, 2)
    else:
        downbeat_conf = 0.0
        for b in beats:
            b["is_downbeat"] = False
    for b in beats:
        markers.append({"time": b["time"], "frame": b["frame"],
                        "type": "DOWNBEAT" if b["is_downbeat"] else "BEAT",
                        "intensity": b["strength"], "confidence": round(tconf, 2)})

    # --- drops / entradas / impactos ---
    def mean_pow(arr, t0, t1):
        a, b = max(0, int(t0 * fr)), min(T, int(t1 * fr))
        return arr[a:b].mean() if b > a else 1e-12

    # candidatos: golpes graves/caixas ou onsets muito fortes, com >= 1 s de contexto anterior
    strong = [m for m in markers if m["time"] >= 1.0 and (
        (m["type"] in ("KICK", "SNARE") and m["intensity"] >= 0.35)
        or (m["type"] == "ONSET" and m["strength"] >= 0.6))]
    cand_events = []
    for m in strong:
        t = m["time"]
        g = 10 * np.log10(mean_pow(tot_pow, t, t + 1.0) / (mean_pow(tot_pow, t - 2.0, t - 0.1) + 1e-12))
        gb = 10 * np.log10(mean_pow(bpow["bass"], t, t + 1.0) / (mean_pow(bpow["bass"], t - 2.0, t - 0.1) + 1e-12))
        sustain = 10 * np.log10(mean_pow(tot_pow, t + 1.0, t + 2.5) / (mean_pow(tot_pow, t - 2.0, t - 0.1) + 1e-12))
        after_riser = any(abs(r["end"] - t) <= 0.6 for r in risers)
        after_silence = any(-0.1 <= t - sl["end"] <= 0.3 for sl in silences)
        kind = None
        if g >= 2.0 and after_riser and sustain >= 0:
            kind = "DROP"
        elif g >= 6.0 and sustain >= 3.0:
            kind = "ENTRY"
        elif g >= 8.0 or (after_silence and m["intensity"] >= 0.6):
            kind = "IMPACT"
        if kind:
            on_down = any(abs(b["time"] - t) < 0.05 and b.get("is_downbeat") for b in beats)
            rank = g + (3.0 if m["type"] == "KICK" else 0.0) + (3.0 if on_down else 0.0)
            cand_events.append((t, kind, g, gb, m, rank))
    chosen = []
    for ev in sorted(cand_events, key=lambda e: -e[5]):
        if all(abs(ev[0] - c[0]) > 1.5 for c in chosen):
            chosen.append(ev)
    chosen = [c[:5] for c in chosen]
    for r in risers:   # riser termina no drop que ele prepara
        for t, kind, g, gb, m in chosen:
            if kind == "DROP" and r["start"] < t < r["end"]:
                r["end"], r["end_frame"] = round(t, 3), m["frame"]
    for mk in markers:
        if mk["type"] == "RISER":
            r = next(r for r in risers if abs(r["start"] - mk["time"]) < 1e-3)
            mk["duration"] = round(r["end"] - r["start"], 3)
    for t, kind, g, gb, m in sorted(chosen):
        markers.append({"time": t, "frame": m["frame"], "type": kind,
                        "intensity": round(float(np.clip(0.55 + g / 30, 0, 1)), 3),
                        "confidence": round(float(np.clip(0.4 + g / 20, 0, 0.95)), 2),
                        "gain_db": round(float(g), 1), "bass_gain_db": round(float(gb), 1)})

    # --- atividade vocal (heurística: banda de voz harmônica + modulação silábica 3–8 Hz) ---
    vb = _band_bins(freqs, 300, 3400)
    v_frac = P[:, vb].sum(1) / tot_pow
    v_flat = np.exp(np.mean(np.log(P[:, vb] + 1e-12), axis=1)) / (P[:, vb].mean(axis=1) + 1e-12)
    v_env = np.sqrt(P[:, vb].sum(1))
    W = int(1.0 * fr)
    vocal = np.zeros(T, bool)
    mf = np.fft.rfftfreq(W, 1 / fr)
    syl = (mf >= 3) & (mf <= 8)
    allm = (mf >= 0.5) & (mf <= 20)
    for s in range(0, T - W, W // 2):
        e = v_env[s:s + W] - v_env[s:s + W].mean()
        M = np.abs(np.fft.rfft(e * np.hanning(W))) ** 2
        mod = M[syl].sum() / (M[allm].sum() + 1e-12)
        if v_frac[s:s + W].mean() > 0.5 and v_flat[s:s + W].mean() < 0.3 and mod > 0.45 \
                and rms_hop_db[s:s + W].mean() > sil_thr + 10:
            vocal[s:s + W] = True
    vocal_regions = [{"start": round(a / fr, 3), "end": round(b / fr, 3), "confidence": 0.5}
                     for a, b in _regions(vocal, int(0.5 * fr))]
    vocal_frac = float(vocal.mean())
    for r in vocal_regions:
        markers.append({"time": r["start"], "frame": int(round(r["start"] * fps)), "type": "VOCAL",
                        "intensity": 0.5, "confidence": 0.5, "duration": round(r["end"] - r["start"], 3)})

    spec = P.mean(0)
    spec[freqs < 30] = 0
    dom = float(freqs[int(np.argmax(spec))])

    reasons = [f"periodicidade rítmica {periodicity:.2f} (conf. tempo {tconf:.2f})",
               f"fração com atividade vocal (heurística) {vocal_frac:.2f}"]
    if tconf >= 0.5 and vocal_frac < 0.3:
        mode = "music"
    elif vocal_frac >= 0.3 and tconf < 0.4:
        mode = "dialogue"
    else:
        mode = "mixed"

    markers.sort(key=lambda m: (m["time"], m["type"]))
    return {
        "sr": sr, "hop": hop, "duration_s": round(len(y) / sr, 4),
        "rms_db_per_frame": np.round(rms_db, 2).tolist(),
        "loud_norm_per_frame": np.round(loud_norm, 4).tolist(),
        "onset_per_frame": np.round(np.clip(env_frame, 0, 1.5) / 1.5, 4).tolist(),
        "react_per_frame": {k: np.round(v, 4).tolist() for k, v in react.items()},
        "tempo": {"bpm": round(float(bpm), 2) if bpm else None,
                  "periodicity": round(periodicity, 3), "confidence": round(tconf, 2),
                  "downbeat_confidence": downbeat_conf},
        "beats": beats, "markers": markers, "silences": silences, "risers": risers,
        "sections": sections, "vocal_regions": vocal_regions,
        "dominant_freq_hz": round(dom, 1), "silence_threshold_db": round(sil_thr, 1),
        "peak_rms_db": round(float(rms_db.max()), 2),
        "mode": {"value": mode, "reasons": reasons},
    }
