"""FASES 2/3/17 — análise temporal do vídeo.

Passada 1 (TODOS os frames, baixa resolução, barata):
  luma, contraste, pretos/altas, saturação, matiz dominante, bordas, alta
  frequência, diferença e distância de histograma entre frames, movimento global
  afim (LK + RANSAC: translação, rotação, escala) e movimento residual (objetos).

Passada 2 (ADAPTATIVA, resolução média):
  agenda densidade de análise por região (estável 8 · movimento 4/2 · cortes,
  impactos e flashes 1) e só nesses frames calcula fluxo denso, saliência,
  rostos, região de interesse (ROI) e espaço negativo para texto.
  Tracking do sujeito roda em todos os frames (precisa de continuidade).
"""
import cv2
import numpy as np

from ..io_ffmpeg import iter_frames, scaled_size

P1_W = 256
P2_W = 480
HAAR = (cv2.data.haarcascades + "haarcascade_frontalface_default.xml") if hasattr(cv2, "data") else None


def _face_detector():
    """Haar cascade (OpenCV 4.x). Se indisponível, devolve None e a ROI usa saliência+movimento."""
    if HAAR and hasattr(cv2, "CascadeClassifier"):
        det = cv2.CascadeClassifier(HAAR)
        if not det.empty():
            return det
    return None


def _hist(hsv):
    h = cv2.calcHist([hsv], [0, 1, 2], None, [16, 4, 4], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()


def _affine(prev_g, g):
    pts = cv2.goodFeaturesToTrack(prev_g, maxCorners=220, qualityLevel=0.01, minDistance=5)
    if pts is None or len(pts) < 8:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_g, g, pts, None, winSize=(21, 21), maxLevel=3)
    ok = st.ravel() == 1
    p0, p1 = pts[ok].reshape(-1, 2), nxt[ok].reshape(-1, 2)
    if len(p0) < 8:
        return None
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if M is None:
        return None
    inl = inl.ravel().astype(bool)
    pred = p0 @ M[:, :2].T + M[:, 2]
    resid = np.linalg.norm(p1 - pred, axis=1)
    out_res = float(np.median(resid[~inl])) if (~inl).sum() >= 3 else 0.0
    return M, float(inl.mean()), out_res, int(len(p0))


def pass1(path, src_w, src_h, fps, n_frames_hint=None):
    w, h = scaled_size(src_w, src_h, P1_W)
    sx = src_w / w
    cx, cy = w / 2.0, h / 2.0
    rows, hists, prev_g = [], [], None
    for i, rgb in enumerate(iter_frames(path, w, h, fps=fps)):
        f = rgb.astype(np.float32) / 255.0
        Y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
        g = (Y * 255).astype(np.uint8)
        hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
        S = hsv[..., 1].astype(np.float32) / 255.0
        hue_hist = np.bincount(hsv[..., 0].ravel(), weights=S.ravel(), minlength=180)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1)
        r = {
            "frame": i,
            "luma": float(Y.mean()), "contrast": float(Y.std()),
            "black": float(np.percentile(Y, 2)), "white": float(np.percentile(Y, 98)),
            "sat": float(S.mean()),
            "rgb": [float(f[..., c].mean()) for c in range(3)],
            "hue": int(np.argmax(np.convolve(hue_hist, np.ones(9) / 9, mode="same"))) * 2,
            "edges": float(np.mean(np.hypot(gx, gy)) / 255.0),
            "hf": float(cv2.Laplacian(g, cv2.CV_32F).var() / 1e4),
            "diff": 0.0, "hist_d": 0.0,
            "dx": 0.0, "dy": 0.0, "rot": 0.0, "zoom": 1.0, "inlier": 1.0, "obj": 0.0, "nfeat": 0,
        }
        hs = _hist(hsv)
        if prev_g is not None:
            r["diff"] = float(np.mean(np.abs(g.astype(np.int16) - prev_g.astype(np.int16))) / 255.0)
            r["hist_d"] = float(cv2.compareHist(hists[-1], hs, cv2.HISTCMP_BHATTACHARYYA))
            af = _affine(prev_g, g)
            if af is not None:
                M, inl, ores, nf = af
                s = float(np.hypot(M[0, 0], M[1, 0]))
                c2 = M[:, :2] @ np.array([cx, cy]) + M[:, 2]
                # deslocamento do CONTEÚDO no centro do quadro, em px da fonte
                r.update(dx=float((c2[0] - cx) * sx), dy=float((c2[1] - cy) * sx),
                         rot=float(np.degrees(np.arctan2(M[1, 0], M[0, 0]))), zoom=s,
                         inlier=inl, obj=float(ores * sx), nfeat=nf)
            else:
                r.update(inlier=0.0)
        rows.append(r)
        hists.append(hs)
        prev_g = g
    return rows, np.array(hists), (w, h)


def schedule(n, rows, critical, src_w):
    """FASE 3 — densidade adaptativa: retorna stride por frame e o conjunto agendado."""
    speed = np.array([np.hypot(r["dx"], r["dy"]) / src_w for r in rows])  # fração da largura/frame
    stride = np.full(n, 8)
    stride[speed > 0.004] = 4
    stride[speed > 0.012] = 2
    for c in critical:
        for k in range(c - 2, c + 3):
            if 0 <= k < n:
                stride[k] = 1
    sched, k = set(), 0
    while k < n:
        sched.add(k)
        k += int(stride[k])
    sched.update(c for c in critical if 0 <= c < n)
    return stride, sched


def _spectral_saliency(g):
    small = cv2.resize(g, (64, 64 * g.shape[0] // g.shape[1] or 1)).astype(np.float32)
    F = np.fft.fft2(small)
    logA = np.log(np.abs(F) + 1e-6)
    resid = logA - cv2.blur(logA, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(resid + 1j * np.angle(F)))) ** 2
    sal = cv2.GaussianBlur(sal, (0, 0), 2.5)
    sal = cv2.resize(sal, (g.shape[1], g.shape[0]))
    return sal / (sal.max() + 1e-12)


def _negative_space(score_map, roi, cols=4, rows_=3):
    h, w = score_map.shape
    cells = []
    for r in range(rows_):
        for c in range(cols):
            y0, y1 = r * h // rows_, (r + 1) * h // rows_
            x0, x1 = c * w // cols, (c + 1) * w // cols
            busy = float(score_map[y0:y1, x0:x1].mean())
            box = (x0 / w, y0 / h, x1 / w, y1 / h)
            ov = _overlap(box, roi) if roi else 0.0
            cells.append({"cell": [c, r], "box": [round(v, 3) for v in box],
                          "empty": round(max(0.0, 1 - busy - ov), 3)})
    cells.sort(key=lambda d: -d["empty"])
    return cells[:4]


def _overlap(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / area if area else 0.0


def _box_track_step(prev_g, g, box, global_M):
    """Um passo de tracking do sujeito: LK com verificação ida-e-volta + afim parcial.

    Se parte dos pontos da caixa se move diferente da câmera (fundo), usa só esses
    (o sujeito); senão usa todos (sujeito solidário ao cenário)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    mask = np.zeros_like(prev_g)
    mask[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 255
    pts = cv2.goodFeaturesToTrack(prev_g, maxCorners=160, qualityLevel=0.005, minDistance=3, mask=mask)
    if pts is None or len(pts) < 6:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev_g, g, pts, None, winSize=(15, 15), maxLevel=3)
    back, st2, _ = cv2.calcOpticalFlowPyrLK(g, prev_g, nxt, None, winSize=(15, 15), maxLevel=3)
    fb = np.linalg.norm(back.reshape(-1, 2) - pts.reshape(-1, 2), axis=1)
    ok = (st.ravel() == 1) & (st2.ravel() == 1) & (fb < 1.0)
    p0, p1 = pts.reshape(-1, 2)[ok], nxt.reshape(-1, 2)[ok]
    if len(p0) < 6:
        return None
    if global_M is not None:
        bg_pred = p0 @ global_M[:, :2].T + global_M[:, 2]
        fg = np.linalg.norm(p1 - bg_pred, axis=1) > 1.5
        if fg.sum() >= 4 and fg.mean() >= 0.05:
            p0, p1 = p0[fg], p1[fg]
    if len(p0) < 10:   # poucos pontos: translação mediana (mais estável que afim)
        d = np.median(p1 - p0, axis=0)
        spread = float(np.median(np.linalg.norm((p1 - p0) - d, axis=1)))
        M = np.array([[1.0, 0, d[0]], [0, 1.0, d[1]]])
        return M, float(np.clip(1 - spread, 0, 1)), int(len(p0))
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=1.0)
    if M is None:
        return None
    return M, float(inl.mean()), int(inl.sum())


def _core_box(smp, w, h):
    """Caixa de tracking: centrada no centro da ROI, com 60% do tamanho (núcleo do sujeito)."""
    r = smp["roi"]
    cx, cy = smp["roi_center"][0] * w, smp["roi_center"][1] * h
    bw, bh = max(24, 0.6 * (r[2] - r[0]) * w), max(24, 0.6 * (r[3] - r[1]) * h)
    return [cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2]


def pass2(path, src_w, src_h, fps, n, rows, shots, sched, thumb_frames):
    w, h = scaled_size(src_w, src_h, P2_W)
    face_det = _face_detector()
    shot_of = np.zeros(n, int)
    for si, s in enumerate(shots):
        shot_of[s["start_frame"]:s["end_frame"]] = si
    samples, thumbs = {}, {}
    track = {"x": np.full(n, np.nan), "y": np.full(n, np.nan), "scale": np.full(n, np.nan),
             "rot": np.full(n, np.nan), "conf": np.zeros(n)}
    prev_g, box, acc_s, acc_r, cur_shot = None, None, 1.0, 0.0, -1
    last_faces, last_face_i = [], -10 ** 9
    fps_f = float(eval(str(fps))) if isinstance(fps, str) else float(fps)
    shot_starts = {s["start_frame"] for s in shots}
    for i, rgb in enumerate(iter_frames(path, w, h, fps=fps)):
        if i >= n:
            break
        g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        if i in thumb_frames:
            tw, th = scaled_size(w, h, 192)
            thumbs[i] = cv2.resize(rgb, (tw, th), interpolation=cv2.INTER_AREA)
        need_roi = i in sched or i in shot_starts or box is None
        roi = None
        if need_roi:
            sal = _spectral_saliency(g)
            motion = np.zeros_like(sal)
            g_half = cv2.resize(g, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
            if prev_g is not None and i not in shot_starts:
                pg_half = cv2.resize(prev_g, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
                flow = cv2.calcOpticalFlowFarneback(pg_half, g_half, None, 0.5, 3, 11, 3, 5, 1.1, 0)
                r = rows[i]
                gdx, gdy = r["dx"] / (src_w / (w // 2)), r["dy"] / (src_w / (w // 2))
                res = np.hypot(flow[..., 0] - gdx, flow[..., 1] - gdy)
                res = cv2.resize(res, (w, h))
                motion = np.clip(res / (np.percentile(res, 99) + 1e-6), 0, 1)
            if face_det is not None and (i in shot_starts or i - last_face_i >= max(1, int(round(float(fps_f) / 4)))):
                fh_ = face_det.detectMultiScale(g_half, scaleFactor=1.15, minNeighbors=6, minSize=(16, 16))
                last_faces = [tuple(int(v * 2) for v in b) for b in fh_] if len(fh_) else []
                last_face_i = i
            faces = last_faces if face_det is not None else []
            score = 0.55 * sal + 0.45 * motion
            if len(faces):
                fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
                cxn, cyn = (fx + fw / 2) / w, (fy + fh / 2) / h
                bw, bh = min(1, fw * 2.4 / w), min(1, fh * 2.8 / h)
                roi = [max(0, cxn - bw / 2), max(0, cyn - bh / 2.8), min(1, cxn + bw / 2), min(1, cyn + bh * 1.8 / 2.8)]
                kind = "face"
            else:
                thr = np.percentile(score, 93)
                ys, xs = np.nonzero(score >= thr)
                wts = score[ys, xs]
                cxn, cyn = float(np.average(xs, weights=wts) / w), float(np.average(ys, weights=wts) / h)
                x0, x1 = np.percentile(xs, [10, 90]) / w
                y0, y1 = np.percentile(ys, [10, 90]) / h
                roi = [float(max(0, min(x0, cxn - 0.08))), float(max(0, min(y0, cyn - 0.08))),
                       float(min(1, max(x1, cxn + 0.08))), float(min(1, max(y1, cyn + 0.08)))]
                kind = "saliency+motion"
            edges = cv2.Canny(g, 60, 160).astype(np.float32) / 255.0
            busy = np.clip(0.5 * score + 0.5 * cv2.blur(edges, (9, 9)) * 3, 0, 1)
            samples[i] = {
                "roi": [round(v, 4) for v in roi], "roi_center": [round(cxn, 4), round(cyn, 4)],
                "roi_kind": kind, "faces": int(len(faces)),
                "object_motion": round(float(motion.mean()), 4),
                "text_zones": _negative_space(busy, roi),
            }
        # ---- tracking do sujeito (todo frame, dentro do shot) ----
        si = int(shot_of[i])
        if si != cur_shot or box is None:
            cur_shot = si
            smp = samples.get(i, {})
            if smp.get("roi"):
                box = _core_box(smp, w, h)
                acc_s, acc_r = 1.0, 0.0
        elif prev_g is not None:
            r = rows[i]
            gM = None
            if r["inlier"] > 0:
                th = np.radians(r["rot"])
                gM = np.array([[r["zoom"] * np.cos(th), -r["zoom"] * np.sin(th), 0],
                               [r["zoom"] * np.sin(th), r["zoom"] * np.cos(th), 0]])
                c = np.array([w / 2, h / 2])
                gM[:, 2] = c + np.array([r["dx"], r["dy"]]) / (src_w / w) - gM[:, :2] @ c
            st = _box_track_step(prev_g, g, box, gM)
            if st is None:
                track["conf"][i] = 0.0
                smp = samples.get(i, {})
                if smp.get("roi"):   # re-inicializa pela ROI quando perde
                    box = _core_box(smp, w, h)
            else:
                M, inl, ninl = st
                ctr = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])
                nc = M[:, :2] @ ctr + M[:, 2]
                s = float(np.clip(np.hypot(M[0, 0], M[1, 0]), 0.9, 1.1))
                hw_, hh_ = (box[2] - box[0]) / 2 * s, (box[3] - box[1]) / 2 * s
                box = [nc[0] - hw_, nc[1] - hh_, nc[0] + hw_, nc[1] + hh_]
                acc_s *= s
                acc_r += float(np.degrees(np.arctan2(M[1, 0], M[0, 0])))
                track["conf"][i] = round(inl * min(1.0, ninl / 15), 3)
        if box is not None:
            track["x"][i] = (box[0] + box[2]) / 2 / w
            track["y"][i] = (box[1] + box[3]) / 2 / h
            track["scale"][i] = acc_s
            track["rot"][i] = acc_r
            if track["conf"][i] == 0 and i in shot_starts:
                track["conf"][i] = 1.0
        prev_g = g
    return samples, thumbs, track


def interpolate_samples(n, samples, shots):
    """ROI contínua por frame: interpola amostras DENTRO de cada shot (nunca através de cortes)."""
    cx, cy = np.full(n, 0.5), np.full(n, 0.5)
    box = np.tile(np.array([0.3, 0.3, 0.7, 0.7]), (n, 1))
    for s in shots:
        a, b = s["start_frame"], s["end_frame"]
        ks = sorted(k for k in samples if a <= k < b)
        if not ks:
            continue
        fr = np.arange(a, b)
        cx[a:b] = np.interp(fr, ks, [samples[k]["roi_center"][0] for k in ks])
        cy[a:b] = np.interp(fr, ks, [samples[k]["roi_center"][1] for k in ks])
        for j in range(4):
            box[a:b, j] = np.interp(fr, ks, [samples[k]["roi"][j] for k in ks])
    return cx, cy, box
