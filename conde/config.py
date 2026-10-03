"""Parâmetros de estilo e limites do motor de decisão (sobrescrevíveis via JSON)."""
import copy
import json

DEFAULT = {
    "project_name": "CONDE_EDIT",
    "version": 1,
    "seed": 7,
    "mode": "auto",                 # auto | music | dialogue
    "comp_fps": "source",          # "source" ou número (ex.: 60) — ver FASE 13
    "allow_interpolation": True,   # slow-motion abaixo da taxa nativa usa Pixel Motion
    "min_speed_interpolated": 0.45,
    "max_speed": 5.5,
    "dialogue": {"trim_silence_min_s": 0.6, "pad_s": 0.12},
    # FASE 42 — orçamento de impactos por faixa de energia (eventos/segundo)
    "impact_budget": {"LOW": 0.35, "MEDIUM": 0.9, "HIGH": 1.6, "EXTREME": 2.2},
    "min_impact_gap_s": 0.2,
    # FASE 48 — tabelas de impacto (px referidos a 1920 de largura)
    "tiers": {
        "small": {"scale": [2.0, 4.0], "shake": [0.0, 2.5], "blur": [0, 5], "flash": [0.0, 0.0], "rot": [0.0, 0.4]},
        "medium": {"scale": [5.0, 8.0], "shake": [3.0, 8.0], "blur": [6, 14], "flash": [0.25, 0.5], "rot": [0.3, 1.0]},
        "large": {"scale": [9.0, 13.0], "shake": [8.0, 16.0], "blur": [14, 24], "flash": [0.55, 0.9], "rot": [0.6, 2.0]},
    },
    "recipe_caps": {"RGB_HIT": 0.15, "DISTORT_HIT": 0.12, "FLASH_PUNCH": 0.3, "PUNCH_SHAKE": 0.4},
    "breath": {"min_len_s": 1.5, "energy_pct": 30, "target_share": 0.12},
    "ramps": {"per_seconds": 6.0, "s_slow": 0.5, "hold_s": 0.27, "ramp_s": 0.2, "target_v1": 2.2},
    "freeze": {"per_seconds": 20.0, "len_s": 0.3, "min_track_conf": 0.6},
    "glitch": {"max_share": 0.03, "max_len_frames_30": 6},
    "text": {
        "font": "Montserrat-ExtraBold", "fallback_font": "Arial-BoldMT",
        "min_hold_s": 1.2, "placeholders": ["TEXTO 01", "TEXTO 02", "TEXTO 03", "TEXTO 04"],
        "copy": [],                 # [{"time": s, "text": "..."}] — copy real do usuário, se houver
    },
    "color": {"exposure_target": 0.40, "exposure_strength": 0.7, "wb_strength": 0.35, "max_stops": 1.0,
              "max_wb_stops": 0.35, "look": {"contrast": 12, "vibrance": 18, "vignette": 0.35}},
    "controllers": {"GLOBAL_INTENSITY": 1.0, "SHAKE_INTENSITY": 1.0, "ZOOM_INTENSITY": 1.0, "VFX_INTENSITY": 1.0,
                    "GLOW_INTENSITY": 1.0, "TEXT_INTENSITY": 1.0, "COLOR_INTENSITY": 1.0,
                    "TRANSITION_INTENSITY": 1.0, "PARTICLE_INTENSITY": 1.0, "AUDIO_REACT_INTENSITY": 1.0},
}


def load(path=None, overrides=None):
    cfg = copy.deepcopy(DEFAULT)

    def merge(a, b):
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = v
    if path:
        merge(cfg, json.loads(open(path, encoding="utf-8").read()))
    if overrides:
        merge(cfg, overrides)
    return cfg
