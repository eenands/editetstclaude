"""FASE 40 — revisão frame a frame do PREVIEW renderizado: 1 frame antes, o frame do evento, 1 depois.

Mede na imagem renderizada (não na intenção): pico de luz no frame do flash, início da
mudança visual no frame do golpe, troca de shot exatamente no frame do corte; e, no
nível de dados, freeze/rampas. Gera tiras de frames para revisão humana.
"""
from pathlib import Path

import cv2
import numpy as np

from ..decision.curves import Key
from ..decision.timemap import TimeMap
from ..generate import expressions as X


def _dist(a, b):
    """Distância de cor (Bhattacharyya em H×S) — robusta a zoom, blur e movimento."""
    ha = cv2.calcHist([cv2.cvtColor(a, cv2.COLOR_RGB2HSV)], [0, 1], None, [18, 8], [0, 180, 0, 256])
    hb = cv2.calcHist([cv2.cvtColor(b, cv2.COLOR_RGB2HSV)], [0, 1], None, [18, 8], [0, 180, 0, 256])
    return float(cv2.compareHist(cv2.normalize(ha, ha), cv2.normalize(hb, hb), cv2.HISTCMP_BHATTACHARYYA))


def _scale_rate(a, b):
    """Razão de escala b/a por afim parcial (LK + RANSAC): isola zoom de pan/translação."""
    ga, gb = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
    pts = cv2.goodFeaturesToTrack(ga, 150, 0.01, 4)
    if pts is None or len(pts) < 8:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(ga, gb, pts, None, winSize=(15, 15), maxLevel=3)
    ok = st.ravel() == 1
    if ok.sum() < 8:
        return None
    Mx, _ = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC, ransacReprojThreshold=1.0)
    return None if Mx is None else float(np.hypot(Mx[0, 0], Mx[1, 0]))


def verify_preview(tl, prev, analysis, out_dir, max_strips=12):
    M = prev["metrics"]
    n = len(M)
    luma = np.array([m["luma"] for m in M])
    diff = np.array([m["diff"] for m in M])
    fps = tl["comp"]["fps"]
    pre = tl["precomps"]["PRE_SOURCE_REMAP"]
    tm = TimeMap(fps, tl["source"]["frames"], [Key.from_dict(k) for k in pre["timeremap"]], src_fps=tl["source"]["fps"])
    onsets = sorted({m["frame"] for m in (analysis["audio"]["markers"] if analysis.get("audio") else [])
                     if m["type"] in ("KICK", "SNARE", "DROP", "ENTRY", "IMPACT", "SILENCE")})
    results = []
    marks = [(m["frame"] / fps, m["comment"]) for L in tl["layers"] if L["name"] == "MARKERS_IMPACT" for m in L["markers"]]

    def shake_mag(fr):
        ox, oy, _ = X.shake_eval(marks, fr / fps, 0.0, 1.0)
        return float(np.hypot(ox, oy))

    for e in tl["events"]:
        f = e.get("audio_sync", {}).get("frame") if e.get("audio_sync") else None
        rec, checks = e.get("recipe", ""), []
        if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "SHAKE") and f is not None and 2 <= f < n - 3:
            if "flash" in e.get("params", {}) or rec == "LIGHT_BLOOM":
                w = luma[f - 2:f + 4]
                pk = f - 2 + int(np.argmax(w))
                checks.append(("pico de luz no frame do golpe", pk == f, f"pico em f{pk} (luma {luma[pk]:.3f}; f-1 {luma[f - 1]:.3f})"))
            if "scale" in e.get("params", {}):
                th = [m["thumb"] for m in M]
                r_hit, r_before = _scale_rate(th[f - 1], th[f]), _scale_rate(th[f - 2], th[f - 1])
                if r_hit is not None and r_before is not None:
                    ok = (r_hit - 1) > 0.004 and (r_hit - 1) > (r_before - 1) + 0.003
                    checks.append(("zoom do punch já visível no frame do golpe (não antes, não depois)", ok,
                                   f"escala f-2→f-1 {r_before:.4f}, f-1→f {r_hit:.4f}"))
                else:
                    checks.append(("zoom do punch já visível no frame do golpe", None,
                                   "não mensurável na imagem (quadro sem textura: flash/estouro)"))
            if "shake_px" in e.get("params", {}):
                a0, a1 = shake_mag(f - 1), shake_mag(f)
                checks.append(("shake começa no frame do golpe (expression)", a1 > a0 + 0.3,
                               f"|offset| f-1 {a0:.2f}px → f {a1:.2f}px"))
        if e["category"] in ("TRANSITION", "CUT") and rec in ("HARD_CUT", "WHIP", "ZOOM_THROUGH", "FLASH_CUT", "GLITCH") and f is not None and 1 <= f < n - 4:
            sh_in = (e.get("source") or {}).get("shot_in")
            cut_src = next((s["start_frame"] for s in analysis["shots"] if s["id"] == sh_in), None)
            if cut_src is not None:   # nível de dados: o time remap exibe o shot novo exatamente em f
                ok = tm.src_frame(f) >= cut_src > tm.src_frame(f - 1)
                checks.append(("o shot novo começa exatamente no frame do corte (time remap)", ok,
                               f"f-1→fonte {tm.src_frame(f - 1)}, f→fonte {tm.src_frame(f)}, corte na fonte {cut_src}"))
            if rec == "HARD_CUT":   # nível de imagem: só no corte seco (efeitos pesados mascaram a medida)
                th = [m["thumb"] for m in M]
                d_new, d_old = _dist(th[f], th[f + 3]), _dist(th[f - 1], th[f + 3])
                checks.append(("imagem troca de shot no frame do corte", d_new + 0.05 < d_old,
                               f"dist cor f↔f+3 {d_new:.3f} vs f-1↔f+3 {d_old:.3f}"))
            if rec == "FLASH_CUT":
                pk = f - 2 + int(np.argmax(luma[f - 2:f + 4]))
                checks.append(("pico do flash no corte", pk == f, f"pico em f{pk}"))
        if rec == "MICRO_SYNC":
            p = e["params"]
            b_ = e["audio_sync"]["frame"]
            src_cut = next((op["cut_src_frame"] for op in tl["base_edit"]["operations"]
                            if op.get("op") == "MICRO_SYNC" and op["to_frame"] == b_), None)
            if src_cut is not None:
                checks.append(("corte da fonte exibido no frame do golpe", tm.src_frame(b_) == src_cut > tm.src_frame(b_ - 1),
                               f"f{b_} → fonte {tm.src_frame(b_)} (corte {src_cut})"))
                checks.append(("sincronia restaurada fora da janela", tm.src_frame(e["end_frame"]) == e["end_frame"], ""))
        if e["category"] == "FREEZE":
            a, b = e["start_frame"], e["start_frame"] + e["params"]["freeze_frames"]
            frozen = {tm.src_frame(x) for x in range(a, b)}
            checks.append(("mesmo frame-fonte durante o freeze", len(frozen) == 1, f"frames-fonte {sorted(frozen)}"))
            checks.append(("volta à sincronia no release", tm.src_frame(b) == b, f"f{b} → fonte {tm.src_frame(b)}"))
        if e["category"] == "SPEED_RAMP":
            p = e["params"]
            hs = tm.src_frame(p["hit_out_frame"])
            checks.append(("hit visual cai no golpe de áudio", abs(hs - p["hit_source_frame"]) <= 1,
                           f"saída f{p['hit_out_frame']} mostra fonte f{hs} (alvo f{p['hit_source_frame']})"))
            checks.append(("bordas neutras", tm.src_frame(e["start_frame"]) == e["start_frame"] and tm.src_frame(e["end_frame"]) == e["end_frame"], ""))
        if f is not None and onsets and e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "TRANSITION", "CUT", "FREEZE"):
            off = min(onsets, key=lambda o: abs(o - f)) - f
            e.setdefault("audio_sync", {})["measured_offset_frames"] = int(off) if abs(off) <= 6 else None
        if checks:
            decided = [c for c in checks if c[1] is not None]
            ok = all(c[1] for c in decided)
            state = ("OK" if ok else "FALHOU") if decided else "INCONCLUSIVO"
            e["validation"]["preview"] = state + ": " + "; ".join(
                f"{'✔' if c[1] else ('?' if c[1] is None else '✘')} {c[0]}" + (f" ({c[2]})" if c[2] else "") for c in checks)
            results.append({"id": e["id"], "category": e["category"], "recipe": rec, "ok": ok,
                            "inconclusive": [c[0] for c in checks if c[1] is None], "checks": checks})
    # tiras para revisão humana: eventos mais fortes
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    strips = []
    main = sorted([e for e in tl["events"] if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "TRANSITION", "FREEZE", "PARTICLE")
                   and e.get("audio_sync")], key=lambda e: -e.get("intensity", 0))[:max_strips]
    for e in sorted(main, key=lambda e: e["start_frame"]):
        f = e["audio_sync"]["frame"]
        fr = [x for x in (f - 1, f, f + 1, f + 2) if 0 <= x < n]
        th = [M[x]["thumb"] for x in fr]
        h, w = th[0].shape[:2]
        canvas = np.full((h + 16, w * len(th), 3), 12, np.uint8)
        for i, (x, im) in enumerate(zip(fr, th)):
            canvas[:h, i * w:(i + 1) * w] = im
            cv2.putText(canvas, f"f{x}" + (" HIT" if x == f else ""), (i * w + 3, h + 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35,
                        (255, 220, 120) if x == f else (220, 220, 220), 1, cv2.LINE_AA)
        fn = out / f"verify_{e['id']}_{e.get('recipe', e['category'])}.jpg"
        cv2.imwrite(str(fn), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])
        strips.append(fn.name)
    # frames pretos inesperados (FASE 39)
    black = [m["frame"] for m in M if m["luma"] < 0.02]
    src_black = {i for i, l in enumerate(analysis["motion_per_frame"]["luma"]) if l < 0.02}
    unexpected = [b for b in black if tm.src_frame(b) not in src_black]
    return {"events_checked": len(results), "passed": sum(r["ok"] for r in results),
            "failed": [{"id": r["id"], "recipe": r["recipe"], "checks": [(c[0], c[2]) for c in r["checks"] if c[1] is False]}
                       for r in results if not r["ok"]],
            "inconclusive": [{"id": r["id"], "recipe": r["recipe"], "checks": r["inconclusive"]} for r in results if r["inconclusive"]],
            "unexpected_black_frames": unexpected, "strips": strips}
