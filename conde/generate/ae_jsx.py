"""FASE 49/51 — gera o script do After Effects a partir da timeline.

Saída: um único .jsx autocontido (timeline embutida como literal ES3 + biblioteca),
executável em File > Scripts > Run Script File. Versionado (FASE 62).
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "ae" / "lib" / "conde_core.jsx"


def render_tests(tl, n=4):
    """Trechos críticos para render de teste (FASE 63): eventos mais fortes ±0.5 s."""
    fps = tl["comp"]["fps"]
    pick = [e for e in tl["events"] if e["category"] in ("TRANSITION", "SPEED_RAMP", "FREEZE", "PUNCH", "FLASH",
                                                         "DISTORTION", "TEXT", "PARTICLE")]
    pick.sort(key=lambda e: -e.get("intensity", 0))
    out, used = [], []
    for e in pick:
        a = max(0, e["start_frame"] - int(fps * 0.5))
        b = min(tl["comp"]["duration_frames"], e["end_frame"] + int(fps * 0.5))
        if any(a < y and x < b for x, y in used):
            continue
        used.append((a, b))
        out.append({"id": e["id"], "category": e["category"], "start_frame": a, "end_frame": b})
        if len(out) >= n:
            break
    return out


def write_jsx(tl, out_dir, source_path=None):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tl = dict(tl)
    tl["render_tests"] = render_tests(tl)
    if source_path:
        tl["project"] = dict(tl["project"], source=str(Path(source_path).resolve()))
    # o AE só precisa do que constrói: remove campos analíticos volumosos
    slim = {k: v for k, v in tl.items() if k not in ("energy_per_frame", "decisions")}
    data = json.dumps(slim, ensure_ascii=True, separators=(",", ":"))
    name = f"BUILD_{tl['project']['name']}_v{tl['project']['version']:03d}.jsx"
    header = (
        "// =====================================================================\n"
        f"// {name} — gerado pelo pipeline CONDE\n"
        "// Abrir no After Effects: File > Scripts > Run Script File...\n"
        "// (habilite: Preferences > Scripting & Expressions > Allow Scripts to Write Files)\n"
        f"// Comp: {tl['comp']['width']}x{tl['comp']['height']} @ {tl['comp']['fps']:g} fps, "
        f"{tl['comp']['duration_frames']} frames | eventos: {len(tl['events'])} | camadas: {len(tl['layers'])}\n"
        "// =====================================================================\n"
    )
    body = header + "var CONDE_TL = " + data + ";\n\n" + CORE.read_text(encoding="utf-8") + \
        "\n\n(function () {\n    var r = CONDE.build(CONDE_TL);\n" \
        "    alert(\"CONDE: \" + r.stats.layers + \" camadas, \" + r.stats.keys + \" keys, \" + r.stats.expressions +\n" \
        "          \" expressions, \" + r.stats.failures + \" falhas.\\nVeja o BUILD_LOG ao lado do .aep.\");\n})();\n"
    path = out_dir / name
    path.write_text(body, encoding="utf-8")
    return path, tl["render_tests"]
