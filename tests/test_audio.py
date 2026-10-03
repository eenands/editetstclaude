"""Mede o analisador de áudio contra o gabarito do clipe sintético."""
from tests.conftest import match


def _times(a, typ):
    return [m["time"] for m in a["markers"] if m["type"] == typ]


def _frames(a, typ):
    return [m["frame"] for m in a["markers"] if m["type"] == typ]


def test_tempo(audio_result, synthetic):
    _, truth = synthetic
    assert abs(audio_result["tempo"]["bpm"] - truth["audio"]["bpm"]) < 1.0
    assert audio_result["mode"]["value"] == "music"


def test_kicks_frame_accurate(audio_result, synthetic):
    _, truth = synthetic
    fps = truth["fps"]
    p, r, errs = match(_frames(audio_result, "KICK"), [round(t * fps) for t in truth["audio"]["kicks"]], 1)
    assert r >= 0.95, f"recall kicks {r}"
    assert p >= 0.9, f"precisão kicks {p}"
    exact = sum(1 for e in errs if e == 0) / len(errs)
    assert exact >= 0.9, f"só {exact:.0%} dos kicks no frame exato"


def test_snares(audio_result, synthetic):
    _, truth = synthetic
    p, r, _ = match(_times(audio_result, "SNARE"), truth["audio"]["snares"], 0.035)
    assert r == 1.0 and p >= 0.6, (p, r)


def test_drop_riser_silence_impact(audio_result, synthetic):
    _, truth = synthetic
    fps = truth["fps"]
    drops = _frames(audio_result, "DROP")
    assert any(abs(d - truth["audio"]["drop"] * fps) <= 1 for d in drops), drops
    r = audio_result["risers"]
    assert r and abs(r[0]["start"] - truth["audio"]["riser"][0]) < 0.3 and abs(r[0]["end"] - truth["audio"]["riser"][1]) < 0.1
    s0, s1 = truth["audio"]["silence"]
    assert any(abs(s["start"] - s0) < 0.15 and abs(s["end"] - s1) < 0.1 for s in audio_result["silences"])
    # nenhuma "falsa pausa" entre batidas no trecho rítmico
    assert not [s for s in audio_result["silences"] if 3.0 < s["start"] < 12.5]
    assert any(abs(t - truth["audio"]["impact_final"]) < 0.034 for t in _times(audio_result, "IMPACT"))


def test_downbeat_on_drop(audio_result, synthetic):
    _, truth = synthetic
    db = [b["time"] for b in audio_result["beats"] if b["is_downbeat"]]
    assert any(abs(t - truth["audio"]["drop"]) < 0.034 for t in db)
