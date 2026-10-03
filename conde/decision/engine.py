"""FASES 7–12, 24, 29, 41–56 — motor de decisão.

Entrada: analysis.json. Saída: timeline.json (contrato único lido pelo gerador de
JSX do After Effects e pelo renderizador de preview).

Cada evento carrega: categoria, quadros, ação, parâmetros, keys, intensidade,
easing, sincronia de áudio, dependências, scores e o PORQUÊ.
Sistemas disputam "canais" (zoom, posição, flash, blur...) por prioridade
(FASE 55): sujeito/narrativa > transição > áudio > câmera > texto > VFX > decoração.
"""
import numpy as np
from scipy.ndimage import gaussian_filter1d

from ..analysis.energy import band_of
from .curves import THIRD, Key, pulse, punch
from .timemap import TimeMap, profile_keys, ramp_with_hit, trim_map

CATEGORIES = ["CUT", "TRANSITION", "ZOOM", "PUNCH", "SHAKE", "TEXT", "GRAPHIC", "VFX", "TRACKING", "ROTOSCOPE",
              "SPEED_RAMP", "TIME_REMAP", "COLOR", "PARTICLE", "DISTORTION", "CAMERA", "FREEZE", "BLUR", "FLASH",
              "GLITCH", "PARALLAX", "AUDIO_SYNC"]
TYPE_W = {"DROP": 1.0, "IMPACT": 0.95, "ENTRY": 0.9, "KICK": 0.7, "SNARE": 0.65, "FLASH_VISUAL": 0.6}
RECIPES = {
    "small": ["PUNCH_IN", "ROT_KICK"],
    "medium": ["PUNCH_SHAKE", "FLASH_PUNCH", "BLUR_HIT", "ROT_KICK"],
    "large": ["FLASH_PUNCH", "DISTORT_HIT", "RGB_HIT", "PUNCH_SHAKE"],
}
RECIPE_ACTIONS = {
    "PUNCH_IN": ["Scale Punch"],
    "ROT_KICK": ["Rotation Kick", "Scale Punch", "Directional Shake"],
    "PUNCH_SHAKE": ["Scale Punch", "Position Shake"],
    "FLASH_PUNCH": ["Scale Punch", "Exposure Flash", "Position Shake"],
    "BLUR_HIT": ["Scale Punch", "Directional Blur", "Position Shake"],
    "DISTORT_HIT": ["Scale Punch", "Turbulent Distortion", "Exposure Flash", "Position Shake"],
    "RGB_HIT": ["Scale Punch", "RGB Split", "Position Shake"],
}
RECIPE_CHANNELS = {
    "PUNCH_IN": ["zoom"], "ROT_KICK": ["zoom", "rot"], "PUNCH_SHAKE": ["zoom"],
    "FLASH_PUNCH": ["zoom", "flash"], "BLUR_HIT": ["zoom", "dirblur"],
    "DISTORT_HIT": ["zoom", "distort", "flash"], "RGB_HIT": ["zoom", "rgb"],
}


def lerp(r, x):
    return r[0] + (r[1] - r[0]) * float(np.clip(x, 0, 1))


class Engine:
    def __init__(self, an, cfg):
        self.an, self.cfg = an, cfg
        self.rng = np.random.default_rng(cfg["seed"])
        self.src_fps = float(an["fps"])
        self.fps = self.src_fps if cfg["comp_fps"] == "source" else float(cfg["comp_fps"])
        self.W, self.H = an["width"], an["height"]
        self.S = self.W / 1920.0
        self.n_src = an["n_frames"]
        self.audio = an["audio"]
        self.layers = {}
        self.events = []
        self.busy = {}
        self.markers = {"MARKERS_IMPACT": [], "MARKERS_AUDIO": [], "MARKERS_SECTIONS": []}
        self.warnings = list(an["media"].get("warnings", []))
        self.decisions = []      # log de decisões (A/B, rejeições, ajustes)
        self.recipe_count = {}
        self.text_slots = []

    # ------------------------------------------------------------ utilidades
    def fr(self, sec):
        return max(1, int(round(sec * self.fps)))

    def k30(self, frames30):
        return max(1, int(round(frames30 * self.fps / 30.0)))

    def t(self, f):
        return round(f / self.fps, 4)

    def a_idx(self, out_f):
        """Índice nos arrays de análise (frames da fonte) para um frame de saída."""
        if self.mode == "dialogue":
            return self.tm.src_frame(out_f)
        return int(min(self.n_src - 1, max(0, np.floor(out_f * self.src_fps / self.fps + 1e-6))))

    def v_idx(self, out_f):
        return self.tm.src_frame(out_f)

    def a2o(self, src_f):
        """Frame da fonte (áudio) → frame de saída (None se o trecho foi cortado)."""
        if self.mode != "dialogue":
            return int(round(src_f * self.fps / self.src_fps))
        out = 0
        for a, b in self.keep:
            if a <= src_f < b:
                return out + (src_f - a)
            out += b - a
        return None

    def claim(self, channels, a, b, owner):
        """Reserva [a, b] FECHADO: janelas que só encostam também conflitam (o key final de uma
        e o inicial da outra cairiam no mesmo frame da mesma propriedade)."""
        for ch in channels:
            for (x, y, _) in self.busy.get(ch, []):
                if a <= y and x <= b:
                    return False
        for ch in channels:
            self.busy.setdefault(ch, []).append((a, b, owner))
        return True

    def free(self, ch, a, b):
        return all(not (a <= y and x <= b) for (x, y, _) in self.busy.get(ch, []))

    def layer(self, name, kind, **kw):
        if name not in self.layers:
            spec = {"name": name, "kind": kind, "in": 0, "out": self.n_out, "parent": None, "effects": [],
                    "expressions": {}, "keys": {}, "baked": {}, "markers": [], "blend": "NORMAL",
                    "motion_blur": False, "comment": ""}
            spec.update(kw)
            self.layers[name] = spec
        return self.layers[name]

    def effect(self, layer, match, name, params=None):
        L = self.layers[layer]
        for e in L["effects"]:
            if e["name"] == name:
                return e
        e = {"match": match, "name": name, "params": params or {}}
        L["effects"].append(e)
        return e

    def add_keys(self, layer, prop, keys):
        L = self.layers[layer]
        lst = L["keys"].setdefault(prop, [])
        lst.extend(k.to_dict() if isinstance(k, Key) else k for k in keys)
        lst.sort(key=lambda k: k["f"])

    def slider_keys(self, prop_layer, slider, keys):
        self.add_keys(prop_layer, f"effect:{slider}:ADBE Slider Control-0001", keys)

    def event(self, category, start, end, **kw):
        ev = {"category": category, "start_frame": int(start), "end_frame": int(end),
              "start_time": self.t(start), "end_time": self.t(end), "duration_frames": int(end - start),
              "duration_s": round((end - start) / self.fps, 4)}
        ev.update(kw)
        ev.setdefault("validation", {"preview": "pending", "after_effects": "pending (AE indisponível neste ambiente)"})
        self.events.append(ev)
        return ev

    # ------------------------------------------------------------ dados derivados
    def _prepare(self):
        an = self.an
        a = self.audio
        if self.cfg["mode"] != "auto":
            self.mode = self.cfg["mode"]
        elif a:
            self.mode = a["mode"]["value"]
        else:
            self.mode = "visual"
        if self.mode == "dialogue" and abs(self.fps - self.src_fps) > 1e-6:
            self.warnings.append("Modo diálogo exige comp no fps da fonte; usando fps da fonte.")
            self.fps = self.src_fps
        self.energy_src = np.array(an["energy"]["per_frame"])
        comp = an["energy"]["components_per_frame"]
        self.motion_src = np.array(comp["visual_motion"])
        self.change_src = np.array(comp["visual_change"])
        self.track = an["track"]
        self.roi = an["roi_per_frame"]
        self.shot_of_src = np.zeros(self.n_src, int)
        for i, s in enumerate(an["shots"]):
            self.shot_of_src[s["start_frame"]:s["end_frame"]] = i
        self.vocal = [(r["start"], r["end"]) for r in (a["vocal_regions"] if a else [])]

    def _base_edit(self):
        """FASE 9 — edição base: só tempo, ordem, cortes e sincronia (sem efeitos)."""
        an = self.an
        base = {"mode": self.mode, "operations": []}
        if self.mode == "dialogue":
            dcfg = self.cfg["dialogue"]
            pad = int(round(dcfg["pad_s"] * self.src_fps))
            cut = [(s["start_frame"] + pad, s["end_frame"] - pad) for s in self.audio["silences"]
                   if s["end"] - s["start"] >= dcfg["trim_silence_min_s"] and s["end_frame"] - s["start_frame"] > 2 * pad]
            keep, cur = [], 0
            for a0, b0 in cut:
                if a0 > cur:
                    keep.append((cur, a0))
                cur = b0
            if cur < self.n_src:
                keep.append((cur, self.n_src))
            self.keep = keep
            self.tm, self.n_out = trim_map(self.fps, self.n_src, keep)
            self.audio_tm = self.tm
            removed = self.n_src - self.n_out
            base["operations"].append({"op": "TRIM_SILENCES", "segments_kept": len(keep),
                                       "removed_frames": removed, "removed_s": round(removed / self.fps, 3)})
        else:
            self.keep = [(0, self.n_src)]
            self.tm = TimeMap(self.fps, self.n_src, src_fps=self.src_fps)
            self.audio_tm = None
            self.n_out = int(round(self.n_src * self.fps / self.src_fps))
        # cortes e shots no domínio de saída
        self.cuts = []
        for c in an["cuts"]:
            o = self.a2o(c["frame"]) if self.mode == "dialogue" else int(round(c["frame"] * self.fps / self.src_fps))
            if o is not None:
                self.cuts.append({"src": c["frame"], "out": o, "type": c["type"], "conf": c["confidence"]})
        if self.mode == "dialogue":
            out = 0
            for a0, b0 in self.keep[:-1]:
                out += b0 - a0
                self.cuts.append({"src": None, "out": out, "type": "JUMP_CUT", "conf": 1.0})
            self.cuts.sort(key=lambda c: c["out"])
        # marcadores de áudio no domínio de saída
        self.amarks = []
        if self.audio:
            for m in self.audio["markers"]:
                o = self.a2o(int(round(m["time"] * self.src_fps)))
                if o is not None and 0 <= o < self.n_out:
                    mm = dict(m)
                    mm["out"] = o
                    self.amarks.append(mm)
        self.beats_out = sorted({m["out"] for m in self.amarks if m["type"] in ("BEAT", "DOWNBEAT")})
        self.downbeats_out = sorted({m["out"] for m in self.amarks if m["type"] == "DOWNBEAT"})
        # FASE 10 — microedição: alinhar corte ao golpe mais próximo (±2 frames) com warp neutro
        hits = sorted({m["out"] for m in self.amarks if m["type"] in ("KICK", "SNARE", "DROP", "ENTRY", "IMPACT", "SILENCE")
                       or (m["type"] in ("BEAT", "DOWNBEAT") and m["intensity"] >= 0.3)})
        before, after = [], []
        w = self.k30(8)
        for c in self.cuts:
            if not hits:
                break
            b = min(hits, key=lambda h: abs(h - c["out"]))
            d = b - c["out"]
            before.append(abs(d) if abs(d) <= 6 else None)
            c["shown_at"] = c["out"]
            if d == 0 or abs(d) > 2 or self.mode == "dialogue" or c["type"] == "JUMP_CUT":
                after.append(abs(d) if abs(d) <= 6 else None)
                continue
            a0, b0 = c["out"] - w, c["out"] + w
            if a0 < 0 or b0 > self.n_out or not self.tm.free(a0, b0) or self._in_vocal(a0, b0):
                after.append(abs(d))
                continue
            cs = self.tm.src_time(c["out"])
            v1 = w / (b - a0)
            v2 = w / (b0 - b)
            keys = [Key(a0, self.tm.src_time(a0), in_speed=1.0, out_speed=v1),
                    Key(b, cs, in_speed=v1, out_speed=v2),
                    Key(b0, self.tm.src_time(b0), in_speed=v2, out_speed=1.0)]
            wid = f"MICRO_{len(self.tm.windows) + 1:03d}"
            self.tm.insert_window(a0, b0, keys, {"id": wid, "kind": "MICRO_SYNC", "neutral": True,
                                                 "speeds": [round(v1, 3), round(v2, 3)]})
            self.claim(["time"], a0, b0, wid)
            c["shown_at"] = b
            after.append(0)
            base["operations"].append({"op": "MICRO_SYNC", "cut_src_frame": c["src"], "from_frame": c["out"],
                                       "to_frame": b, "shift": d, "window": [a0, b0],
                                       "speeds": [round(v1, 3), round(v2, 3)]})
            self.event("CUT", a0, b0, detector="corte da fonte a %+d frame(s) do golpe de áudio" % (-d),
                       action=["Micro time-warp neutro"], recipe="MICRO_SYNC",
                       params={"shift_frames": d, "speed_before": round(v1, 3), "speed_after": round(v2, 3)},
                       intensity=0.2, easing="velocidade constante por trecho (keys Bezier, influência 33%)",
                       audio_sync={"type": "HIT", "frame": b, "offset_frames": 0},
                       dependencies=["PRE_SOURCE_REMAP/FOOTAGE.timeremap"], priority=2,
                       why=f"Corte original caía {abs(d)} frame(s) {'depois' if d < 0 else 'antes'} do golpe; "
                           "deslocá-lo exige reamostrar só 2×%d frames, preservando a sincronia global." % w,
                       expected="Corte exatamente no golpe sem alterar duração nem sync do resto.")
        for c in self.cuts:
            c.setdefault("shown_at", c["out"])
        ok_b = [x for x in before if x is not None]
        ok_a = [x for x in after if x is not None]
        base["cut_alignment"] = {
            "cuts": len(self.cuts),
            "within_1f_before": sum(1 for x in ok_b if x <= 1), "within_1f_after": sum(1 for x in ok_a if x <= 1),
            "mean_abs_offset_before": round(float(np.mean(ok_b)), 2) if ok_b else None,
            "mean_abs_offset_after": round(float(np.mean(ok_a)), 2) if ok_a else None,
        }
        self.shots_out = []
        for i, s in enumerate(an["shots"]):
            a0 = self.cuts[i - 1]["shown_at"] if i > 0 and i - 1 < len(self.cuts) and self.mode != "dialogue" else None
            if self.mode == "dialogue":
                a0 = self.a2o(s["start_frame"])
                b0 = self.a2o(s["end_frame"] - 1)
                if a0 is None or b0 is None:
                    continue
                b0 += 1
            else:
                a0 = 0 if i == 0 else self.cuts[i - 1]["shown_at"]
                b0 = self.n_out if i == len(an["shots"]) - 1 else self.cuts[i]["shown_at"]
            so = dict(s)
            so.update(out_start=a0, out_end=b0)
            self.shots_out.append(so)
        self.base = base

    def _in_vocal(self, a, b):
        if self.mode not in ("mixed", "dialogue"):
            return False
        ta, tb = a / self.fps, b / self.fps
        return any(ta < y and x < tb for x, y in self.vocal)

    def _energy_out(self):
        self.E = np.array([self.energy_src[self.a_idx(f)] for f in range(self.n_out)])
        self.E_slow = gaussian_filter1d(self.E, self.fps * 0.75)
        self.band = [band_of(e) for e in self.E]

    # ------------------------------------------------------------ FASE 43 respiros
    def _breath(self):
        bc = self.cfg["breath"]
        zones = []
        for pct in (bc["energy_pct"], 35, 40, 45):
            thr = np.percentile(self.E_slow, pct)
            low = self.E_slow <= thr
            zones, start = [], None
            for i, v in enumerate(np.append(low, False)):
                if v and start is None:
                    start = i
                elif not v and start is not None:
                    if i - start >= self.fr(bc["min_len_s"]):
                        zones.append([start, i])
                    start = None
            share = sum(b - a for a, b in zones) / self.n_out
            if share >= bc["target_share"]:
                break
        self.breath = []
        big = [m["out"] for m in self.amarks if m["type"] in ("DROP", "ENTRY", "IMPACT")]
        for a, b in zones:
            nxt = [x for x in big if b - self.fr(0.5) <= x <= b + self.fr(1.0)]
            self.breath.append({"start_frame": a, "end_frame": b, "start_time": self.t(a), "end_time": self.t(b),
                                "why": "energia na faixa mais baixa do vídeo" +
                                       (f"; prepara o golpe em f{nxt[0]} (contraste)" if nxt else "")})
            self.markers["MARKERS_SECTIONS"].append({"frame": a, "duration": b - a, "comment": "RESPIRO"})

    def in_breath(self, f):
        return any(z["start_frame"] <= f < z["end_frame"] for z in self.breath)

    # ------------------------------------------------------------ candidatos (FASE 53)
    def _candidates(self):
        sections = {self.a2o(int(round(s["time"] * self.src_fps))) for s in (self.audio["sections"] if self.audio else [])}
        cuts_at = [c["shown_at"] for c in self.cuts]
        cands = {}
        for m in self.amarks:
            if m["type"] not in TYPE_W or m["out"] >= self.n_out - 2:
                continue
            f = m["out"]
            key = next((k for k in cands if abs(k - f) <= 1), f)
            c = cands.setdefault(key, {"frame": key, "types": set(), "audio": 0.0, "markers": []})
            c["types"].add(m["type"])
            c["audio"] = max(c["audio"], TYPE_W[m["type"]] * max(m["intensity"], 0.4 if m["type"] in ("DROP", "IMPACT", "ENTRY") else 0))
            c["markers"].append(m)
        for fl in self.an["visual_flashes"]:
            o = self.a2o(fl["frame"]) if self.mode == "dialogue" else int(round(fl["frame"] * self.fps / self.src_fps))
            if o is None:
                continue
            key = next((k for k in cands if abs(k - o) <= 1), o)
            c = cands.setdefault(key, {"frame": key, "types": set(), "audio": 0.0, "markers": []})
            c["types"].add("FLASH_VISUAL")
            c["visual_flash"] = fl
        out = []
        for f, c in cands.items():
            si = self.v_idx(f)
            motion = float(self.motion_src[si])
            contrast = float(self.change_src[si])
            imp = 0.0
            if any(s is not None and abs(s - f) <= 2 for s in sections):
                imp += 0.5
            if any(abs(d - f) <= 1 for d in self.downbeats_out):
                imp += 0.3
            if any(abs(x - f) <= 2 for x in cuts_at):
                imp += 0.2
            conf = self.track["conf"][si] or 0.0
            narrative = 0.25 + 0.5 * conf
            score = 0.40 * c["audio"] + 0.20 * motion + 0.20 * min(1, imp) + 0.10 * contrast + 0.10 * narrative
            e = float(self.E[f])
            inten = float(np.clip(score * (0.75 + 0.5 * e) * 1.25, 0, 1))
            if "DROP" in c["types"]:
                inten = max(inten, 0.85)
            if "IMPACT" in c["types"] or "ENTRY" in c["types"]:
                inten = max(inten, 0.72)
            if self.in_breath(f) and not ({"DROP", "IMPACT", "ENTRY"} & c["types"]):
                inten = min(inten, 0.3)
            c.update(scores={"audio": round(c["audio"], 3), "motion": round(motion, 3), "importance": round(min(1, imp), 3),
                             "visual_contrast": round(contrast, 3), "narrative_proxy": round(narrative, 3),
                             "total": round(score, 3)},
                     intensity=round(inten, 3), energy=round(e, 3), band=self.band[f])
            out.append(c)
        self.cands = sorted(out, key=lambda c: c["frame"])

    # ------------------------------------------------------------ FASE 11/12 speed ramps
    def _ramps(self):
        if self.mode not in ("music", "mixed"):
            self.decisions.append({"system": "SPEED_RAMP", "decision": f"desligado no modo {self.mode} (sync de fala)"})
            return
        rc = self.cfg["ramps"]
        native_floor = self.fps / self.src_fps
        s_slow = rc["s_slow"]
        interp = s_slow < native_floor - 1e-6
        if interp and not self.cfg["allow_interpolation"]:
            s_slow = native_floor
            interp = False
        s_slow = max(s_slow, self.cfg["min_speed_interpolated"]) if interp else s_slow
        budget = max(1, int(self.n_out / self.fps / rc["per_seconds"]))
        r, hold, edge = self.fr(rc["ramp_s"]), self.fr(rc["hold_s"]), self.k30(6)
        hits = [m for m in self.amarks if m["type"] in ("KICK", "SNARE", "DOWNBEAT", "DROP", "ENTRY")]
        shots = sorted([s for s in self.shots_out if s["motion_level"] == "HIGH" and s["duration_s"] >= 1.2],
                       key=lambda s: -(s["energy"] + s["motion_speed"] * 20))
        made = 0
        for s in shots:
            if made >= budget:
                break
            a, b = s["out_start"] + edge, s["out_end"] - edge
            # encolhe a janela para não encostar em outra janela de tempo (micro-sync/freeze):
            # bordas compartilhadas apagariam o key de borda da janela vizinha
            for (x, y, _) in self.busy.get("time", []):
                if a <= x <= b:
                    b = min(b, x - 2)
                if a <= y <= b:
                    a = max(a, y + 2)
            if b - a < self.fr(1.0) or not self.tm.free(a, b) or self._in_vocal(a, b):
                continue
            if any(self.in_breath(f) for f in (a, (a + b) // 2, b - 1)):
                continue
            # instante visual do shot: flash/transiente da própria imagem, senão pico de mudança
            vis_hit = None
            for fl in self.an["visual_flashes"]:
                o = int(round(fl["frame"] * self.fps / self.src_fps))
                if a + r + 2 <= o <= b - hold - r - 2:
                    vis_hit = o
            best = None
            for m in hits:
                tau = m["out"]
                if not (a + 2 * r <= tau <= b - hold - 2 * r):
                    continue
                strength = m["intensity"] + (0.3 if m["type"] in ("DOWNBEAT", "DROP") else 0)
                if vis_hit is not None:
                    m_src = vis_hit / self.fps
                else:
                    m_src = (a + (tau - a) * rc["target_v1"] * 0.8) / self.fps
                res = ramp_with_hit(a, b, tau, m_src, self.fps, s_slow, hold, r, vmax=self.cfg["max_speed"], vmin=s_slow)
                if res is None:
                    continue
                prof, v1, v2 = res
                # hit visual que JÁ cai no golpe: mantém a sincronia e cria o slow-mo nele (sem mover o hit)
                aligned = vis_hit is not None and abs(m_src * self.fps - tau) <= 1
                sc = (strength + (0.8 if aligned else 0) - abs(v1 - rc["target_v1"]) / 3 * (0.3 if aligned else 1.0)
                      - abs(v2 - 1.6) / 4)
                if best is None or sc > best[0]:
                    best = (sc, tau, m, prof, v1, v2, m_src)
            if best is None:
                self.decisions.append({"system": "SPEED_RAMP", "shot": s["id"], "decision": "sem rampa viável (velocidades fora de limite)"})
                continue
            sc, tau, m, prof, v1, v2, m_src = best
            if not self.claim(["time"], a, b, "ramp"):
                continue
            keys = profile_keys(prof, self.tm.src_time(a), self.fps)
            rid = f"RAMP_{made + 1:03d}"
            self.tm.insert_window(a, b, keys, {"id": rid, "kind": "SPEED_RAMP", "neutral": True})
            made += 1
            curve = [round(100 * v) for _, v in prof.pts]
            hit_desc = (f"flash da própria imagem (fonte f{int(round(m_src * self.src_fps))})" if vis_hit is not None
                        else "trecho de movimento contínuo")
            self.event("SPEED_RAMP", a, b, detector=f"shot {s['id']} com movimento HIGH ({s['camera']}) + {m['type']} em f{tau}",
                       action=["Time Remap", "Speed Ramp", "Force Motion Blur nas velocidades > 150%"], recipe=rid,
                       params={"speed_curve_pct": curve, "breakpoints": [int(f) for f, _ in prof.pts],
                               "fast_speed": round(v1, 3), "slow_speed": s_slow, "catchup_speed": round(v2, 3),
                               "hit_out_frame": tau, "hit_source_frame": int(round(m_src * self.src_fps)),
                               "interpolation": "Pixel Motion" if interp else "nativa (sem duplicar frames)",
                               "motion_blur_shutter": [round(min(360, 180 * max(0, v - 1)), 1) for _, v in prof.pts]},
                       intensity=round(min(1.0, 0.5 + 0.1 * v1), 3),
                       easing="rampas lineares de velocidade (keys Bezier com velocidade explícita, influência 33.3%)",
                       audio_sync={"type": m["type"], "frame": tau, "offset_frames": 0},
                       dependencies=["PRE_SOURCE_REMAP/FOOTAGE.timeremap", "VIDEO.CC Force Motion Blur"],
                       priority=2, source={"shot": s["id"]},
                       why=f"Movimento rápido sustentado + golpe de áudio; a rampa leva o {hit_desc} "
                           f"exatamente ao {m['type']} e devolve a sincronia na borda do shot.",
                       expected="Sensação de aceleração física, slow-mo no golpe, sem desalinhar a música.")
            if interp:
                self.warnings.append(f"{rid}: slow-motion de {int(s_slow * 100)}% em footage de {self.src_fps:g} fps "
                                     "exige interpolação (Pixel Motion) — verificar artefatos de borda.")

    # ------------------------------------------------------------ FASE 29 freeze
    def _freeze(self):
        fc = self.cfg["freeze"]
        budget = int(self.n_out / self.fps / fc["per_seconds"]) or (1 if self.n_out / self.fps >= 8 else 0)
        L = self.fr(fc["len_s"])
        made = 0
        for c in sorted(self.cands, key=lambda c: -c["intensity"]):
            if made >= budget:
                break
            f = c["frame"]
            si = self.v_idx(f)
            s = self.shots_out[self._shot_at(f)]
            conf = self.track["conf"][si] or 0.0
            moving = s["motion_level"] != "LOW" or (self.an["motion_per_frame"]["obj"][si] > 2.0 * self.S)
            if c["intensity"] < 0.6 or conf < fc["min_track_conf"] or not moving:
                continue
            a, b = f, f + L
            if any(abs(x["shown_at"] - f) < self.k30(6) or a < x["shown_at"] < b + self.k30(6) for x in self.cuts):
                continue
            if b + self.k30(6) >= s["out_end"] or not self.tm.free(a - 1, b + 1):
                continue
            if not self.claim(["time", "zoom"], a, b + self.k30(8), "freeze"):
                continue
            src_t = self.tm.src_time(a)
            keys = [Key(a, src_t, in_speed=1.0, out_speed=0.0, interp_in="LINEAR", interp_out="HOLD"),
                    Key(b, self.tm.src_time(b), in_speed=1.0, out_speed=1.0, interp_in="HOLD", interp_out="LINEAR")]
            fid = f"FREEZE_{made + 1:03d}"
            self.tm.insert_window(a, b, keys, {"id": fid, "kind": "FREEZE", "neutral": True})
            made += 1
            zoom_keys = [Key(a, 100.0, out_speed=0.0, out_infl=0.5),
                         Key(b, 110.0, in_speed=4.0 / (L / self.fps), in_infl=0.4, out_speed=0.0, out_infl=0.15),
                         Key(b + self.k30(8), 100.0, in_speed=0.0, in_infl=0.6)]
            self.add_keys("CAMERA_ZOOM", "transform.scale", [Key(k.f, [k.v, k.v], k.in_speed, k.out_speed, k.in_infl,
                                                                 k.out_infl, k.interp_in, k.interp_out) for k in zoom_keys])
            self.freeze_events = getattr(self, "freeze_events", []) + [(a, b, si)]
            self.markers["MARKERS_IMPACT"].append({"frame": b, "comment": self._shake_comment(9 * self.S, 16, 9, 0.6, 0, 0.3, 90 + made)})
            self.claim(["flash"], b, b + self.k30(6), fid)
            self._flash_keys(b, 0.45, [1.0, 0.97, 0.92], self.k30(6))
            self.event("FREEZE", a, b + self.k30(8), detector=f"{'/'.join(sorted(c['types']))} intensidade {c['intensity']} + sujeito rastreado (conf {conf:.2f}) em movimento",
                       action=["Freeze (Time Remap HOLD)", "Push-in 100→110%", "Brackets no sujeito", "Release: punch + flash + shake"],
                       recipe=fid, params={"freeze_frames": L, "source_frame": int(round(src_t * self.src_fps)),
                                           "zoom": [100, 110, 100], "release_flash": 0.45},
                       sequence="ACTION → FREEZE → ZOOM → GRAPHIC → RELEASE → ACTION",
                       intensity=c["intensity"], easing="ease-in no push, release exponencial",
                       audio_sync={"type": "/".join(sorted(c["types"])), "frame": f, "offset_frames": 0},
                       dependencies=["PRE_SOURCE_REMAP/FOOTAGE.timeremap", "CAMERA_ZOOM.scale", "GFX_BRACKETS", "VFX_FLASH"],
                       priority=1, scores=c["scores"],
                       why="Golpe forte coincidindo com sujeito nítido em movimento: congelar dá leitura ao momento "
                           "e o release devolve energia; neutro no tempo (pula os frames congelados).",
                       expected="Pausa dramática legível sem perder a sincronia musical.")
            self.text_slots.append({"frame": a + self.k30(2), "end": b, "kind": "freeze", "src": si})

    def _shot_at(self, f):
        for i, s in enumerate(self.shots_out):
            if s["out_start"] <= f < s["out_end"]:
                return i
        return len(self.shots_out) - 1

    # ------------------------------------------------------------ FASE 46/47/56 transições
    def _transitions(self):
        prev_kind, last_nonhard = None, -10 ** 9
        n_hard = 0
        for i, c in enumerate(self.cuts):
            f = c["shown_at"]
            if f <= 2 or f >= self.n_out - 2:
                continue
            si = self._shot_at(f)
            s_in = self.shots_out[si]
            s_out = self.shots_out[max(0, si - 1)]
            near = [m for m in self.amarks if abs(m["out"] - f) <= 1]
            types = {m["type"] for m in near}
            band = self.band[f]
            e = float(self.E[f])
            opts = {"HARD_CUT": 0.5 + (0.2 if band in ("LOW", "MEDIUM") else 0) + (0.1 if types else 0)
                    + (0.25 if self.in_breath(f) or self.in_breath(min(self.n_out - 1, f + self.k30(4))) else 0)}
            ang_o, ang_i = s_out.get("content_angle_deg"), s_in.get("content_angle_deg")
            cont = None
            if ang_o is not None and s_out["motion_level"] != "LOW":
                if ang_i is not None:
                    dd = abs((ang_o - ang_i + 180) % 360 - 180)
                    cont = float(np.cos(np.radians(dd)))
                else:
                    cont = 0.3
                if cont > -0.5:
                    opts["WHIP"] = 0.55 + 0.25 * max(0, cont) + 0.1 * e
            if types & {"DROP", "ENTRY"} or s_out.get("zoom_total", 1) > 1.03:
                opts["ZOOM_THROUGH"] = 0.6 + (0.3 if types & {"DROP", "ENTRY"} else 0)
            dl = abs(s_in["luma"] - s_out["luma"])
            if types & {"KICK", "SNARE", "DROP", "ENTRY"} and (dl >= 0.06 or band == "EXTREME"):
                opts["FLASH_CUT"] = 0.5 + 0.3 * min(1, dl / 0.3) + 0.1
            if band == "EXTREME" and f - getattr(self, "_last_glitch", -10 ** 9) > self.fr(10):
                opts["GLITCH_CUT"] = 0.45 + 0.2 * e
            if self.in_breath(f) or self.in_breath(min(self.n_out - 1, f + self.k30(4))):
                for k in list(opts):
                    if k != "HARD_CUT":
                        opts[k] -= 0.3
            if prev_kind and prev_kind != "HARD_CUT" and prev_kind in opts:
                del opts[prev_kind]     # nunca a mesma transição duas vezes seguidas
            hard_share = n_hard / max(1, i)
            if i >= 2 and hard_share < 0.35:
                opts["HARD_CUT"] += 0.25
            ranked = sorted(opts.items(), key=lambda kv: -kv[1])
            choice = ranked[0][0]
            ab = None
            if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.1:
                ab = self._ab(f, ranked[0][0], ranked[1][0], cont, types, e, last_nonhard)
                choice = ab["chosen"]
            # FASE 47 — match cut (anotação): sujeito na mesma posição dos dois lados
            roi_o = (self.roi["x"][self.v_idx(f - 1)], self.roi["y"][self.v_idx(f - 1)])
            roi_i = (self.roi["x"][self.v_idx(f)], self.roi["y"][self.v_idx(f)])
            match = float(np.hypot(roi_o[0] - roi_i[0], roi_o[1] - roi_i[1])) < 0.08
            self._apply_transition(choice, f, s_out, s_in, types, e, ab, match, opts)
            prev_kind = choice
            if choice == "HARD_CUT":
                n_hard += 1
            else:
                last_nonhard = f

    def _ab(self, f, A, B, cont, types, e, last_nonhard):
        """FASE 56 — compara duas versões por critérios técnicos explícitos."""
        def crit(kind):
            gap = (f - last_nonhard) / self.fps
            leg = 1.0 if kind == "HARD_CUT" else float(np.clip(gap / 2.0, 0, 1))
            rhythm = 1.0 if types & {"KICK", "SNARE", "DROP", "ENTRY", "DOWNBEAT", "BEAT"} else 0.5
            target = {"HARD_CUT": 0.3, "WHIP": 0.7, "ZOOM_THROUGH": 0.85, "FLASH_CUT": 0.6, "GLITCH_CUT": 0.9}[kind]
            energy = 1 - abs(target - e)
            continuity = (max(0.0, cont) if cont is not None else 0.2) if kind == "WHIP" else 0.6
            impact = target
            tot = 0.25 * leg + 0.2 * rhythm + 0.25 * energy + 0.15 * continuity + 0.15 * impact
            return {"legibility": round(leg, 2), "rhythm": rhythm, "energy_match": round(energy, 2),
                    "continuity": round(continuity, 2), "impact": impact, "total": round(tot, 3)}
        ca, cb = crit(A), crit(B)
        chosen = A if ca["total"] >= cb["total"] else B
        res = {"version_a": {"kind": A, **ca}, "version_b": {"kind": B, **cb}, "chosen": chosen}
        self.decisions.append({"system": "A/B", "frame": f, **res})
        return res

    def _apply_transition(self, kind, f, s_out, s_in, types, e, ab, match, opts):
        inten = round(float(np.clip(0.4 + 0.6 * e, 0, 1)), 3)
        sync = {"type": "/".join(sorted(types)) if types else "none", "frame": f, "offset_frames": 0}
        common = dict(audio_sync=sync, intensity=inten, priority=2, ab_test=ab, match_cut=match,
                      options_scored={k: round(v, 3) for k, v in opts.items()},
                      source={"shot_out": s_out["id"], "shot_in": s_in["id"]})
        if kind == "HARD_CUT":
            self.event("CUT", f, f + 1, detector="fronteira de shot", recipe="HARD_CUT", action=["Corte seco"],
                       params={}, easing="—", dependencies=[],
                       why="Corte limpo: " + ("zona de respiro/energia baixa" if e < 0.45 else "contraste com transições vizinhas")
                       + (" — MATCH CUT: sujeito na mesma posição dos dois lados" if match else ""),
                       expected="Leitura imediata; guarda as transições elaboradas para onde importam.", **common)
            return
        if kind == "WHIP":
            a, b = f - self.k30(4), f + self.k30(5)
            if not (self.claim(["pos", "dirblur"], a, b, "whip")):
                return self._apply_transition("HARD_CUT", f, s_out, s_in, types, e, ab, match, opts)
            ang = np.radians(s_out["content_angle_deg"])
            ux, uy = np.cos(ang), np.sin(ang)
            D = 0.55 * self.W * (0.7 + 0.3 * inten)
            pre, post = (f - 1 - a) / self.fps, (b - f) / self.fps
            for comp, u in (("transform.x", ux), ("transform.y", uy)):
                if abs(u) < 1e-3:
                    continue
                self.add_keys("CAMERA_POSITION", comp, [
                    Key(a, 0.0, out_speed=0.0, out_infl=0.75),
                    Key(f - 1, D * u, in_speed=2.4 * D * u / pre, in_infl=0.25, interp_out="HOLD"),
                    Key(f, -D * u, interp_in="HOLD", out_speed=2.4 * D * u / post, out_infl=0.25),
                    Key(b, 0.0, in_speed=0.0, in_infl=0.75)])
            blur = 60 * self.S * inten
            ae_dir = (s_out["content_angle_deg"] + 90) % 180
            self.add_keys("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0002",
                          [Key(a + 1, 0.0, out_speed=0.0, out_infl=0.7), Key(f, blur, in_speed=0.0, in_infl=0.3, out_speed=0.0, out_infl=0.3),
                           Key(b, 0.0, in_speed=0.0, in_infl=0.7)])
            self.add_keys("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0001",
                          [Key(a, ae_dir, interp_in="HOLD", interp_out="HOLD")])
            self.event("TRANSITION", a, b, detector=f"movimento do shot de saída ({s_out['camera']}) + corte",
                       recipe="WHIP", action=["Whip pan", "Directional Blur", "Motion Tile (bordas espelhadas)"],
                       params={"direction_deg": round(float(s_out["content_angle_deg"]), 1), "distance_px": round(D, 1),
                               "blur_px": round(blur, 1), "ae_blur_direction": round(ae_dir, 1)},
                       easing="ease-in acelerando até o corte, salto, ease-out (influência 75%)",
                       dependencies=["CAMERA_POSITION.x/y", "VFX_DIRBLUR", "VIDEO.Motion Tile"],
                       why=f"Transição derivada do movimento: o conteúdo já se move a {s_out['content_angle_deg']:.0f}°, "
                           f"o whip continua essa direção e o shot seguinte entra pelo lado oposto "
                           f"(continuidade {'alta' if opts.get('WHIP', 0) > 0.75 else 'parcial'}).",
                       expected="Corte invisível por movimento contínuo.", **common)
        elif kind == "ZOOM_THROUGH":
            a, b = f - self.k30(5), f + self.k30(6)
            if not self.claim(["zoom", "distort"], a, b, "zoom_through"):
                return self._apply_transition("HARD_CUT", f, s_out, s_in, types, e, ab, match, opts)
            Z = 28 * (0.6 + 0.4 * inten)
            self.add_keys("CAMERA_ZOOM", "transform.scale", [
                Key(a, [100.0, 100.0], out_speed=0.0, out_infl=0.8),
                Key(f - 1, [100 + Z] * 2, in_speed=3.0 * Z / ((f - 1 - a) / self.fps), in_infl=0.2, interp_out="HOLD"),
                Key(f, [100 + 0.6 * Z] * 2, interp_in="HOLD", out_speed=-2.5 * 0.6 * Z / ((b - f) / self.fps), out_infl=0.2),
                Key(b, [100.0, 100.0], in_speed=0.0, in_infl=0.8)])
            name = f"VFX_DISTORT_T{f:05d}"
            self.layer(name, "adjustment", **{"in": a, "out": b, "comment": "Lente no zoom-through", "group": "VFX/DISTORTION"})
            self.effect(name, "ADBE Optics Compensation", "LENS", {"1": 0.0})
            self.add_keys(name, "effect:LENS:1", pulse(a + 1, 55 * inten, f - a - 1, b - f, self.fps))
            self.layers[name]["expressions"]["effect:LENS:1"] = "value * " + _k("VFX_INTENSITY", "TRANSITION_INTENSITY")
            self.event("TRANSITION", a, b, detector="corte em " + ("/".join(sorted(types & {"DROP", "ENTRY"})) or "push-in"),
                       recipe="ZOOM_THROUGH", action=["Zoom-through", "Lens Distortion (Optics Compensation)"],
                       params={"zoom_peak_pct": round(100 + Z, 1), "lens_fov_peak": round(55 * inten, 1)},
                       easing="aceleração exponencial até o corte, ease-out no shot novo",
                       dependencies=["CAMERA_ZOOM.scale", name],
                       why="Mudança de seção (entrada/drop) no corte: atravessar o quadro marca o salto de energia.",
                       expected="Sensação de mergulho para dentro da próxima cena.", **common)
        elif kind == "FLASH_CUT":
            a, b = f - 1, f + self.k30(7)
            if not self.claim(["flash"], a, b, "flash_cut"):
                return self._apply_transition("HARD_CUT", f, s_out, s_in, types, e, ab, match, opts)
            tint = self._light_tint(s_in)
            peak = 0.6 + 0.35 * inten
            self._flash_keys(f, peak, tint, b - f)
            self.markers["MARKERS_IMPACT"].append({"frame": f, "comment": self._shake_comment(5 * self.S * inten, 14, 8, 0.3, 0, 0.2, f)})
            self.event("TRANSITION", a, b, detector="corte em golpe de áudio + contraste de luminância "
                       f"{abs(s_in['luma'] - s_out['luma']):.2f}", recipe="FLASH_CUT",
                       action=["Flash de luz no corte", "Shake leve"],
                       params={"flash_peak": round(peak, 2), "tint": tint, "decay_frames": b - f},
                       easing="ataque em 1 frame, decaimento exponencial",
                       dependencies=["VFX_FLASH", "MARKERS_IMPACT"],
                       why="Golpe no corte e diferença grande de luz entre os shots: o flash une as duas exposições.",
                       expected="Corte com impacto luminoso, sem salto brusco de exposição.", **common)
        elif kind == "GLITCH_CUT":
            self._last_glitch = f
            self._glitch(f - self.k30(2), f + self.k30(3), inten, "transição", common)

    # ------------------------------------------------------------ helpers de VFX
    def _light_tint(self, shot):
        r, g, b = shot["mean_rgb"]
        m = max(r, g, b) + 1e-6
        tint = [0.85 + 0.15 * r / m, 0.85 + 0.15 * g / m, 0.85 + 0.15 * b / m]
        return [round(x, 3) for x in tint]

    def _flash_keys(self, f, peak, tint, decay):
        self.add_keys("VFX_FLASH", "transform.opacity", pulse(f - 1, 100 * peak, 1, decay, self.fps))
        self.add_keys("VFX_FLASH", "effect:TINT:2", [Key(f - 1, tint, interp_in="HOLD", interp_out="HOLD")])

    def _shake_comment(self, amp, freq, decay, rot, direction, bias, seed):
        return (f"amp={amp:.2f};freq={freq:.1f};decay={decay:.1f};rot={rot:.2f};dir={direction:.0f};"
                f"bias={bias:.2f};seed={seed}")

    def _glitch(self, a, b, inten, why_ctx, common=None):
        if not self.claim(["glitch", "rgb"], a, b, "glitch"):
            return
        name = f"VFX_GLITCH_{a:05d}"
        self.layer(name, "adjustment", **{"in": a, "out": b, "comment": "Glitch é EVENTO (curto)", "group": "VFX/GLITCH"})
        self.effect(name, "ADBE Turbulent Displace", "DISPLACE", {"2": 0.0, "3": 12.0, "5": 2.0})
        self.effect(name, "ADBE Noise", "NOISE", {"1": 0.0})
        steps_amt, steps_rgb, steps_noise = [], [], []
        for f in range(a, b + 1):
            on = f < b
            amt = float(self.rng.uniform(40, 140)) * inten * self.S if on else 0.0
            steps_amt.append((f, round(amt, 1)))
            steps_rgb.append((f, round(float(self.rng.uniform(6, 22)) * inten * self.S * (1 if self.rng.random() > 0.3 else -1), 1) if on else 0.0))
            steps_noise.append((f, round(float(self.rng.uniform(4, 12)) * inten, 1) if on else 0.0))
        hk = lambda st: [Key(f, v, interp_in="HOLD", interp_out="HOLD") for f, v in st]
        self.add_keys(name, "effect:DISPLACE:2", hk(steps_amt))
        self.add_keys(name, "effect:NOISE:1", hk(steps_noise))
        self.layers[name]["expressions"]["effect:DISPLACE:2"] = "value * " + _k("VFX_INTENSITY")
        self.slider_keys("CTRL_VFX", "RGB_SPLIT_PX", hk(steps_rgb))
        self._rgb_instance(a, b)
        ev = dict(common or {})
        ev.setdefault("intensity", inten)
        prio = ev.pop("priority", 6)
        for k in ("why", "expected"):
            ev.pop(k, None)
        self.event("GLITCH", a, b, detector=f"energia EXTREME ({why_ctx})", recipe="GLITCH",
                   action=["Turbulent Displace em degraus", "RGB Split", "Noise"],
                   params={"frames": b - a, "displace_steps": steps_amt[:-1], "rgb_px_steps": steps_rgb[:-1]},
                   easing="HOLD (degraus digitais)", dependencies=[name, "CTRL_VFX.RGB_SPLIT_PX", "VFX_RGB_SPLIT"],
                   why="Pico de energia: o glitch é um evento curto que quebra o fluxo, nunca textura permanente.",
                   expected="Ruptura digital pontual.", priority=prio, **ev)

    def _rgb_instance(self, a, b):
        name = f"VFX_RGB_SPLIT_{a:05d}"
        self.layer(name, "rgbsplit", **{"in": a, "out": b, "parent": "CAMERA_SHAKE", "comment":
                                        "Instância de PRE_RGB_SPLIT só durante o evento (custo zero fora dele)",
                                        "group": "VFX/GLITCH"})

    # ------------------------------------------------------------ FASE 24/48 impactos
    def _impacts(self):
        bud = self.cfg["impact_budget"]
        gap = self.fr(self.cfg["min_impact_gap_s"])
        accepted = []
        trans_frames = {e["audio_sync"]["frame"] for e in self.events if e["category"] in ("TRANSITION", "GLITCH")}
        freeze_frames = [(e["start_frame"], e["end_frame"]) for e in self.events if e["category"] == "FREEZE"]
        prev_recipe = None
        for c in sorted(self.cands, key=lambda c: (-c["intensity"], c["frame"])):
            f = c["frame"]
            if any(a - 1 <= f <= b for a, b in freeze_frames):
                continue
            if any(abs(f - x) <= gap for x in accepted):
                continue
            win = self.fr(2.0)
            n_near = sum(1 for x in accepted if abs(x - f) < win // 2)
            if n_near + 1 > bud[c["band"]] * 2.0 and not ({"DROP", "IMPACT", "ENTRY"} & c["types"]):
                micro = c["intensity"] >= 0.5 and not self.in_breath(f)
                self.decisions.append({"system": "IMPACT", "frame": f, "decision": f"rejeitado: orçamento {c['band']}"
                                       + (" → micro-shake (só representa o golpe)" if micro else "")})
                if micro:
                    amp = 2.0 * self.S
                    self.markers["MARKERS_IMPACT"].append({"frame": f, "comment": self._shake_comment(amp, 12, 12, 0.0, 0, 0.0, f)})
                    self.event("SHAKE", f, f + self.k30(5), detector="/".join(sorted(c["types"])) + " acima do orçamento da faixa",
                               recipe="MICRO_SHAKE", action=["Micro-shake"], params={"shake_px": round(amp, 2)},
                               intensity=0.2, easing="decaimento exponencial (expression)", scores=c["scores"],
                               audio_sync={"type": "/".join(sorted(c["types"])), "frame": f, "offset_frames": 0},
                               dependencies=["CAMERA_SHAKE", "MARKERS_IMPACT"], priority=6,
                               why="Golpe forte, mas o orçamento de densidade da faixa já foi usado: um micro-shake "
                                   "mantém o golpe visível sem somar mais um efeito pesado.",
                               expected="Ritmo contínuo com hierarquia (golpes principais continuam maiores).")
                continue
            if any(abs(f - x) <= 1 for x in trans_frames):
                # a transição já ocupa a câmera: só reforço de shake sincronizado
                inten = c["intensity"]
                self.markers["MARKERS_IMPACT"].append({"frame": f, "comment": self._shake_comment(
                    lerp(self.cfg["tiers"]["medium"]["shake"], inten) * self.S, 15, 8, 0.4, 0, 0.3, f)})
                accepted.append(f)
                continue
            accepted.append(f)
            self._impact(c, prev_recipe)
            prev_recipe = c.get("recipe")

    def _choose_recipe(self, tier, prev, f):
        opts = list(RECIPES[tier])
        caps = self.cfg["recipe_caps"]
        total = sum(self.recipe_count.values()) + 1
        w = []
        for r in opts:
            wt = 1.0 / (1 + self.recipe_count.get(r, 0))
            if r == prev and tier != "small":
                wt = 0.0
            if r in caps and (self.recipe_count.get(r, 0) + 1) / max(total, 6) > caps[r]:
                wt *= 0.1
            if self.in_breath(f) and r in ("RGB_HIT", "DISTORT_HIT", "PUNCH_SHAKE"):
                wt = 0.0
            w.append(wt)
        w = np.array(w)
        if w.sum() <= 0:
            return opts[0]
        return opts[int(self.rng.choice(len(opts), p=w / w.sum()))]

    def _impact(self, c, prev):
        f = c["frame"]
        inten = c["intensity"]
        tier = "small" if inten < 0.45 else ("medium" if inten < 0.72 else "large")
        x = (inten - {"small": 0.0, "medium": 0.45, "large": 0.72}[tier]) / {"small": 0.45, "medium": 0.27, "large": 0.28}[tier]
        T = self.cfg["tiers"][tier]
        recipe = self._choose_recipe(tier, prev, f)
        attack = self.k30(2 if tier != "small" else 3)
        release = self.k30({"small": 7, "medium": 9, "large": 12}[tier])
        # FASE 40: o key de repouso fica em f-1 → no frame do golpe a imagem JÁ está em movimento
        # (com o repouso em f, o primeiro frame visível do punch cairia 1–2 frames depois do kick)
        k0 = f - 1
        a, b = k0, k0 + attack + release
        chans = RECIPE_CHANNELS[recipe]
        free = [ch for ch in chans if self.free(ch, a - 1, b)]
        if "zoom" not in free:
            self.decisions.append({"system": "IMPACT", "frame": f, "decision": "câmera ocupada (prioridade maior) — só shake"})
            recipe = "SHAKE_ONLY"
        self.recipe_count[recipe] = self.recipe_count.get(recipe, 0) + 1
        c["recipe"] = recipe
        scale = lerp(T["scale"], x)
        shake = lerp(T["shake"], x) * self.S
        rot = lerp(T["rot"], x)
        blur = lerp(T["blur"], x) * self.S
        flash = lerp(T["flash"], x)
        si = self.v_idx(f)
        dx, dy = self.an["motion_per_frame"]["dx"][si], self.an["motion_per_frame"]["dy"][si]
        mdir = float(np.degrees(np.arctan2(dy, dx))) if np.hypot(dx, dy) > 1 else 90.0
        params, actions, deps = {}, [], []
        if recipe != "SHAKE_ONLY":
            self.claim([ch for ch in chans if ch in free], a - 1, b, f"impact{f}")
            k = punch(k0, 100.0, 100.0 + scale, attack, release, self.fps)
            self.add_keys("CAMERA_ZOOM", "transform.scale", [Key(q.f, [q.v, q.v], q.in_speed, q.out_speed, q.in_infl, q.out_infl,
                                                                 q.interp_in, q.interp_out) for q in k])
            params["scale"] = [100.0, round(100 + scale, 2), round(100 + 0.12 * scale, 2), 100.0]
            params["scale_key_frames"] = [int(q.f) for q in k]
            actions.append("Scale Punch")
            deps.append("CAMERA_ZOOM.scale")
        if recipe == "ROT_KICK" and "rot" in free:
            sgn = 1 if self.rng.random() > 0.5 else -1
            r_amt = sgn * max(rot * 2.0, 0.8)
            self.add_keys("CAMERA_ROTATION", "transform.rotation", punch(k0, 0.0, r_amt, attack, release, self.fps, settle_ratio=-0.25))
            params["rotation_deg"] = [0, round(r_amt, 2), round(-0.25 * r_amt, 2), 0]
            actions.append("Rotation Kick")
            deps.append("CAMERA_ROTATION.rotation")
        if recipe in ("FLASH_PUNCH", "DISTORT_HIT") and "flash" in free:
            tint = self._light_tint(self.shots_out[self._shot_at(f)])
            self._flash_keys(f, flash, tint, self.k30(7))
            params["flash"] = [0, round(flash, 2), 0]
            params["flash_tint"] = tint
            actions.append("Exposure Flash")
            deps.append("VFX_FLASH")
        if recipe == "BLUR_HIT" and "dirblur" in free:
            ae_dir = (mdir + 90) % 180
            self.add_keys("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0002", pulse(k0, blur * 1.6, 1, self.k30(6), self.fps))
            self.add_keys("VFX_DIRBLUR", "effect:DIRBLUR:ADBE Motion Blur-0001", [Key(k0, ae_dir, interp_in="HOLD", interp_out="HOLD")])
            params["dir_blur_px"] = [0, round(blur * 1.6, 1), 0]
            params["dir_blur_angle_ae"] = round(ae_dir, 1)
            actions.append("Directional Blur")
            deps.append("VFX_DIRBLUR")
        if recipe == "DISTORT_HIT" and "distort" in free:
            name = f"VFX_DISTORT_{f:05d}"
            self.layer(name, "adjustment", **{"in": k0, "out": b, "comment": "Distorção curta acompanhando o golpe", "group": "VFX/DISTORTION"})
            self.effect(name, "ADBE Turbulent Displace", "DISPLACE", {"2": 0.0, "3": 60.0 * self.S, "5": 1.0})
            self.add_keys(name, "effect:DISPLACE:2", pulse(k0, 45 * inten, 1, b - k0 - 1, self.fps))
            self.layers[name]["expressions"]["effect:DISPLACE:2"] = "value * " + _k("VFX_INTENSITY")
            params["displace_amount"] = [0, round(45 * inten, 1), 0]
            actions.append("Turbulent Distortion")
            deps.append(name)
        if recipe == "RGB_HIT" and "rgb" in free:
            px = 14 * self.S * inten
            self.slider_keys("CTRL_VFX", "RGB_SPLIT_PX", pulse(k0, px, 1, self.k30(6), self.fps))
            self._rgb_instance(k0, f + self.k30(7))
            params["rgb_px"] = [0, round(px, 1), 0]
            actions.append("RGB Split")
            deps.append("VFX_RGB_SPLIT")
        if recipe in ("PUNCH_SHAKE", "FLASH_PUNCH", "BLUR_HIT", "DISTORT_HIT", "RGB_HIT", "ROT_KICK", "SHAKE_ONLY") and shake > 0.2:
            bias = 0.6 if recipe in ("BLUR_HIT", "ROT_KICK") else 0.25
            self.markers["MARKERS_IMPACT"].append({"frame": f, "comment": self._shake_comment(
                shake, 18 if tier == "large" else 14, 10 if tier == "large" else 8, rot * 0.5, mdir, bias, f)})
            params["shake_px"] = round(shake, 2)
            params["shake_dir_deg"] = round(mdir, 1)
            actions.append("Position Shake" if bias < 0.5 else "Directional Shake")
            deps.append("CAMERA_SHAKE (expression + MARKERS_IMPACT)")
        types = "/".join(sorted(c["types"]))
        cat = "PUNCH" if recipe in ("PUNCH_IN", "PUNCH_SHAKE", "ROT_KICK") else (
            {"FLASH_PUNCH": "FLASH", "BLUR_HIT": "BLUR", "DISTORT_HIT": "DISTORTION", "RGB_HIT": "VFX", "SHAKE_ONLY": "SHAKE"}[recipe])
        reasons = []
        if c["scores"]["audio"] >= 0.5:
            reasons.append(f"golpe de áudio forte ({types})")
        elif c["scores"]["audio"] > 0:
            reasons.append(f"golpe de áudio ({types})")
        if c["scores"]["motion"] >= 0.5:
            reasons.append("movimento alto")
        if c["scores"]["importance"] >= 0.5:
            reasons.append("início de seção/downbeat")
        if "FLASH_VISUAL" in c["types"]:
            reasons.append("flash da própria imagem")
        reasons.append(f"energia {c['band']}")
        self.event(cat, a, b, detector=types + " + energia local", recipe=recipe, tier=tier, action=actions,
                   params=params, intensity=inten, scores=c["scores"],
                   easing="fast-out / retorno exponencial com settle (Bezier)",
                   audio_sync={"type": types, "frame": f, "offset_frames": 0}, dependencies=deps, priority=3,
                   source={"shot": self.shots_out[self._shot_at(f)]["id"], "src_frame": si},
                   why=" + ".join(reasons) + ".",
                   expected={"small": "pulso sutil que marca o ritmo", "medium": "impacto físico perceptível",
                             "large": "golpe máximo, ponto alto da seção"}[tier])

    # ------------------------------------------------------------ montagem
    def run(self):
        from . import systems
        self._prepare()
        self._base_edit()
        self._energy_out()
        self.accent = systems._accent(self)
        self._make_base_layers()
        self._breath()
        self._candidates()
        self._freeze()      # narrativa primeiro (FASE 55): o freeze reserva o tempo antes das rampas
        self._ramps()
        self._transitions()
        self._impacts()
        systems.camera(self)
        systems.zoom_cuts(self)
        systems.light_events(self)
        systems.glitch_events(self)
        systems.particles(self)
        systems.graphics_and_text(self)
        systems.color(self)
        systems.audio_react(self)
        systems.markers(self)
        systems.finalize_ramp_blur(self)
        return systems.assemble(self)

    def _make_base_layers(self):
        from . import systems
        systems.base_layers(self)


def _k(*sliders):
    from ..generate.expressions import k_expr
    return k_expr(*sliders)


def decide(analysis, cfg):
    return Engine(analysis, cfg).run()
