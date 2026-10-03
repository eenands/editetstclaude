"""FASE 4 — detecção e descrição de shots."""
import colorsys

import cv2
import numpy as np


def detect_cuts(rows, hists, fps):
    """Corte = pico local de distância de histograma acima de limiar adaptativo.

    Rejeita flashes/transientes: se em até 6 frames o quadro volta a parecer o
    anterior, é mudança de iluminação, não corte (vira evento visual FLASH).
    """
    n = len(rows)
    d = np.array([r["hist_d"] for r in rows])
    inl = np.array([r["inlier"] for r in rows])
    luma = np.array([r["luma"] for r in rows])
    half = max(3, int(fps * 0.5))
    cand = []
    for t in range(1, n):
        lo, hi = max(1, t - half), min(n, t + half + 1)
        neigh = np.delete(d[lo:hi], t - lo)
        base = float(np.median(neigh)) if len(neigh) else 0.0
        thr = max(0.22, 3.0 * base + 0.08)
        if d[t] >= thr:
            cand.append((t, thr))
    # agrupa candidatos a <= 6 frames: um grupo é UM evento (corte, ou flash/transiente)
    groups, cur = [], []
    for c in cand:
        if cur and c[0] - cur[-1][0] > 6:
            groups.append(cur)
            cur = []
        cur.append(c)
    if cur:
        groups.append(cur)
    cuts, flashes = [], []
    for g in groups:
        t0, t1 = g[0][0], g[-1][0]
        ks = range(t1 + 1, min(n, t1 + 7))
        back = min((cv2.compareHist(hists[t0 - 1], hists[k], cv2.HISTCMP_BHATTACHARYYA) for k in ks), default=1.0)
        tpk, thr = max(g, key=lambda c: d[c[0]])
        if back < max(0.15, 0.4 * d[tpk]):
            # o quadro de antes reaparece depois: mudança de iluminação/transiente, não corte
            flashes.append({"frame": t0, "time": round(t0 / fps, 4), "end_frame": t1,
                            "luma_jump": round(float(luma[t0] - luma[t0 - 1]), 3),
                            "hist_d": round(float(d[tpk]), 3), "returns_like_before": round(float(back), 3)})
            continue
        if len(g) > 1:   # A -> quadro(s) intruso(s) -> B: corte no início, intrusos viram flash
            flashes.append({"frame": t0, "time": round(t0 / fps, 4), "end_frame": t1, "luma_jump":
                            round(float(luma[t0] - luma[t0 - 1]), 3), "hist_d": round(float(d[t0]), 3),
                            "returns_like_before": round(float(back), 3), "inside_cut": True})
        t, thr = g[0]
        conf = float(np.clip(0.5 * (d[t] - thr) / thr + 0.5 + 0.3 * (1 - inl[t]), 0, 1))
        cuts.append({"frame": t, "time": round(t / fps, 4), "type": "CUT", "hist_d": round(float(d[t]), 3),
                     "threshold": round(thr, 3), "confidence": round(conf, 2)})
    # fades para preto (>=2 frames quase pretos): fronteira no meio
    dark = luma < 0.03
    t = 0
    while t < n:
        if dark[t]:
            u = t
            while u < n and dark[u]:
                u += 1
            if u - t >= 2 and 0 < t and u < n:
                m = (t + u) // 2
                if all(abs(c["frame"] - m) > 3 for c in cuts):
                    cuts.append({"frame": m, "time": round(m / fps, 4), "type": "FADE",
                                 "hist_d": 0.0, "threshold": 0.0, "confidence": 0.7})
            t = u
        else:
            t += 1
    cuts.sort(key=lambda c: c["frame"])
    return cuts, flashes


HUE_NAMES = [(15, "vermelho"), (40, "laranja"), (65, "amarelo"), (160, "verde"), (195, "ciano"),
             (255, "azul"), (290, "violeta"), (335, "magenta"), (360, "vermelho")]


def color_name(rgb):
    h, l, s = colorsys.rgb_to_hls(*rgb)
    if s < 0.15 or l < 0.08 or l > 0.92:
        return "neutro escuro" if l < 0.35 else ("neutro claro" if l > 0.7 else "neutro")
    hd = h * 360
    return next(name for lim, name in HUE_NAMES if hd <= lim)


def _direction(dx, dy, w):
    """Converte deslocamento do CONTEÚDO em direção da CÂMERA (oposta)."""
    sp = np.hypot(dx, dy) / w
    if sp < 0.0015:
        return "estática", None
    ang = (np.degrees(np.arctan2(dy, dx)) + 360) % 360     # direção do conteúdo na tela (y para baixo)
    cam = (ang + 180) % 360
    names = {0: "pan → direita", 90: "tilt ↓ baixo", 180: "pan ← esquerda", 270: "tilt ↑ cima"}
    key = min(names, key=lambda a: min(abs(cam - a), 360 - abs(cam - a)))
    return names[key], round(float(ang), 1)


def _pos_name(x, y):
    hx = "esquerda" if x < 0.4 else ("direita" if x > 0.6 else "centro")
    vy = "alto" if y < 0.4 else ("baixo" if y > 0.6 else "")
    return (hx + (" " + vy if vy else "")).strip()


def build_shots(cuts, n, fps):
    bounds = [0] + [c["frame"] for c in cuts] + [n]
    shots = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        if b - a < 1:
            continue
        shots.append({"id": f"SHOT_{len(shots) + 1:03d}", "start_frame": a, "end_frame": b,
                      "start_time": round(a / fps, 4), "end_time": round(b / fps, 4),
                      "duration_s": round((b - a) / fps, 4), "duration_frames": b - a,
                      "boundary_in": cuts[i - 1]["type"] if i > 0 else "START"})
    return shots


def enrich_shots(shots, rows, samples, audio, src_w, fps):
    loud = np.array(audio["loud_norm_per_frame"]) if audio else None
    for s in shots:
        a, b = s["start_frame"], s["end_frame"]
        R = rows[a + 1:b] if b - a > 2 else rows[a:b]
        dx = np.array([r["dx"] for r in R])
        dy = np.array([r["dy"] for r in R])
        speed = np.hypot(dx, dy) / src_w
        med_dx, med_dy = float(np.median(dx)), float(np.median(dy))
        # tremor de câmera na mão: resíduo de alta frequência em torno da tendência suave
        if len(dx) > 5:
            k = np.ones(5) / 5
            jit = float(np.std(dx - np.convolve(dx, k, "same")) + np.std(dy - np.convolve(dy, k, "same"))) / src_w
        else:
            jit = 0.0
        good = [r for r in R if r["inlier"] >= 0.5]   # frames com estimativa confiável
        zoom = float(np.prod([r["zoom"] for r in good])) if good else 1.0
        rot = float(np.sum([r["rot"] for r in good])) if good else 0.0
        sp = float(np.mean(speed)) if len(speed) else 0.0
        level = "LOW" if sp < 0.002 else ("MEDIUM" if sp < 0.008 else "HIGH")
        cam, ang = _direction(med_dx, med_dy, src_w)
        if zoom > 1.04:
            cam += " + push-in"
        elif zoom < 0.96:
            cam += " + pull-out"
        rgb = np.mean([rows[k]["rgb"] for k in range(a, b)], axis=0)
        luma = float(np.mean([rows[k]["luma"] for k in range(a, b)]))
        smp = [samples[k] for k in sorted(samples) if a <= k < b]
        faces = max((x["faces"] for x in smp), default=0)
        if smp:
            cx = float(np.median([x["roi_center"][0] for x in smp]))
            cy = float(np.median([x["roi_center"][1] for x in smp]))
            kind = "rosto" if faces else "região saliente"
            subject = f"{kind} ({_pos_name(cx, cy)})"
        else:
            cx = cy = 0.5
            subject = "indefinido"
        a_en = float(loud[a:b].mean()) if loud is not None and b <= len(loud) else 0.0
        accel = float(np.max(np.abs(np.diff(speed)))) if len(speed) > 2 else 0.0
        s.update({
            "motion_speed": round(sp, 5), "motion_level": level,
            "content_velocity_px": [round(med_dx, 2), round(med_dy, 2)],
            "content_angle_deg": ang, "camera": cam, "handheld_jitter": round(jit, 5),
            "zoom_total": round(zoom, 4), "roll_total_deg": round(rot, 2),
            "peak_accel": round(accel, 5),
            "luma": round(luma, 4), "contrast": round(float(np.mean([rows[k]["contrast"] for k in range(a, b)])), 4),
            "black": round(float(np.mean([rows[k]["black"] for k in range(a, b)])), 4),
            "white": round(float(np.mean([rows[k]["white"] for k in range(a, b)])), 4),
            "sat": round(float(np.mean([rows[k]["sat"] for k in range(a, b)])), 4),
            "mean_rgb": [round(float(v), 4) for v in rgb],
            "dominant_color": {"hex": "#%02x%02x%02x" % tuple(int(v * 255) for v in rgb), "name": color_name(rgb)},
            "subject": subject, "subject_center": [round(cx, 3), round(cy, 3)], "faces": int(faces),
            "audio_energy": round(a_en, 3),
        })
    # energia visual relativa (0..1 entre shots)
    if shots:
        m = np.array([s["motion_speed"] for s in shots])
        c = np.array([s["contrast"] for s in shots])
        mv = m / (m.max() + 1e-9)
        cv = c / (c.max() + 1e-9)
        for s, a1, b1 in zip(shots, mv, cv):
            s["visual_energy"] = round(float(0.75 * a1 + 0.25 * b1), 3)
            s["energy"] = round(0.5 * s["visual_energy"] + 0.5 * s["audio_energy"], 3)
            pot, why = [], []
            if s["motion_level"] == "HIGH" and s["duration_s"] >= 1.0:
                pot.append("SPEED_RAMP"); why.append("movimento rápido sustentado")
            if s["content_angle_deg"] is not None and s["motion_level"] != "LOW":
                pot.append("WHIP_SOURCE"); why.append("direção de câmera clara para transição derivada do movimento")
            if s["motion_level"] == "LOW" and s["duration_s"] >= 2.0:
                pot.append("PUNCH_IN_CUTS"); why.append("plano estável e longo: zoom-cuts no beat")
            if s["luma"] < 0.3 or abs(s["mean_rgb"][0] - s["mean_rgb"][2]) > 0.12:
                pot.append("COLOR_FIX"); why.append("exposição baixa ou dominante de cor")
            s["editing_potential"] = {"tags": pot, "why": why,
                                      "score": round(min(1.0, 0.25 * len(pot) + 0.5 * s["energy"]), 2)}
    return shots
