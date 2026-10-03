"""Sistemas do motor: câmera (15/16), zoom-cuts, luz (27), glitch (30), partículas (26),
gráficos (22), tipografia (20/21), cor (31), reação ao áudio (32), marcadores, montagem."""
import colorsys

import numpy as np
from scipy.ndimage import gaussian_filter1d

from ..generate import expressions as X
from .curves import Key, pulse, validate_keys
from .engine import CATEGORIES

STACK = ["CTRL_MASTER", "CTRL_CAMERA", "CTRL_AUDIO", "CTRL_VFX", "MARKERS_AUDIO", "MARKERS_IMPACT", "MARKERS_SECTIONS",
         "TEXT_", "GFX_", "VFX_PARTICLES_", "VFX_FLASH", "VFX_GLOW", "COLOR_VIGNETTE", "COLOR_LOOK", "COLOR_CORR_",
         "VFX_GLITCH_", "VFX_DISTORT_", "VFX_DIRBLUR", "VFX_RGB_SPLIT_", "VIDEO", "TRACK_", "CAMERA_FOCUS",
         "CAMERA_SHAKE", "CAMERA_ZOOM", "CAMERA_ROTATION", "CAMERA_POSITION", "CAMERA_MASTER", "AUDIO_ORIGINAL"]


def _slider(eng, layer, name, value):
    eng.effect(layer, "ADBE Slider Control", name, {"ADBE Slider Control-0001": value})


# ------------------------------------------------------------------ camadas base
def base_layers(eng):
    W, H, S = eng.W, eng.H, eng.S
    cx, cy = W / 2.0, H / 2.0
    for n in ("CTRL_MASTER", "CTRL_CAMERA", "CTRL_AUDIO", "CTRL_VFX"):
        eng.layer(n, "null", guide=True, group="CONTROLLERS", comment="Controlador central (FASE 34)")
    for k, v in eng.cfg["controllers"].items():
        _slider(eng, "CTRL_MASTER", k, v)
    _slider(eng, "CTRL_CAMERA", "DRIFT_SCALE", 100.0)
    _slider(eng, "CTRL_CAMERA", "CUT_ZOOM", 100.0)
    _slider(eng, "CTRL_CAMERA", "AMBIENT_SHAKE", 0.0)
    for n in ("BASS", "MID", "TREBLE", "AMP", "GATE_BASS_TO_ZOOM", "GATE_TREBLE_TO_GLOW"):
        _slider(eng, "CTRL_AUDIO", n, 0.0)
    _slider(eng, "CTRL_VFX", "RGB_SPLIT_PX", 0.0)
    for n in ("MARKERS_AUDIO", "MARKERS_IMPACT", "MARKERS_SECTIONS"):
        eng.layer(n, "null", guide=True, group="CONTROLLERS", comment="Marcadores (dados que dirigem expressions)")
    # FASE 15 — hierarquia de câmera: MASTER > POSITION > ROTATION > ZOOM > SHAKE > (vídeo)
    eng.layer("CAMERA_MASTER", "null", transform={"anchor": [0, 0], "position": [0, 0]}, group="CAMERA",
              comment="Reenquadramento global")
    eng.layer("CAMERA_POSITION", "null", parent="CAMERA_MASTER", group="CAMERA",
              transform={"anchor": [0, 0], "position": [0, 0], "separate_xy": True},
              comment="Pans/whips (X e Y separados → easing 1D previsível)")
    eng.layer("CAMERA_ROTATION", "null", parent="CAMERA_POSITION", group="CAMERA",
              transform={"anchor": [cx, cy], "position": [cx, cy]}, comment="Roll / rotation kicks")
    eng.layer("CAMERA_ZOOM", "null", parent="CAMERA_ROTATION", group="CAMERA",
              transform={"anchor": [cx, cy], "position": [cx, cy]},
              expressions={"transform.scale": X.zoom_scale_js(), "transform.anchor": X.focus_js(),
                           "transform.position": X.focus_js()},
              comment="Punches (keys) × drift × zoom-cut × grave; centro = CAMERA_FOCUS")
    eng.layer("CAMERA_SHAKE", "null", parent="CAMERA_ZOOM", group="CAMERA",
              transform={"anchor": [cx, cy], "position": [cx, cy]},
              expressions={"transform.position": X.shake_position_js(), "transform.rotation": X.shake_rotation_js()},
              comment="Shake procedural com decaimento (FASE 16)")
    eng.layer("CAMERA_FOCUS", "null", transform={"anchor": [0, 0], "position": [cx, cy]}, group="CAMERA",
              comment="Ponto de foco do zoom = sujeito rastreado")
    eng.layer("VIDEO", "video", parent="CAMERA_SHAKE", motion_blur=True, group="FOOTAGE",
              transform={"anchor": [cx, cy], "position": [cx, cy]},
              comment="PRE_SOURCE_REMAP (time remap) — bordas espelhadas para shake/whip")
    eng.effect("VIDEO", "ADBE Tile", "MOTION_TILE", {"4": 230.0, "5": 230.0, "6": 1})
    eng.layer("VFX_DIRBLUR", "adjustment", group="VFX/MOTION", comment="Blur direcional de whips e golpes",
              expressions={"effect:DIRBLUR:ADBE Motion Blur-0002": "value * " + X.k_expr("VFX_INTENSITY")})
    eng.effect("VFX_DIRBLUR", "ADBE Motion Blur", "DIRBLUR", {"ADBE Motion Blur-0001": 90.0, "ADBE Motion Blur-0002": 0.0})
    eng.layer("VFX_FLASH", "solid", color=[1, 1, 1], blend="ADD", group="VFX/LIGHT",
              transform={"opacity": 0.0}, expressions={"transform.opacity": X.scaled_js("VFX_INTENSITY")},
              comment="Flash de luz (tingido pela cena)")
    eng.effect("VFX_FLASH", "ADBE Tint", "TINT", {"1": [0, 0, 0], "2": [1, 1, 1], "3": 100.0})
    eng.layer("VFX_GLOW", "adjustment", group="VFX/LIGHT", comment="Glow: eventos de luz + agudos (gate)",
              expressions={"effect:GLOW:4": X.treble_glow_js()})
    eng.effect("VFX_GLOW", "ADBE Glo2", "GLOW", {"2": 72.0, "3": 40.0 * S, "4": 0.0})
    eng.layer("AUDIO_ORIGINAL", "audio", group="AUDIO", comment="Áudio original contínuo (dirige a edição)")


# ------------------------------------------------------------------ câmera
def camera(eng):
    W, H, S = eng.W, eng.H, eng.S
    tr = eng.track
    keys_f = []
    step = eng.k30(4)
    for s in eng.shots_out:
        a, b = s["out_start"], s["out_end"]
        srcs = [eng.v_idx(f) for f in range(a, b)]
        confs = [tr["conf"][i] or 0 for i in srcs]
        use_track = np.mean(confs) >= 0.5 and all(tr["x"][i] is not None for i in srcs)
        xs = np.array([(tr["x"][i] if use_track else eng.roi["x"][i]) for i in srcs]) * W
        ys = np.array([(tr["y"][i] if use_track else eng.roi["y"][i]) for i in srcs]) * H
        if len(xs) > 4:
            xs = gaussian_filter1d(xs, eng.fps * 0.4, mode="nearest")
            ys = gaussian_filter1d(ys, eng.fps * 0.4, mode="nearest")
        fr = list(range(a, b, step))
        if fr[-1] != b - 1:
            fr.append(b - 1)
        for j, f in enumerate(fr):
            last = j == len(fr) - 1
            keys_f.append(Key(f, [round(float(xs[f - a]), 2), round(float(ys[f - a]), 2)],
                              interp_in="LINEAR", interp_out="HOLD" if last else "LINEAR"))
        s["focus_source"] = "track" if use_track else "roi"
    eng.add_keys("CAMERA_FOCUS", "transform.position", keys_f)
    # drift lento (respiro e energia baixa/média)
    drift = []
    for s in eng.shots_out:
        a, b = s["out_start"], s["out_end"]
        e = float(np.mean(eng.E[a:b]))
        breath = np.mean([eng.in_breath(f) for f in range(a, b)]) > 0.5
        if (e < 0.6 or breath) and b - a >= eng.fr(1.0):
            d = 3.0 + 3.0 * min(1.0, (b - a) / eng.fps / 6)
            drift += [Key(a, 100.0, out_speed=0.0, out_infl=0.5),
                      Key(b - 1, 100 + d, in_speed=0.0, in_infl=0.5, interp_out="HOLD")]
            eng.event("CAMERA", a, b, recipe="DRIFT_PUSH_IN", action=["Push-in lento"],
                      detector=f"shot {s['id']} energia {e:.2f}" + (" (respiro)" if breath else ""),
                      params={"scale": [100, round(100 + d, 2)], "center": s["focus_source"]}, intensity=round(0.15 + 0.1 * e, 3),
                      easing="ease in/out (influência 50%)", audio_sync=None, dependencies=["CTRL_CAMERA.DRIFT_SCALE", "CAMERA_FOCUS"],
                      priority=4, source={"shot": s["id"]},
                      why="Trecho calmo: câmera estável com aproximação lenta mantém vida sem competir com os golpes.",
                      expected="Respiro com profundidade; contraste para as partes intensas.")
        else:
            drift += [Key(a, 100.0, interp_in="HOLD", interp_out="HOLD")]
    eng.slider_keys("CTRL_CAMERA", "DRIFT_SCALE", drift)
    # shake ambiente por faixa (nunca em respiro)
    amp = {"LOW": 0.0, "MEDIUM": 0.5, "HIGH": 1.1, "EXTREME": 1.8}
    vals = np.array([0.0 if eng.in_breath(f) else amp[eng.band[f]] * S for f in range(eng.n_out)])
    vals = np.round(gaussian_filter1d(vals, eng.fps * 0.2), 2)
    keys = [Key(0, float(vals[0]), interp_in="LINEAR", interp_out="LINEAR")]
    for f in range(eng.k30(3), eng.n_out, eng.k30(3)):
        if abs(vals[f] - keys[-1].v) >= 0.05:
            keys.append(Key(f, float(vals[f]), interp_in="LINEAR", interp_out="LINEAR"))
    eng.slider_keys("CTRL_CAMERA", "AMBIENT_SHAKE", keys)


def zoom_cuts(eng):
    """Punch-in cuts: em plano estável e longo, degraus de escala no beat simulam cortes."""
    steps = [Key(0, 100.0, interp_in="HOLD", interp_out="HOLD")]
    if eng.mode == "dialogue":
        for i, c in enumerate([c for c in eng.cuts if c["type"] == "JUMP_CUT"]):
            v = 108.0 if i % 2 == 0 else 100.0
            steps.append(Key(c["shown_at"], v, interp_in="HOLD", interp_out="HOLD"))
        if len(steps) > 1:
            eng.event("ZOOM", steps[1].f, steps[-1].f + 1, recipe="JUMP_CUT_ZOOM", action=["Zoom alternado nos jump cuts"],
                      detector="cortes de silêncio", params={"levels": [100, 108]}, intensity=0.3, easing="HOLD",
                      audio_sync=None, dependencies=["CTRL_CAMERA.CUT_ZOOM"], priority=2,
                      why="Alternar o enquadramento esconde o salto dos jump cuts de fala.",
                      expected="Cortes de pausa sem sensação de erro.")
    for s in eng.shots_out:
        a, b = s["out_start"], s["out_end"]
        band = [eng.band[f] for f in range(a, b)]
        hi = np.mean([x in ("MEDIUM", "HIGH", "EXTREME") for x in band])
        if s["motion_level"] != "LOW" or (b - a) / eng.fps < 2.0 or hi < 0.7 or np.mean([eng.in_breath(f) for f in range(a, b)]) > 0.3:
            continue
        beats = [d for d in eng.downbeats_out if a + eng.k30(6) <= d <= b - eng.k30(10)]
        if len(beats) < 2:
            beats = [d for d in eng.beats_out if a + eng.k30(6) <= d <= b - eng.k30(10)][::2]
        levels = [112.0, 100.0, 118.0, 100.0]
        made = []
        for i, d in enumerate(beats[:4]):
            steps.append(Key(d, levels[i % 4], interp_in="HOLD", interp_out="HOLD"))
            made.append(d)
        if made:
            steps.append(Key(b, 100.0, interp_in="HOLD", interp_out="HOLD"))
            eng.event("ZOOM", made[0], b, recipe="PUNCH_IN_CUTS", action=["Zoom-cuts no downbeat"],
                      detector=f"plano estável e longo ({s['duration_s']:.1f}s) em energia média/alta",
                      params={"frames": made, "levels": levels[:len(made)]}, intensity=0.45, easing="HOLD (degrau = corte)",
                      audio_sync={"type": "DOWNBEAT", "frame": made[0], "offset_frames": 0},
                      dependencies=["CTRL_CAMERA.CUT_ZOOM"], priority=4, source={"shot": s["id"]},
                      why="Plano parado demais para a energia do trecho: cortes de enquadramento no downbeat criam ritmo sem trocar de plano.",
                      expected="Ritmo visual musical em material estático.")
    steps.sort(key=lambda k: k.f)
    dedup = []
    for k in steps:
        if dedup and dedup[-1].f == k.f:
            dedup[-1] = k
        else:
            dedup.append(k)
    eng.slider_keys("CTRL_CAMERA", "CUT_ZOOM", dedup)


# ------------------------------------------------------------------ luz / glitch
def light_events(eng):
    for fl in eng.an["visual_flashes"]:
        f = int(round(fl["frame"] * eng.fps / eng.src_fps)) if eng.mode != "dialogue" else eng.a2o(fl["frame"])
        if f is None or not eng.claim(["glow"], f - 1, f + eng.k30(10), "light"):
            continue
        # a luz respeita a cena: quanto mais a própria imagem já estoura, menos bloom se adiciona
        sh = eng.shots_out[eng._shot_at(f)]
        peak_luma = min(1.0, sh["luma"] + max(0.0, fl["luma_jump"]))
        g = round(float(2.5 * np.clip(1.0 - peak_luma, 0.15, 1.0)), 2)
        eng.add_keys("VFX_GLOW", "effect:GLOW:4", pulse(f, g, 1, eng.k30(10), eng.fps))
        eng.event("FLASH", f, f + eng.k30(10), recipe="LIGHT_BLOOM", action=["Glow acompanhando a luz da cena"],
                  detector=f"flash na própria imagem (salto de luma {fl['luma_jump']:+.2f}, não é corte)",
                  params={"glow_intensity": [0, g, 0], "threshold_pct": 72, "scene_peak_luma": round(peak_luma, 3)}, intensity=0.5,
                  easing="ataque 1 frame, decaimento exponencial", audio_sync=None,
                  dependencies=["VFX_GLOW"], priority=6,
                  why="A luz já existe na cena: o bloom amplia o evento real em vez de inventar uma luz, "
                      f"com ganho reduzido porque a imagem já chega a luma {peak_luma:.2f}.",
                  expected="Flash orgânico, coerente com a iluminação.")


def glitch_events(eng):
    gc = eng.cfg["glitch"]
    max_frames = gc["max_share"] * eng.n_out
    used = sum(e["duration_frames"] for e in eng.events if e["category"] == "GLITCH")
    L = eng.k30(gc["max_len_frames_30"])
    for c in sorted(eng.cands, key=lambda c: -c["intensity"]):
        if used + L > max_frames:
            break
        f = c["frame"]
        if c["band"] != "EXTREME" or "SNARE" not in c["types"] or eng.in_breath(f):
            continue
        if not eng.free("glitch", f - L, f + 2 * L) or not eng.free("time", f - 2, f + L):
            continue
        busy = [e for e in eng.events if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX", "TRANSITION", "FREEZE")
                and e["start_frame"] - 2 <= f <= e["end_frame"]]
        if busy:
            eng.decisions.append({"system": "GLITCH", "frame": f, "decision": f"descartado: já há {busy[0]['category']} "
                                  f"({busy[0].get('recipe')}) neste golpe — no máximo 2 sistemas de VFX simultâneos"})
            continue
        eng._glitch(f, f + L, c["intensity"], "caixa em pico de energia",
                    {"audio_sync": {"type": "SNARE", "frame": f, "offset_frames": 0}, "scores": c["scores"]})
        used += L


# ------------------------------------------------------------------ partículas
def particles(eng):
    S = eng.S
    pool = [c for c in eng.cands if "DROP" in c["types"]] or [c for c in eng.cands if c["intensity"] >= 0.85]
    budget = max(1, int(eng.n_out / eng.fps / 15))
    for n, c in enumerate(sorted(pool, key=lambda c: -c["intensity"])[:budget]):
        f = c["frame"]
        if eng.in_breath(f):
            continue
        si = eng.v_idx(f)
        x, y = eng.roi["x"][si] * eng.W, eng.roi["y"][si] * eng.H
        dx, dy = eng.an["motion_per_frame"]["dx"][si], eng.an["motion_per_frame"]["dy"][si]
        base = np.arctan2(dy, dx) if np.hypot(dx, dy) > 1 else -np.pi / 2
        rng = np.random.default_rng(eng.cfg["seed"] + f)
        N = 34
        groups = []
        life_max = 0
        col = eng.accent
        for i in range(N):
            ang = base + rng.normal(0, 0.9) if rng.random() < 0.6 else rng.uniform(0, 2 * np.pi)
            sp = rng.uniform(300, 1100) * S
            life = rng.uniform(0.45, 0.9)
            size = rng.uniform(8, 22) * S
            life_max = max(life_max, life)
            vx, vy = sp * np.cos(ang), sp * np.sin(ang)
            mix = rng.uniform(0, 1)
            color = [round(1 - mix * (1 - ch), 3) for ch in col]
            groups.append({"name": f"P{i:02d}", "items": [{"type": "ellipse", "size": [round(size, 1)] * 2},
                                                          {"type": "fill", "color": color}],
                           "physics": {"vx": round(vx, 1), "vy": round(vy, 1), "g": round(900 * S, 1), "drag": 1.8,
                                       "life": round(life, 3)}})
        name = f"VFX_PARTICLES_{n + 1:03d}"
        out = f + eng.fr(life_max) + 1
        eng.layer(name, "shape", **{"in": f, "out": out, "parent": "CAMERA_SHAKE", "blend": "ADD", "group": "VFX/PARTICLES",
                                    "transform": {"position": [round(x, 1), round(y, 1)]},
                                    "shape": {"groups": groups, "particles": True},
                                    "comment": "Faíscas procedurais (física determinística, sem plugin)"})
        eng.event("PARTICLE", f, out, recipe="SPARK_BURST", action=["Explosão de partículas no sujeito"],
                  detector="/".join(sorted(c["types"])) + f" intensidade {c['intensity']}",
                  params={"count": N, "speed_px_s": [round(300 * S), round(1100 * S)], "gravity_px_s2": round(900 * S),
                          "drag": 1.8, "life_s": [0.45, 0.9], "size_px": [round(8 * S, 1), round(22 * S, 1)],
                          "direction_deg": round(float(np.degrees(base)), 1), "origin_px": [round(x), round(y)]},
                  intensity=c["intensity"], easing="física: arrasto exponencial + gravidade",
                  audio_sync={"type": "/".join(sorted(c["types"])), "frame": f, "offset_frames": 0},
                  dependencies=[name, "CTRL_MASTER.PARTICLE_INTENSITY"], priority=7,
                  why="Maior golpe do vídeo: partículas só aqui, nascendo do sujeito e seguindo a direção do movimento.",
                  expected="Energia física concentrada no ápice.")


# ------------------------------------------------------------------ gráficos + texto
def _accent(eng):
    best = max(eng.shots_out, key=lambda s: s["sat"])
    h, l, s = colorsys.rgb_to_hls(*best["mean_rgb"])
    r, g, b = colorsys.hls_to_rgb(h, 0.6, 0.85)
    return [round(r, 3), round(g, 3), round(b, 3)]


def _track_null(eng, shot):
    name = f"TRACK_{shot['id']}"
    if name in eng.layers:
        return name
    a, b = shot["out_start"], shot["out_end"]
    vals = []
    for f in range(a, b):
        si = eng.v_idx(f)
        x = eng.track["x"][si] if eng.track["x"][si] is not None else eng.roi["x"][si]
        y = eng.track["y"][si] if eng.track["y"][si] is not None else eng.roi["y"][si]
        vals.append([round(x * eng.W, 2), round(y * eng.H, 2)])
    eng.layer(name, "null", parent="CAMERA_SHAKE", group="TRACKING", **{"in": a, "out": b},
              transform={"anchor": [0, 0]}, baked={"transform.position": {"start": a, "values": vals}},
              comment="Track 2D do sujeito (LK + RANSAC, calculado no pipeline)")
    confs = [eng.track["conf"][eng.v_idx(f)] or 0 for f in range(a, b)]
    eng.event("TRACKING", a, b, recipe="POINT_TRACK", action=["Track 2D do sujeito → null"],
              detector="ROI (saliência + movimento residual) no início do shot",
              params={"frames": b - a, "mean_confidence": round(float(np.mean(confs)), 3),
                      "low_confidence_frames": int(sum(1 for c in confs if c < 0.3))},
              intensity=0.0, easing="—", audio_sync=None, dependencies=[name], priority=1, source={"shot": shot["id"]},
              why="Gráficos que acompanham o sujeito precisam de posição real por frame.",
              expected="Brackets presos ao sujeito, sem deslizar.")
    return name


def _brackets(eng, a, b, shot, why):
    name = f"GFX_BRACKETS_{a:05d}"
    tn = _track_null(eng, shot)
    si = eng.v_idx(a)
    box = eng.roi["box"][si]
    bw = max(0.12, min(0.5, box[2] - box[0])) * eng.W * 0.9
    bh = max(0.12, min(0.6, box[3] - box[1])) * eng.H * 0.9
    L = 0.22 * min(bw, bh)
    corners = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        px, py = sx * bw / 2, sy * bh / 2
        corners.append({"name": f"C{len(corners)}", "items": [
            {"type": "path", "points": [[px, py - sy * L], [px, py], [px - sx * L, py]], "closed": False},
            {"type": "stroke", "color": eng.accent, "width": round(max(2.0, 4 * eng.S), 1)},
            {"type": "trim"}]})
    eng.layer(name, "shape", **{"in": a, "out": b, "parent": "CAMERA_SHAKE", "group": "GRAPHICS",
                                "shape": {"groups": corners},
                                "expressions": {"transform.position": X.bracket_follow_js(tn)},
                                "comment": "Brackets HUD presos ao track"})
    k_in = eng.k30(6)
    for g in corners:
        eng.add_keys(name, f"shape:{g['name']}:trim.end", [Key(a, 0.0, out_speed=0.0, out_infl=0.1),
                                                           Key(a + k_in, 100.0, in_speed=0.0, in_infl=0.8)])
    eng.add_keys(name, "transform.scale", [Key(a, [125.0, 125.0], out_speed=0.0, out_infl=0.1),
                                           Key(a + k_in, [100.0, 100.0], in_speed=0.0, in_infl=0.85)])
    eng.add_keys(name, "transform.opacity", [Key(a, 100.0, interp_out="HOLD"), Key(b - eng.k30(4), 100.0, out_speed=0.0, out_infl=0.3),
                                             Key(b, 0.0, in_speed=0.0, in_infl=0.5)])
    eng.event("GRAPHIC", a, b, recipe="BRACKETS", action=["Brackets rastreados", "Trim Paths draw-on", "Scale pop"],
              detector=why, params={"size_px": [round(bw), round(bh)], "stroke_px": round(max(2.0, 4 * eng.S), 1),
                                    "color": eng.accent, "track": tn},
              intensity=0.5, easing="draw-on 6f (ease-out forte)", audio_sync={"type": "HIT", "frame": a, "offset_frames": 0},
              dependencies=[name, tn], priority=5, why="Dirige o olhar para o sujeito no momento-chave (hierarquia: sujeito primeiro).",
              expected="Leitura imediata de onde olhar.")


def _ring(eng, f, shot):
    si = eng.v_idx(f)
    x, y = eng.roi["x"][si] * eng.W, eng.roi["y"][si] * eng.H
    name = f"GFX_RING_{f:05d}"
    d = eng.k30(12)
    eng.layer(name, "shape", **{"in": f, "out": f + d, "parent": "CAMERA_SHAKE", "group": "GRAPHICS",
                                "transform": {"position": [round(x, 1), round(y, 1)]},
                                "shape": {"groups": [{"name": "RING", "items": [{"type": "ellipse", "size": [10, 10]},
                                                                                {"type": "stroke", "color": eng.accent, "width": 10}]}]},
                                "comment": "Anel de impacto (linguagem gráfica)"})
    R = 0.55 * eng.H
    eng.add_keys(name, "shape:RING:ellipse.size", [Key(f, [10.0, 10.0], out_speed=4 * R / (d / eng.fps), out_infl=0.1),
                                                   Key(f + d, [R, R], in_speed=0.0, in_infl=0.7)])
    eng.add_keys(name, "shape:RING:stroke.width", [Key(f, 14 * eng.S, out_speed=0.0, out_infl=0.3), Key(f + d, 0.0, in_speed=0.0, in_infl=0.5)])
    eng.add_keys(name, "transform.opacity", [Key(f, 100.0, out_speed=0.0, out_infl=0.4), Key(f + d, 0.0, in_speed=0.0, in_infl=0.4)])
    eng.event("GRAPHIC", f, f + d, recipe="RING_BURST", action=["Anel expandindo do sujeito"],
              detector="impacto grande com sujeito", params={"radius_px": round(R), "color": eng.accent},
              intensity=0.7, easing="expansão rápida, desaceleração forte",
              audio_sync={"type": "HIT", "frame": f, "offset_frames": 0}, dependencies=[name], priority=7,
              why="Golpe grande: a onda circular materializa a energia a partir do sujeito.", expected="Acento gráfico coerente.")


def _place_text(eng, f, text, size_rel):
    si = eng.v_idx(f)
    shot = eng.shots_out[eng._shot_at(f)]
    ks = [int(k) for k in eng.an["samples"]]
    s0 = shot["start_frame"]
    s1 = shot["end_frame"]
    inside = [k for k in ks if s0 <= k < s1] or ks
    k = min(inside, key=lambda k: abs(k - si))
    smp = eng.an["samples"][str(k)]
    roi = smp["roi"]
    zones = [z for z in smp["text_zones"] if _ov(z["box"], roi) < 0.1] or smp["text_zones"]
    z = zones[0]
    x0, y0, x1, y1 = z["box"]
    cols = 4
    col = z["cell"][0]
    size = size_rel * eng.H
    est_w = len(text) * size * 0.62
    max_w = 0.88 * eng.W
    if est_w > max_w:
        size *= max_w / est_w
        est_w = max_w
    if col == 0:
        just, x = "LEFT", max(0.06 * eng.W, x0 * eng.W + 0.02 * eng.W)
        x = min(x, 0.94 * eng.W - est_w)
    elif col == cols - 1:
        just, x = "RIGHT", min(0.94 * eng.W, x1 * eng.W - 0.02 * eng.W)
        x = max(x, 0.06 * eng.W + est_w)
    else:
        just, x = "CENTER", (x0 + x1) / 2 * eng.W
        x = float(np.clip(x, 0.06 * eng.W + est_w / 2, 0.94 * eng.W - est_w / 2))
    y = float(np.clip((y0 + y1) / 2 * eng.H + size * 0.35, 0.1 * eng.H + size, 0.9 * eng.H))
    dark = shot["luma"] > 0.62
    if just == "LEFT":
        bx = [x, y - size, x + est_w, y + 0.25 * size]
    elif just == "RIGHT":
        bx = [x - est_w, y - size, x, y + 0.25 * size]
    else:
        bx = [x - est_w / 2, y - size, x + est_w / 2, y + 0.25 * size]
    return {"position": [round(x, 1), round(y, 1)], "justify": just, "size": round(size, 1),
            "color": [0.06, 0.06, 0.08] if dark else [1.0, 1.0, 1.0],
            "box_px": [round(v, 1) for v in bx], "zone": z, "roi_at_entry": roi, "sample_frame": k}


def _ov(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    ar = (a[2] - a[0]) * (a[3] - a[1])
    return ix * iy / ar if ar else 0


def _text(eng, a, b, text, preset, size_rel, why, sync=None):
    tc = eng.cfg["text"]
    # FASE 55 — texto não compete com transição: entra depois dela
    for e in eng.events:
        if e["category"] == "TRANSITION" and e["start_frame"] - 2 <= a <= e["end_frame"]:
            a = e["end_frame"] + 1
        if e["category"] in ("TRANSITION", "FREEZE") and a < e["start_frame"] < b:
            b = e["start_frame"] - 1
    if b - a < eng.fr(tc["min_hold_s"]) * 0.75:
        eng.decisions.append({"system": "TEXT", "frame": a, "decision": "slot curto demais para leitura — descartado"})
        return
    if not eng.claim(["text"], a, b, "text"):
        return
    pl = _place_text(eng, a, text, size_rel)
    n = len([l for l in eng.layers if l.startswith("TEXT_")]) + 1
    name = f"TEXT_{n:03d}"
    din = {"char_cascade": eng.k30(14), "impact": eng.k30(5), "word_reveal": eng.k30(10)}[preset]
    dout = eng.k30(8)
    spec = {"text": text, "font": tc["font"], "fallback_font": tc["fallback_font"], "size": pl["size"],
            "color": pl["color"], "tracking": 40 if preset != "impact" else 10, "leading": round(pl["size"] * 1.05, 1),
            "justify": pl["justify"], "preset": preset, "in_frames": [a, a + din], "out_frames": [b - dout, b],
            "based_on": "words" if preset == "word_reveal" else "characters",
            "animator": {"position": [0, round(40 * eng.S, 1)] if preset != "impact" else [0, 0],
                         "scale": 160 if preset == "impact" else 100, "blur": round((20 if preset == "impact" else 12) * eng.S, 1),
                         "opacity": 0, "tracking": -40 if preset == "impact" else 0},
            "shadow": {"opacity": 40, "softness": round(20 * eng.S, 1), "distance": round(4 * eng.S, 1)},
            "box_px": pl["box_px"]}
    plate = pl["zone"]["empty"] < 0.35
    if plate:
        bx = pl["box_px"]
        pad = 0.35 * pl["size"]
        pname = f"GFX_PLATE_{n:03d}"
        eng.layer(pname, "shape", **{"in": a, "out": b, "parent": "CAMERA_ROTATION", "group": "GRAPHICS",
                                     "shape": {"groups": [{"name": "PLATE", "items": [
                                         {"type": "rect", "size": [round(bx[2] - bx[0] + 2 * pad, 1), round(bx[3] - bx[1] + 2 * pad, 1)],
                                          "center": [round((bx[0] + bx[2]) / 2, 1), round((bx[1] + bx[3]) / 2, 1)]},
                                         {"type": "fill", "color": [0.0, 0.0, 0.0]}]}]},
                                     "transform": {"opacity": 0.0}, "comment": "Placa de legibilidade (sem espaço negativo)"})
        eng.add_keys(pname, "transform.opacity", [Key(a, 0.0, out_speed=0.0, out_infl=0.3), Key(a + din, 62.0, in_speed=0.0, in_infl=0.6),
                                                  Key(b - dout, 62.0, out_speed=0.0, out_infl=0.4), Key(b, 0.0, in_speed=0.0, in_infl=0.4)])
        spec["plate"] = pname
    eng.layer(name, "text", **{"in": a, "out": b, "parent": "CAMERA_ROTATION", "group": "TEXT", "text": spec,
                               "transform": {"position": pl["position"]},
                               "comment": "Texto no espaço negativo (placeholder editável)"})
    if preset == "impact":
        eng.add_keys(name, "transform.scale", [Key(a, [118.0, 118.0], out_speed=0.0, out_infl=0.1),
                                               Key(a + eng.k30(6), [100.0, 100.0], in_speed=0.0, in_infl=0.85)])
    eng.event("TEXT", a, b, recipe=preset.upper(), action=["Text animator (Range Selector)", f"entrada {preset}", "saída por caractere"],
              detector=why, params={"text": text, "position": pl["position"], "justify": pl["justify"], "size_px": pl["size"],
                                    "zone_cell": pl["zone"]["cell"], "zone_emptiness": pl["zone"]["empty"],
                                    "entrance_frames": din, "exit_frames": dout, "hold_s": round((b - a - din - dout) / eng.fps, 2)},
              intensity=0.5, easing="Range Selector: Start 0→100% ease-out (smoothness 100%)",
              audio_sync=sync, dependencies=[name, "CTRL_MASTER.TEXT_INTENSITY"], priority=5,
              why=f"{why}. Posição: célula {pl['zone']['cell']} do grid 4×3 com {pl['zone']['empty']:.0%} de vazio, fora da ROI do sujeito"
                  + (" — sem espaço negativo real: placa escura a 62% garante leitura." if plate else "."),
              expected="Texto legível que usa o espaço negativo sem cobrir o sujeito.")
    # linha de acento (mesma linguagem dos brackets)
    lname = f"GFX_LINE_{n:03d}"
    bx = pl["box_px"]
    ly = bx[3] + 0.18 * pl["size"]
    eng.layer(lname, "shape", **{"in": a, "out": b, "parent": "CAMERA_ROTATION", "group": "GRAPHICS",
                                 "shape": {"groups": [{"name": "LINE", "items": [
                                     {"type": "path", "points": [[bx[0], ly], [bx[0] + 0.45 * (bx[2] - bx[0]), ly]], "closed": False},
                                     {"type": "stroke", "color": eng.accent, "width": round(max(2.0, 4 * eng.S), 1)},
                                     {"type": "trim"}]}]},
                                 "comment": "Linha de acento do texto"})
    eng.add_keys(lname, "shape:LINE:trim.end", [Key(a, 0.0, out_speed=0.0, out_infl=0.1), Key(a + din, 100.0, in_speed=0.0, in_infl=0.85)])
    eng.add_keys(lname, "shape:LINE:trim.start", [Key(b - dout, 0.0, out_speed=0.0, out_infl=0.6), Key(b, 100.0, in_speed=0.0, in_infl=0.2)])


def graphics_and_text(eng):
    tc = eng.cfg["text"]
    copies = list(tc["copy"])
    ph = iter(tc["placeholders"] + [f"TEXTO {i:02d}" for i in range(5, 40)])

    def next_text(f):
        if copies:
            c = min(copies, key=lambda c: abs(c["time"] * eng.fps - f))
            copies.remove(c)
            return c["text"]
        return next(ph)

    hold = eng.fr(tc["min_hold_s"])
    # 1) abertura: primeiro respiro perto do início
    first = next((z for z in eng.breath if z["start_frame"] <= eng.fr(1.0)), None)
    s0 = eng.shots_out[0]
    if not (first and first["end_frame"] - first["start_frame"] >= eng.fr(2.0)) and \
            s0["out_end"] >= eng.fr(2.0) and float(np.mean(eng.E[s0["out_start"]:s0["out_end"]])) < 0.5:
        first = {"start_frame": s0["out_start"], "end_frame": s0["out_end"]}
    if first and first["end_frame"] - first["start_frame"] >= eng.fr(2.0):
        a = first["start_frame"] + eng.k30(8)
        nxt = [x for x in eng.beats_out if first["end_frame"] - eng.k30(12) <= x <= first["end_frame"]]
        b = (nxt[0] if nxt else first["end_frame"]) - eng.k30(2)
        _text(eng, a, b, next_text(a), "char_cascade", 0.07, "Abertura em respiro: título entra caractere a caractere",
              sync={"type": "BEAT (saída)", "frame": b, "offset_frames": 0})
    # 2) drop: palavra de impacto logo após o golpe
    drops = [c for c in eng.cands if "DROP" in c["types"]]
    for c in drops[:1]:
        a = c["frame"] + eng.k30(2)
        bt = [x for x in eng.beats_out if x >= a + hold]
        b = min(bt[0] if bt else a + hold, eng.n_out - 1)
        _text(eng, a, b, next_text(a), "impact", 0.11, f"DROP em f{c['frame']}: palavra de impacto",
              sync={"type": "DROP", "frame": c["frame"], "offset_frames": eng.k30(2)})
        sh = eng.shots_out[eng._shot_at(c["frame"])]
        if eng.track["conf"][eng.v_idx(c["frame"])] and eng.track["conf"][eng.v_idx(c["frame"])] >= 0.5:
            _brackets(eng, c["frame"], min(sh["out_end"], c["frame"] + eng.fr(1.2)), sh, "DROP com sujeito rastreado")
    # 3) freeze: brackets (+ texto curto)
    for slot in eng.text_slots:
        if slot["kind"] == "freeze":
            sh = eng.shots_out[eng._shot_at(slot["frame"])]
            _brackets(eng, slot["frame"], slot["end"] + eng.k30(6), sh, "freeze frame")
    # 4) final: depois do último IMPACT, se houver espaço
    imps = [c for c in eng.cands if "IMPACT" in c["types"]]
    if imps:
        f = imps[-1]["frame"] + eng.k30(4)
        if eng.n_out - f >= hold + eng.k30(8):
            _text(eng, f, eng.n_out - eng.k30(4), next_text(f), "word_reveal", 0.065, "Golpe final: cartela de encerramento",
                  sync={"type": "IMPACT", "frame": imps[-1]["frame"], "offset_frames": eng.k30(4)})
    # anéis nos impactos grandes com sujeito
    rings = 0
    for e in sorted([e for e in eng.events if e.get("tier") == "large"], key=lambda e: -e["intensity"]):
        if rings >= max(1, int(eng.n_out / eng.fps / 8)):
            break
        f = e["start_frame"]
        if eng.claim(["gfx"], f - eng.fr(0.75), f + eng.fr(0.75), "ring"):
            _ring(eng, f, eng.shots_out[eng._shot_at(f)])
            rings += 1


# ------------------------------------------------------------------ cor (FASE 31)
def color(eng):
    cc = eng.cfg["color"]
    for s in eng.shots_out:
        a, b = s["out_start"], s["out_end"]
        L = s["luma"]
        expo = 0.0
        if L < 0.28 or L > 0.68:
            expo = float(np.clip(np.log2(cc["exposure_target"] / max(L, 1e-3)) * cc["exposure_strength"], -cc["max_stops"], cc["max_stops"]))
        rgb = np.array(s["mean_rgb"]) + 1e-4
        gray = rgb.mean()
        wb = np.clip(np.log2(gray / rgb) * cc["wb_strength"], -cc["max_wb_stops"], cc["max_wb_stops"])
        if np.max(np.abs(wb)) < 0.05:
            wb = np.zeros(3)
        if abs(expo) < 1e-3 and not wb.any():
            continue
        name = f"COLOR_CORR_{s['id']}"
        ch = [round(expo + float(w), 3) for w in wb]
        eng.layer(name, "adjustment", **{"in": a, "out": b, "group": "COLOR",
                                         "comment": "CORREÇÃO por shot (antes da estilização)"})
        eng.effect(name, "ADBE Exposure2", "EXPOSURE", {"1": 2, "5": ch[0], "8": ch[1], "11": ch[2]})
        why = []
        if expo:
            why.append(f"luma média {L:.2f} → {expo:+.2f} stop(s) rumo a {cc['exposure_target']}")
        if wb.any():
            why.append("dominante de cor RGB " + "/".join(f"{v:.2f}" for v in s["mean_rgb"]) +
                       f" → compensação parcial ({int(cc['wb_strength'] * 100)}%) por canal")
        eng.event("COLOR", a, b, recipe="CORRECTION", action=["Exposure por canal (linear)"],
                  detector=f"estatística do shot {s['id']}", params={"exposure_stops": round(expo, 3),
                                                                      "channel_stops_rgb": ch},
                  intensity=round(min(1, abs(expo) / cc["max_stops"] + float(np.max(np.abs(wb)))), 3),
                  easing="—", audio_sync=None, dependencies=[name], priority=4, source={"shot": s["id"]},
                  why="; ".join(why) + ". Correção é parcial para não apagar uma intenção de cor.",
                  expected="Exposição e balanço consistentes antes do look.")
    lk = cc["look"]
    eng.layer("COLOR_LOOK", "adjustment", group="COLOR", comment="ESTILIZAÇÃO (depois da correção)",
              expressions={"transform.opacity": "100 * " + 'thisComp.layer("CTRL_MASTER").effect("COLOR_INTENSITY")(1);'})
    eng.effect("COLOR_LOOK", "ADBE Brightness & Contrast 2", "CONTRAST", {"1": 0.0, "2": lk["contrast"], "3": 0})
    eng.effect("COLOR_LOOK", "ADBE Vibrance", "VIBRANCE", {"1": lk["vibrance"], "2": 0.0})
    eng.layer("COLOR_VIGNETTE", "solid", color=[0, 0, 0], group="COLOR", comment="Vinheta (máscara elíptica subtraída)",
              masks=[{"shape": "ellipse", "inset": 0.04, "feather": round(0.32 * eng.W, 1), "mode": "SUBTRACT"}],
              transform={"opacity": round(100 * lk["vignette"], 1)},
              expressions={"transform.opacity": f'{round(100 * lk["vignette"], 1)} * thisComp.layer("CTRL_MASTER").effect("COLOR_INTENSITY")(1);'})
    eng.event("COLOR", 0, eng.n_out, recipe="LOOK", action=["Contraste", "Vibrance", "Vinheta"],
              detector="estilo global", params=dict(lk), intensity=0.3, easing="—", audio_sync=None,
              dependencies=["COLOR_LOOK", "COLOR_VIGNETTE", "CTRL_MASTER.COLOR_INTENSITY"], priority=4,
              why="Look aplicado só depois da correção, com intensidade central.", expected="Unidade visual.")


# ------------------------------------------------------------------ reação ao áudio (FASE 32)
def audio_react(eng):
    if not eng.audio:
        return
    rp = eng.audio["react_per_frame"]
    idx = [eng.a_idx(f) for f in range(eng.n_out)]
    for k_src, slider in (("bass", "BASS"), ("mid", "MID"), ("treble", "TREBLE"), ("amp", "AMP")):
        arr = rp[k_src]
        eng.layers["CTRL_AUDIO"]["baked"][f"effect:{slider}:ADBE Slider Control-0001"] = {
            "start": 0, "values": [round(float(arr[i]), 3) for i in idx]}
    gb = [1.0 if eng.band[f] in ("HIGH", "EXTREME") and not eng.in_breath(f) else 0.0 for f in range(eng.n_out)]
    gt = [1.0 if eng.band[f] == "MEDIUM" and not eng.in_breath(f) else 0.0 for f in range(eng.n_out)]
    for name, g in (("GATE_BASS_TO_ZOOM", gb), ("GATE_TREBLE_TO_GLOW", gt)):
        keys = [Key(0, g[0], interp_in="HOLD", interp_out="HOLD")]
        for f in range(1, eng.n_out):
            if g[f] != g[f - 1]:
                keys.append(Key(f, g[f], interp_in="HOLD", interp_out="HOLD"))
        eng.slider_keys("CTRL_AUDIO", name, keys)
        share = float(np.mean(g))
        eng.event("AUDIO_SYNC", 0, eng.n_out, recipe=name,
                  action=["Grave → escala da câmera (até +2.5%)"] if "BASS" in name else ["Agudos → intensidade do glow"],
                  detector="bandas do áudio (seguidor de envoltória, release 120 ms)",
                  params={"active_share": round(share, 3), "gate_changes": len(keys) - 1}, intensity=0.3,
                  easing="HOLD nos gates", audio_sync={"type": "BAND", "frame": 0, "offset_frames": 0},
                  dependencies=["CTRL_AUDIO", "CAMERA_ZOOM" if "BASS" in name else "VFX_GLOW"], priority=6,
                  why=("Grave só reage nas seções HIGH/EXTREME" if "BASS" in name else "Agudo só reage nas seções MEDIUM") +
                      " e nunca em respiros — cada seção tem UM canal reativo, não tudo ao mesmo tempo.",
                  expected="Pulsação musical discreta e alternada.")


def markers(eng):
    keep = {"KICK", "SNARE", "DROP", "ENTRY", "IMPACT", "RISER", "SILENCE", "DOWNBEAT", "VOCAL"}
    for m in eng.amarks:
        if m["type"] in keep:
            eng.markers["MARKERS_AUDIO"].append({"frame": m["out"], "duration": eng.fr(m["duration"]) if m.get("duration") else 0,
                                                 "comment": f"{m['type']} i={m['intensity']:.2f} c={m['confidence']:.2f}"})
    if eng.audio:
        for s in eng.audio["sections"]:
            o = eng.a2o(int(round(s["time"] * eng.src_fps)))
            if o is not None:
                eng.markers["MARKERS_SECTIONS"].append({"frame": o, "duration": 0, "comment": f"SEÇÃO novelty={s['novelty']:.2f}"})
    for s in eng.shots_out:
        eng.markers["MARKERS_SECTIONS"].append({"frame": s["out_start"], "duration": 0, "comment": s["id"]})
    # marcadores no mesmo frame (AE substituiria): funde mantendo o maior
    for k, lst in eng.markers.items():
        by = {}
        for m in sorted(lst, key=lambda m: m["frame"]):
            if m["frame"] in by:
                if k == "MARKERS_IMPACT":
                    old = X.parse_comment(by[m["frame"]]["comment"])
                    new = X.parse_comment(m["comment"])
                    if new.get("amp", 0) > old.get("amp", 0):
                        by[m["frame"]] = m
                else:
                    by[m["frame"]]["comment"] += " | " + m["comment"]
            else:
                by[m["frame"]] = dict(m)
        eng.markers[k] = [by[f] for f in sorted(by)]
        eng.layers[k]["markers"] = eng.markers[k]


def finalize_ramp_blur(eng):
    wins = [w for w in eng.tm.windows if w["kind"] == "SPEED_RAMP"]
    if not wins:
        return
    eng.effect("VIDEO", "CC Force Motion Blur", "FORCE_MB", {"1": 8, "2": 1, "3": 0.0})
    keys = [Key(0, 0.0, interp_in="HOLD", interp_out="HOLD")]
    for w in wins:
        for f in range(w["start_frame"], w["end_frame"] + 1, eng.k30(2)):
            v = eng.tm.speed(f)
            keys.append(Key(f, round(float(np.clip(180 * (v - 1), 0, 360)), 1), interp_in="LINEAR", interp_out="LINEAR"))
        keys.append(Key(w["end_frame"] + eng.k30(2), 0.0, interp_in="LINEAR", interp_out="HOLD"))
    eng.add_keys("VIDEO", "effect:FORCE_MB:3", keys)
    eng.layers["VIDEO"]["expressions"]["effect:FORCE_MB:3"] = "value * " + X.k_expr("VFX_INTENSITY")


# ------------------------------------------------------------------ montagem final
def _rank(name):
    for i, p in enumerate(STACK):
        if name == p or (p.endswith("_") and name.startswith(p)):
            return i
    return len(STACK)


def assemble(eng):
    eng.events.sort(key=lambda e: (e["start_frame"], e.get("priority", 9)))
    for i, e in enumerate(eng.events):
        e["id"] = f"EVT_{i + 1:03d}"
        if e["category"] not in CATEGORIES:
            raise ValueError(e["category"])
    layers = sorted(eng.layers.values(), key=lambda L: (_rank(L["name"]), L["in"], L["name"]))
    checks = []
    for L in layers:
        for prop, ks in L["keys"].items():
            errs = validate_keys([Key.from_dict(k) for k in ks])
            dup = len({k["f"] for k in ks}) != len(ks)
            if errs or dup:
                checks.append({"layer": L["name"], "prop": prop, "errors": errs + (["keys no mesmo frame"] if dup else [])})
    checks += [{"layer": "PRE_SOURCE_REMAP/FOOTAGE", "prop": "timeremap", "errors": [e]} for e in eng.tm.check()]
    rgb = any(L["kind"] == "rgbsplit" for L in layers)
    interp = any("Pixel Motion" in str(e.get("params", {}).get("interpolation", "")) for e in eng.events)
    cats = {}
    recipes = {}
    for e in eng.events:
        cats[e["category"]] = cats.get(e["category"], 0) + 1
        recipes[e.get("recipe", "-")] = recipes.get(e.get("recipe", "-"), 0) + 1
    return {
        "schema": "conde.timeline/1",
        "project": {"name": eng.cfg["project_name"], "version": eng.cfg["version"], "source": eng.an["media"]["path"]},
        "comp": {"name": "MASTER_EDIT", "width": eng.W, "height": eng.H, "fps": eng.fps, "duration_frames": eng.n_out,
                 "pixel_aspect": eng.an["media"]["video"]["pixel_aspect"], "motion_blur": True, "shutter_angle": 180},
        "source": {"fps": eng.src_fps, "frames": eng.n_src, "width": eng.W, "height": eng.H},
        "mode": eng.mode,
        "base_edit": eng.base,
        "precomps": {
            "PRE_SOURCE_REMAP": {"fps": eng.fps, "duration_frames": eng.n_out, "frame_blending": "PIXEL_MOTION" if interp else None,
                                 "timeremap": [k.to_dict() for k in eng.tm.keys], "windows": eng.tm.windows},
            "PRE_RGB_SPLIT": {"enabled": rgb},
        },
        "audio": {"layer": "AUDIO_ORIGINAL", "timeremap": [k.to_dict() for k in eng.audio_tm.keys] if eng.audio_tm else None},
        "layers": layers,
        "events": eng.events,
        "zones": {"breath": eng.breath},
        "accent_color": eng.accent,
        "energy_per_frame": [round(float(x), 3) for x in eng.E],
        "decisions": eng.decisions,
        "warnings": eng.warnings,
        "build_checks": checks,
        "stats": {"events_by_category": cats, "events_by_recipe": recipes, "layers": len(layers)},
    }
