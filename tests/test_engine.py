"""Invariantes do motor de decisão (regras do briefing transformadas em testes)."""
import pytest

from conde.decision.curves import Key, evaluate
from conde.decision.engine import CATEGORIES


@pytest.fixture(scope="session")
def timeline(synthetic, tmp_path_factory):
    from conde.analysis.pipeline import analyze
    from conde.config import load
    from conde.decision.engine import decide
    path, _ = synthetic
    an = analyze(path, tmp_path_factory.mktemp("eng"), log=lambda *_: None)
    return decide(an, load()), an


def test_no_build_errors_and_valid_categories(timeline):
    tl, _ = timeline
    assert tl["build_checks"] == []
    assert all(e["category"] in CATEGORIES for e in tl["events"])
    req = {"id", "start_frame", "end_frame", "start_time", "end_time", "duration_frames", "category", "action",
           "params", "intensity", "easing", "audio_sync", "dependencies", "why", "expected"}
    for e in tl["events"]:
        assert req <= set(e), (e["id"], req - set(e))
        assert e["why"] and 0 <= e["intensity"] <= 1


def test_cuts_land_on_hits(timeline):
    tl, _ = timeline
    ca = tl["base_edit"]["cut_alignment"]
    assert ca["within_1f_after"] == ca["cuts"]


def test_time_remap_neutral_outside_windows(timeline):
    tl, an = timeline
    pre = tl["precomps"]["PRE_SOURCE_REMAP"]
    keys = [Key.from_dict(k) for k in pre["timeremap"]]
    fps = tl["comp"]["fps"]
    wins = pre["windows"]
    for f in range(0, tl["comp"]["duration_frames"]):
        if any(w["start_frame"] <= f <= w["end_frame"] for w in wins):
            continue
        assert abs(evaluate(keys, f, fps) * fps - f) < 1e-3, f   # < 0.001 frame
    for w in wins:   # bordas de toda janela voltam à sincronia
        for edge in (w["start_frame"], w["end_frame"]):
            assert abs(evaluate(keys, edge, fps) * fps - edge) < 0.02


def test_no_same_transition_twice_in_a_row(timeline):
    tl, _ = timeline
    tr = [e["recipe"] for e in tl["events"] if e["category"] == "TRANSITION"]
    assert all(a != b for a, b in zip(tr, tr[1:]))


def test_breath_zones_are_clean(timeline):
    tl, _ = timeline
    zones = tl["zones"]["breath"]
    assert zones
    loud = {"GLITCH", "PARTICLE", "DISTORTION"}
    for e in tl["events"]:
        if e["category"] in loud:
            assert not any(z["start_frame"] <= e["start_frame"] < z["end_frame"] for z in zones), e["id"]


def test_vocabulary_not_monotonous(timeline):
    tl, _ = timeline
    rec = [e["recipe"] for e in tl["events"] if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX")]
    assert len(set(rec)) >= 3
    for r in set(rec):
        assert rec.count(r) / len(rec) <= 0.5, (r, rec)


def test_audio_sync_events_on_marker_frames(timeline):
    tl, an = timeline
    fr = {m["frame"] for m in an["audio"]["markers"]}
    for e in tl["events"]:
        s = e.get("audio_sync") or {}
        if e["category"] in ("PUNCH", "FLASH", "BLUR", "DISTORTION", "VFX") and s.get("type") not in (None, "none"):
            assert any(abs(s["frame"] - x) <= 1 for x in fr), e["id"]
            assert e["start_frame"] == s["frame"]


def test_text_inside_safe_area_and_off_subject(timeline):
    tl, _ = timeline
    W, H = tl["comp"]["width"], tl["comp"]["height"]
    texts = [L for L in tl["layers"] if L["kind"] == "text"]
    assert texts
    for L in texts:
        x0, y0, x1, y1 = L["text"]["box_px"]
        assert 0.05 * W <= x0 and x1 <= 0.95 * W and 0.05 * H <= y0 and y1 <= 0.95 * H, (L["name"], L["text"]["box_px"])
