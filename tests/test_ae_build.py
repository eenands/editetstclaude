"""Validação do JSX do After Effects SEM o After Effects.

1. sintaxe ES3 (acorn) + ausência de APIs ES5 que o ExtendScript não tem;
2. execução completa no mock do object model (tests/js/mock_ae.js);
3. todas as expressions avaliadas num contexto que imita o motor JS do AE;
4. os números das expressions de câmera batem com os espelhos Python do preview.
Limite honesto: o mock codifica o conhecimento da API; só o BUILD_LOG gerado
dentro do AE confirma matchNames/índices de parâmetros no programa real.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from conde.decision.curves import Key, evaluate
from conde.generate import expressions as X
from conde.generate.ae_jsx import write_jsx

JS = Path(__file__).parent / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node indisponível")


@pytest.fixture(scope="session")
def built(timeline, synthetic, tmp_path_factory):
    tl, an = timeline
    path, truth = synthetic
    out = tmp_path_factory.mktemp("ae")
    jsx, _ = write_jsx(tl, out, path)
    meta = json.dumps({"width": truth["width"], "height": truth["height"], "frameRate": truth["fps"],
                       "duration": truth["frames"] / truth["fps"]})
    dump = out / "dump.json"
    r = subprocess.run([NODE, str(JS / "mock_ae.js"), str(jsx), str(path), str(dump), meta], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    samples = out / "expr.json"
    h = subprocess.run([NODE, str(JS / "expr_harness.js"), str(dump), str(samples), "2"], capture_output=True, text=True)
    return {"tl": tl, "jsx": jsx, "mock_out": r.stdout, "dump": json.loads(dump.read_text()),
            "harness_rc": h.returncode, "harness_out": h.stdout, "expr": json.loads(samples.read_text())}


def test_es3_syntax(built):
    if not (JS / "node_modules" / "acorn").exists():
        pytest.skip("acorn não instalado (cd tests/js && npm install)")
    r = subprocess.run([NODE, str(JS / "es3_check.js"), str(built["jsx"])], capture_output=True, text=True,
                       env={"ACORN_PATH": str(JS / "node_modules" / "acorn")})
    assert r.returncode == 0, r.stderr + r.stdout


def test_mock_build_has_no_failures(built):
    assert "0 falhas" in built["mock_out"], built["mock_out"]
    assert "[ ]" not in built["mock_out"], built["mock_out"]        # checklist FASE 64
    master = built["dump"]["comps"]["MASTER_EDIT"]
    assert [L["name"] for L in master["layers"]] == [L["name"] for L in built["tl"]["layers"]]
    assert len(built["dump"]["folders"]) == 12
    pre = built["dump"]["comps"]["PRE_SOURCE_REMAP"]["layers"][0]
    tr = next(p for p in pre["props"].values() if p["match"] == "ADBE Time Remapping")
    assert len(tr["keys"]) == len(built["tl"]["precomps"]["PRE_SOURCE_REMAP"]["timeremap"])


def test_all_expressions_evaluate(built):
    assert built["harness_rc"] == 0, built["harness_out"]
    assert built["expr"]["errors"] == []
    assert built["expr"]["evaluations"] > 1000


def _layer(tl, name):
    return next(L for L in tl["layers"] if L["name"] == name)


def _keys(L, prop):
    return [Key.from_dict(k) for k in L["keys"][prop]]


def test_camera_expressions_match_python_mirrors(built):
    tl = built["tl"]
    fps, W, H = tl["comp"]["fps"], tl["comp"]["width"], tl["comp"]["height"]
    cam = _layer(tl, "CTRL_CAMERA")
    au = _layer(tl, "CTRL_AUDIO")
    amb = _keys(cam, "effect:AMBIENT_SHAKE:ADBE Slider Control-0001")
    drift = _keys(cam, "effect:DRIFT_SCALE:ADBE Slider Control-0001")
    cut = _keys(cam, "effect:CUT_ZOOM:ADBE Slider Control-0001")
    gate = _keys(au, "effect:GATE_BASS_TO_ZOOM:ADBE Slider Control-0001")
    bass = au["baked"]["effect:BASS:ADBE Slider Control-0001"]["values"]
    punch = _keys(_layer(tl, "CAMERA_ZOOM"), "transform.scale")
    marks = [(m["frame"] / fps, m["comment"]) for m in _layer(tl, "MARKERS_IMPACT")["markers"]]
    S = built["expr"]["samples"]
    pos = S["MASTER_EDIT:CAMERA_SHAKE:ADBE Transform Group/ADBE Position"]
    rot = S["MASTER_EDIT:CAMERA_SHAKE:ADBE Transform Group/ADBE Rotate Z"]
    zoom = S["MASTER_EDIT:CAMERA_ZOOM:ADBE Transform Group/ADBE Scale"]
    assert len(pos) > 100 and len(zoom) > 100
    worst = 0.0
    for (f, p), (_, r) in zip(pos, rot):
        t = f / fps
        ox, oy, rr = X.shake_eval(marks, t, evaluate(amb, f, fps), 1.0)
        worst = max(worst, abs(p[0] - (W / 2 + ox)), abs(p[1] - (H / 2 + oy)), abs(r[0] - rr))
    assert worst < 1e-6, f"shake difere do espelho Python em {worst}"
    worst = 0.0
    for f, s in zoom:
        exp = X.zoom_eval(evaluate(punch, f, fps)[0], 1.0, evaluate(drift, f, fps), evaluate(cut, f, fps),
                          bass[f], evaluate(gate, f, fps), 1.0)
        worst = max(worst, abs(s[0] - exp))
    assert worst < 1e-6, f"zoom difere do espelho Python em {worst}"
