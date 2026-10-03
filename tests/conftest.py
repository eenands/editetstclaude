import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures" / "synthetic.mp4"


@pytest.fixture(scope="session")
def synthetic():
    if not FIX.exists():
        from tests.synth import main
        main(str(FIX))
    truth = json.loads(FIX.with_suffix(".truth.json").read_text())
    return FIX, truth


@pytest.fixture(scope="session")
def audio_result(synthetic):
    from conde.analysis.audio import analyze_audio
    path, truth = synthetic
    return analyze_audio(path, truth["fps"], truth["frames"])


def match(found, truth, tol):
    """Pareia eventos detectados com o gabarito (tolerância em segundos ou frames)."""
    used, hits, errs = set(), 0, []
    for t in truth:
        best = None
        for i, f in enumerate(found):
            if i not in used and abs(f - t) <= tol and (best is None or abs(f - t) < abs(found[best] - t)):
                best = i
        if best is not None:
            used.add(best)
            hits += 1
            errs.append(found[best] - t)
    recall = hits / len(truth) if truth else 1.0
    precision = hits / len(found) if found else 1.0
    return precision, recall, errs
