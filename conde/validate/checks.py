"""FASES 39, 41–43, 45, 57–59 — sistema de qualidade sobre a timeline (dados, não intenção)."""
import numpy as np

VISUAL = ("CUT", "TRANSITION", "PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "GLITCH", "FREEZE", "ZOOM", "TEXT",
          "PARTICLE", "SHAKE")
DENSITY_BANDS = [(1.0, "LOW"), (2.0, "MEDIUM"), (3.5, "HIGH"), (1e9, "EXTREME")]


def _band(x):
    return next(n for lim, n in DENSITY_BANDS if x < lim)


def quality(tl, analysis):
    fps, n = tl["comp"]["fps"], tl["comp"]["duration_frames"]
    ev = tl["events"]
    issues, report = [], {}
    # ---- FASE 41: repetição
    rec = {}
    for e in ev:
        if e["category"] in VISUAL:
            rec[e.get("recipe", e["category"])] = rec.get(e.get("recipe", e["category"]), 0) + 1
    tot = sum(rec.values()) or 1
    report["repetition"] = dict(sorted(rec.items(), key=lambda kv: -kv[1]))
    for r, c in rec.items():
        if r not in ("HARD_CUT", "MICRO_SYNC", "CHAR_CASCADE", "IMPACT", "WORD_REVEAL") and c / tot > 0.35 and c >= 4:
            issues.append({"severity": "criativo", "phase": 41, "msg": f"{r} usado {c}× ({c / tot:.0%}) — vocabulário estreito"})
    # ---- FASE 42: densidade visual (mudanças relevantes por segundo)
    hits = np.zeros(n)
    for e in ev:
        if e["category"] in VISUAL:
            f = (e.get("audio_sync") or {}).get("frame", e["start_frame"])
            if 0 <= f < n:
                hits[f] += 1
            if e["category"] == "TEXT" and e["end_frame"] < n:
                hits[e["end_frame"] - 1] += 1
        if e["category"] == "ZOOM":
            for f in e["params"].get("frames", []):
                hits[min(n - 1, f)] += 1
    win = int(round(fps))
    dens = np.convolve(hits, np.ones(win), mode="same")
    bands = [_band(x) for x in dens]
    share = {b: round(float(np.mean([x == b for x in bands])), 3) for _, b in DENSITY_BANDS}
    energy = np.array(tl["energy_per_frame"])
    r = float(np.corrcoef(dens, energy)[0, 1]) if dens.std() > 0 and energy.std() > 0 else 0.0
    report["visual_density"] = {"per_second_mean": round(float(dens.mean()), 2), "per_second_max": round(float(dens.max()), 2),
                                "band_share": share, "corr_with_energy": round(r, 3)}
    if share["EXTREME"] > 0.25:
        issues.append({"severity": "criativo", "phase": 42, "msg": f"densidade EXTREME em {share['EXTREME']:.0%} do tempo"})
    if r < 0.2:
        issues.append({"severity": "criativo", "phase": 42, "msg": f"densidade pouco correlacionada com a energia (r={r:.2f})"})
    # ---- FASE 43/59: respiros e pausa
    zones = tl["zones"]["breath"]
    bz = sum(z["end_frame"] - z["start_frame"] for z in zones) / n
    in_breath = [f for z in zones for f in range(z["start_frame"], z["end_frame"])]
    bd = float(np.mean(dens[in_breath])) if in_breath else 0.0
    report["breath"] = {"zones": len(zones), "share": round(bz, 3), "density_inside": round(bd, 2)}
    if not zones:
        issues.append({"severity": "criativo", "phase": 43, "msg": "nenhum momento de respiro"})
    loud = np.zeros(n, int)
    for e in ev:
        if e["category"] in ("SHAKE", "PUNCH", "FLASH", "GLITCH", "PARTICLE", "DISTORTION", "BLUR", "VFX"):
            loud[e["start_frame"]:e["end_frame"]] += 1
    chaos = loud >= 2
    run = best = 0
    for c in chaos:
        run = run + 1 if c else 0
        best = max(best, run)
    report["pause_test"] = {"longest_stacked_fx_s": round(best / fps, 2), "stacked_share": round(float(chaos.mean()), 3)}
    if best / fps > 3.0:
        issues.append({"severity": "criativo", "phase": 59, "msg": f"{best / fps:.1f}s seguidos de efeitos empilhados — vira ruído"})
    # ---- FASE 57: teste sem áudio / só áudio
    beats = sorted({m["frame"] for m in analysis["audio"]["markers"] if m["type"] in ("BEAT", "DOWNBEAT")}) if analysis.get("audio") else []
    vis_frames = sorted({(e.get("audio_sync") or {}).get("frame", e["start_frame"]) for e in ev
                         if e["category"] in ("CUT", "TRANSITION", "PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "ZOOM", "FREEZE")})
    on_grid = sum(1 for f in vis_frames if beats and min(abs(f - b) for b in beats) <= 1) / max(1, len(vis_frames))
    ioi = np.diff(vis_frames) / fps if len(vis_frames) > 2 else np.array([0.0])
    report["no_audio_test"] = {"visual_events": len(vis_frames), "on_beat_grid": round(on_grid, 3),
                               "ioi_mean_s": round(float(ioi.mean()), 3), "ioi_cv": round(float(ioi.std() / (ioi.mean() + 1e-9)), 3)}
    strong = []
    if analysis.get("audio"):
        ks = [m for m in analysis["audio"]["markers"] if m["type"] in ("KICK", "SNARE")]
        thr = np.percentile([m["intensity"] for m in ks], 75) if ks else 1
        strong = sorted({m["frame"] for m in analysis["audio"]["markers"]
                         if m["type"] in ("DROP", "ENTRY", "IMPACT") or (m["type"] in ("KICK", "SNARE") and m["intensity"] >= thr)})
    shake_frames = {m["frame"] for L in tl["layers"] if L["name"] == "MARKERS_IMPACT" for m in L["markers"]}
    covered = [f for f in strong if any(abs(f - v) <= 1 for v in vis_frames) or any(abs(f - s) <= 1 for s in shake_frames)]
    report["audio_only_test"] = {"strong_audio_events": len(strong), "represented_visually": len(covered),
                                 "coverage": round(len(covered) / max(1, len(strong)), 3),
                                 "missed_frames": [f for f in strong if f not in covered]}
    big = [m["frame"] for m in (analysis["audio"]["markers"] if analysis.get("audio") else []) if m["type"] in ("DROP", "ENTRY", "IMPACT")]
    for f in big:
        if not any(abs(f - v) <= 1 for v in vis_frames):
            issues.append({"severity": "criativo", "phase": 57, "msg": f"golpe principal em f{f} sem representação visual"})
    # ---- FASE 58: silhueta — texto/gráfico não cobre o sujeito
    roi = analysis["roi_per_frame"]["box"]
    W, H = tl["comp"]["width"], tl["comp"]["height"]
    over = []
    for L in tl["layers"]:
        if L["kind"] != "text":
            continue
        x0, y0, x1, y1 = L["text"]["box_px"]
        tb = [x0 / W, y0 / H, x1 / W, y1 / H]
        worst = 0.0
        for f in range(L["in"], L["out"], 3):
            b = roi[min(len(roi) - 1, f)]
            if (b[2] - b[0]) * (b[3] - b[1]) > 0.4:
                continue   # ROI difusa (sem sujeito localizado): nada a proteger
            ix = max(0, min(tb[2], b[2]) - max(tb[0], b[0]))
            iy = max(0, min(tb[3], b[3]) - max(tb[1], b[1]))
            worst = max(worst, ix * iy / ((tb[2] - tb[0]) * (tb[3] - tb[1]) + 1e-9))
        over.append({"layer": L["name"], "max_overlap_with_subject": round(worst, 3)})
        if worst > 0.25:
            issues.append({"severity": "criativo", "phase": 58, "msg": f"{L['name']} cobre {worst:.0%} da região do sujeito"})
        if not (0.05 * W <= x0 and x1 <= 0.95 * W and 0.05 * H <= y0 and y1 <= 0.95 * H):
            issues.append({"severity": "técnico", "phase": 39, "msg": f"{L['name']} fora da área segura"})
        hold = (L["text"]["out_frames"][0] - L["text"]["in_frames"][1]) / fps
        if hold < 0.8:
            issues.append({"severity": "criativo", "phase": 45, "msg": f"{L['name']} fica legível só {hold:.2f}s"})
    report["silhouette"] = over
    # ---- FASE 39: técnicos
    for c in tl["build_checks"]:
        issues.append({"severity": "técnico", "phase": 39, "msg": f"{c['layer']}.{c['prop']}: {c['errors']}"})
    tr = [e.get("recipe") for e in ev if e["category"] == "TRANSITION"]
    for a, b in zip(tr, tr[1:]):
        if a == b:
            issues.append({"severity": "criativo", "phase": 46, "msg": f"transição {a} repetida em sequência"})
    report["issues"] = issues
    return report
