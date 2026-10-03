"""Orquestra as FASES 1–6 + 17 e grava os artefatos de análise."""
import csv
import json
import time
from pathlib import Path

import cv2
import numpy as np

from .audio import analyze_audio
from .energy import energy_map
from .media import analyze_media
from .shots import build_shots, detect_cuts, enrich_shots
from .video import interpolate_samples, pass1, pass2, schedule


def _sheet(images, labels, cols, path):
    if not images:
        return
    h, w = images[0].shape[:2]
    rows = (len(images) + cols - 1) // cols
    canvas = np.full((rows * (h + 18), cols * w, 3), 16, np.uint8)
    for i, (im, lab) in enumerate(zip(images, labels)):
        r, c = divmod(i, cols)
        y, x = r * (h + 18), c * w
        canvas[y:y + h, x:x + w] = im[:h, :w]
        cv2.putText(canvas, lab, (x + 4, y + h + 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (230, 230, 230), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), cv2.cvtColor(canvas, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])


def analyze(path, out_dir, log=print):
    out = Path(out_dir)
    (out / "frames").mkdir(parents=True, exist_ok=True)
    timings = {}
    t0 = time.time()
    media = analyze_media(path)
    v = media["video"]
    fps, fps_str = v["fps"], f'{v["fps_num"]}/{v["fps_den"]}'
    W, H = v["width"], v["height"]
    timings["media"] = time.time() - t0
    log(f"[1] mídia: {W}x{H} @ {fps:.3f} fps, {v['duration_s']:.2f}s, {v['codec']}")

    t0 = time.time()
    rows, hists, _ = pass1(path, W, H, fps_str)
    n = len(rows)
    timings["video_pass1"] = time.time() - t0
    log(f"[2] passada 1: {n} frames analisados (todos) em {timings['video_pass1']:.1f}s")

    t0 = time.time()
    audio = analyze_audio(path, fps, n) if media["audio"] else None
    timings["audio"] = time.time() - t0
    if audio:
        log(f"[5] áudio: {audio['tempo']['bpm']} BPM (conf {audio['tempo']['confidence']}), modo {audio['mode']['value']}, "
            f"{len(audio['markers'])} marcadores")

    cuts, flashes = detect_cuts(rows, hists, fps)
    shots = build_shots(cuts, n, fps)
    log(f"[4] shots: {len(shots)} ({len(cuts)} cortes, {len(flashes)} flashes/transientes rejeitados como corte)")

    critical = set(c["frame"] for c in cuts) | set(f["frame"] for f in flashes)
    if audio:
        critical |= {m["frame"] for m in audio["markers"]
                     if m["type"] in ("DROP", "ENTRY", "IMPACT") or
                     (m["type"] in ("KICK", "SNARE") and m["intensity"] >= 0.5)}
    stride, sched = schedule(n, rows, critical, W)
    thumb_frames = set()
    for s in shots:
        a, b = s["start_frame"], s["end_frame"]
        thumb_frames |= {a, (a + b) // 2, b - 1}
    for c in sorted(critical):
        thumb_frames |= {c + k for k in range(-2, 3) if 0 <= c + k < n}

    t0 = time.time()
    samples, thumbs, track = pass2(path, W, H, fps_str, n, rows, shots, sched, thumb_frames)
    timings["video_pass2_adaptive"] = time.time() - t0
    log(f"[3] passada 2 adaptativa: {len(samples)}/{n} frames com análise densa "
        f"({100 * len(samples) / n:.0f}%) em {timings['video_pass2_adaptive']:.1f}s")

    shots = enrich_shots(shots, rows, samples, audio, W, fps)
    roi_x, roi_y, roi_box = interpolate_samples(n, samples, shots)
    energy = energy_map(rows, cuts, audio, W, fps)

    # ---- representação temporal FRAME 000000..N ----
    with open(out / "frames.csv", "w", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(["frame", "time", "shot", "stride", "dense", "luma", "contrast", "sat", "edges", "diff", "hist_d",
                     "content_dx", "content_dy", "rot", "zoom", "inlier", "obj_motion", "roi_x", "roi_y",
                     "track_x", "track_y", "track_conf", "audio_rms_db", "audio_onset", "energy"])
        shot_of = np.zeros(n, int)
        for i, s in enumerate(shots):
            shot_of[s["start_frame"]:s["end_frame"]] = i + 1
        for i, r in enumerate(rows):
            wr.writerow([i, f"{i / fps:.4f}", shot_of[i], int(stride[i]), int(i in samples),
                         f"{r['luma']:.4f}", f"{r['contrast']:.4f}", f"{r['sat']:.4f}", f"{r['edges']:.4f}",
                         f"{r['diff']:.4f}", f"{r['hist_d']:.4f}", f"{r['dx']:.2f}", f"{r['dy']:.2f}",
                         f"{r['rot']:.3f}", f"{r['zoom']:.4f}", f"{r['inlier']:.2f}", f"{r['obj']:.2f}",
                         f"{roi_x[i]:.4f}", f"{roi_y[i]:.4f}",
                         "" if np.isnan(track["x"][i]) else f"{track['x'][i]:.4f}",
                         "" if np.isnan(track["y"][i]) else f"{track['y'][i]:.4f}", f"{track['conf'][i]:.2f}",
                         f"{audio['rms_db_per_frame'][i]:.1f}" if audio else "",
                         f"{audio['onset_per_frame'][i]:.3f}" if audio else "", f"{energy['per_frame'][i]:.3f}"])

    # ---- contact sheets ----
    sheet_imgs, sheet_lab = [], []
    for s in shots:
        a, b = s["start_frame"], s["end_frame"]
        for k, tag in ((a, "in"), ((a + b) // 2, "mid"), (b - 1, "out")):
            if k in thumbs:
                sheet_imgs.append(thumbs[k])
                sheet_lab.append(f"{s['id']} {tag} f{k}")
    _sheet(sheet_imgs, sheet_lab, 3, out / "frames" / "shots_contact_sheet.jpg")
    crit_files = []
    for c in sorted(critical)[:40]:
        ks = [c + k for k in range(-2, 3) if c + k in thumbs]
        if len(ks) >= 3:
            fn = out / "frames" / f"critical_f{c:06d}.jpg"
            _sheet([thumbs[k] for k in ks], [f"f{k}" + (" <" if k == c else "") for k in ks], len(ks), fn)
            crit_files.append(fn.name)

    stride_hist = {int(k): int(v) for k, v in zip(*np.unique(stride, return_counts=True))}
    analysis = {
        "schema": "conde.analysis/1",
        "media": media, "n_frames": n,
        "fps": fps, "width": W, "height": H,
        "audio": audio, "cuts": cuts, "visual_flashes": flashes, "shots": shots,
        "adaptive": {"stride_histogram": stride_hist, "dense_frames": len(samples),
                     "critical_frames": sorted(int(c) for c in critical)},
        "samples": {str(k): v for k, v in sorted(samples.items())},
        "roi_per_frame": {"x": np.round(roi_x, 4).tolist(), "y": np.round(roi_y, 4).tolist(),
                          "box": np.round(roi_box, 4).tolist()},
        "track": {k: [None if (isinstance(x, float) and np.isnan(x)) else round(float(x), 4) for x in v]
                  for k, v in track.items()},
        "motion_per_frame": {"dx": [round(r["dx"], 2) for r in rows], "dy": [round(r["dy"], 2) for r in rows],
                             "obj": [round(r["obj"], 2) for r in rows], "luma": [round(r["luma"], 4) for r in rows],
                             "inlier": [round(r["inlier"], 3) for r in rows]},
        "energy": energy,
        "contact_sheets": ["frames/shots_contact_sheet.jpg"] + [f"frames/{f}" for f in crit_files],
        "timings_s": {k: round(v, 2) for k, v in timings.items()},
    }
    (out / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False))
    log(f"[6] energia: {energy['band_share']}")
    return analysis
