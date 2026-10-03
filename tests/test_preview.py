"""Preview renderizado + verificação frame a frame (FASE 40) + sistema de qualidade."""
import pytest


@pytest.fixture(scope="session")
def rendered(timeline, synthetic, tmp_path_factory):
    from conde.render.preview import render
    from conde.render.verify import verify_preview
    tl, an = timeline
    path, _ = synthetic
    out = tmp_path_factory.mktemp("prev")
    prev = render(tl, path, out / "p.mp4", width=480, log=lambda *_: None)
    return tl, an, prev, verify_preview(tl, prev, an, out)


def test_preview_length_and_no_black_frames(rendered):
    tl, _, prev, ver = rendered
    assert prev["frames"] == tl["comp"]["duration_frames"]
    assert ver["unexpected_black_frames"] == []


def test_every_checked_event_aligned_on_the_frame(rendered):
    _, _, _, ver = rendered
    assert ver["events_checked"] >= 10
    assert ver["failed"] == [], ver["failed"]


def test_quality_has_no_technical_issues(rendered):
    from conde.validate.checks import quality
    tl, an, _, _ = rendered
    q = quality(tl, an)
    assert [i for i in q["issues"] if i["severity"] == "técnico"] == []
    assert q["audio_only_test"]["coverage"] >= 0.85
    assert q["visual_density"]["corr_with_energy"] > 0.3
    assert q["pause_test"]["longest_stacked_fx_s"] <= 3.0
