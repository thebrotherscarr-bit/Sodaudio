"""BUILD-CORE phase 5: rebuilding the Rimtera title loop from mus_title__c8 matches the installed loop. Reads the
Rimtera checkout; writes only to the test's sandbox."""
import numpy as np
import pytest
import soundfile

from .conftest import RIMTERA, wait_idle

TAKE = RIMTERA / "tools" / "audio" / "raw" / "music" / "mus_title__c8.flac"
INSTALLED = RIMTERA / "Assets" / "Music" / "mus_title.ogg"
PIECES = [{"from": 139.04, "to": 142.73, "gain_db": 8.5}, {"from": 142.73, "to": 146.43, "gain_db": 14}, {"from": 61.51, "to": 91.05}]


@pytest.mark.skipif(not (TAKE.exists() and INSTALLED.exists()), reason="needs the Rimtera title take and installed loop")
def test_title_loop_rebuilds(services):
    job = services.queue.submit("recompose", {"take": str(TAKE), "pieces": PIECES, "fade": 0.23})
    wait_idle(services, 300)
    result = services.queue.store.get(job)
    assert result["state"] == "done", result["error"]
    made, rate = soundfile.read(str(services.project.candidate_dir("music") / result["result"]["candidate"]), always_2d=True)
    installed, installed_rate = soundfile.read(str(INSTALLED), always_2d=True)
    assert rate == installed_rate
    assert abs(len(made) - len(installed)) / rate < 0.01
    n = min(len(made), len(installed))
    x, y = made[:n].mean(axis=1), installed[:n].mean(axis=1)
    assert float(x @ y / np.sqrt((x @ x) * (y @ y))) > 0.99  # the rest is Vorbis encoding
    assert abs(10 * np.log10((x ** 2).mean() / (y ** 2).mean())) < 0.5
