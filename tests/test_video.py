"""Mede a análise de vídeo (shots, flash, câmera, ROI, tracking, densidade adaptativa)."""
import numpy as np
import pytest


@pytest.fixture(scope="session")
def analysis(synthetic, tmp_path_factory):
    from conde.analysis.pipeline import analyze
    path, _ = synthetic
    return analyze(path, tmp_path_factory.mktemp("an"), log=lambda *_: None)


def test_cuts_exact_and_flash_not_cut(analysis, synthetic):
    _, truth = synthetic
    assert [c["frame"] for c in analysis["cuts"]] == truth["cuts"]
    assert [f["frame"] for f in analysis["visual_flashes"]] == [truth["non_cut_flash"]]


def test_camera_direction(analysis, synthetic):
    _, truth = synthetic
    for s, ts in zip(analysis["shots"], truth["shots"]):
        vx, vy = ts["content_velocity"]
        mx, my = s["content_velocity_px"]
        assert abs(mx - vx) < 1.0 and abs(my - vy) < 1.0, (s["id"], mx, my, vx, vy)
    cams = [s["camera"] for s in analysis["shots"]]
    assert cams[1].startswith("pan → direita") and cams[2].startswith("tilt ↑") and cams[3].startswith("pan ← esquerda")
    assert cams[0] == "estática" and cams[4] == "estática"


def test_subject_tracking(analysis, synthetic):
    _, truth = synthetic
    tr = analysis["track"]
    W, H = truth["width"], truth["height"]
    for si, sub in truth["subjects"].items():
        sh = truth["shots"][int(si)]
        errs = []
        for f in range(sh["start"], sh["end"]):
            gx = sub["start"][0] + sub["velocity"][0] * (f - sh["start"])
            gy = sub["start"][1] + sub["velocity"][1] * (f - sh["start"])
            errs.append(np.hypot(tr["x"][f] * W - gx, tr["y"][f] * H - gy))
        assert np.median(errs) < 0.03 * W, (si, np.median(errs))     # < 3% da largura
        assert abs(errs[-1] - errs[0]) < 0.02 * W, (si, errs[0], errs[-1])   # deriva < 2%


def test_adaptive_density(analysis, synthetic):
    _, truth = synthetic
    hist = analysis["adaptive"]["stride_histogram"]
    assert set(hist) <= {1, 2, 4, 8} and hist.get(1, 0) > 0 and hist.get(8, 0) > 0
    # frames críticos (cortes) sempre analisados densamente
    dense = {int(k) for k in analysis["samples"]}
    assert all(c in dense for c in truth["cuts"])
    assert len(dense) < 0.7 * truth["frames"]


def test_color_stats_detect_underexposed_blue(analysis):
    last = analysis["shots"][-1]
    assert last["luma"] < 0.2 and last["mean_rgb"][2] > last["mean_rgb"][0] + 0.05
    assert "COLOR_FIX" in last["editing_potential"]["tags"]
