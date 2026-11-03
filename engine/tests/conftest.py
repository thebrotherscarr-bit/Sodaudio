import json
import os
import shutil
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile

HERE = Path(__file__).resolve().parent
AD = HERE.parents[1]
EXAMPLE = AD / "examples" / "rimtera" / "audio-project.json"
RIMTERA = Path(os.environ.get("RIMTERA_ROOT") or AD.parent / "Rimtera")  # the Rimtera checkout beside this one
RIMTERA_TSV = RIMTERA / "tools" / "audio" / "audio_needs.tsv"


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Settings, logs and the default job queue go to a temporary folder, never %APPDATA%."""
    home = tmp_path / "home"
    monkeypatch.setenv("SODAUDIO_HOME", str(home))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return home


@pytest.fixture
def game(tmp_path):
    """A sandbox game folder with Rimtera's cue list (copied, read-only use of the real repo) and empty output folders."""
    root = tmp_path / "game"
    (root / "tools" / "audio").mkdir(parents=True)
    if RIMTERA_TSV.exists():
        shutil.copy(RIMTERA_TSV, root / "tools" / "audio" / "audio_needs.tsv")
    for folder in ("Assets/Sounds", "Assets/Music", "Assets/Ambience"):
        (root / folder).mkdir(parents=True)
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    data["root"] = "."
    (root / "audio-project.json").write_text(json.dumps(data), encoding="utf-8")
    return root


@pytest.fixture
def project(game):
    from engine.importers import merge, run_importer
    from engine.project import Project
    proj = Project.open(game / "audio-project.json")
    merge(proj, run_importer(proj, run_command=False, log=lambda *_: None))
    return proj


@pytest.fixture
def services(game):
    from engine import pipeline
    from engine.services import Services
    from engine.settings import Settings
    settings = Settings()
    settings.data.update(generator="fake", fake_delay=0.01, comfy_url="http://127.0.0.1:1")
    svc = Services(settings, pipeline.handlers())
    svc.open_project(game / "audio-project.json")
    from engine.importers import merge, run_importer
    merge(svc.project, run_importer(svc.project, run_command=False, log=lambda *_: None))
    yield svc
    svc.shutdown()
    if svc.queue:
        svc.queue.stop(wait=True, timeout=5)
        svc.queue.store.close()


def wait_for(predicate, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    raise AssertionError("timed out")


def wait_idle(svc, timeout=120):
    wait_for(lambda: svc.queue.idle(), timeout)


def click_track(path, bpm=120.0, seconds=30.0, rate=44100, quiet=None):
    """A drum-like click on every beat, louder on the downbeat, over a soft pad: a known tempo for the analysis tests."""
    t = np.arange(int(seconds * rate)) / rate
    beat = 60.0 / bpm
    phase = (t % beat) / beat
    accent = np.where((t // beat) % 4 == 0, 1.0, 0.5)
    click = accent * np.exp(-phase * 50) * np.sin(2 * np.pi * 900 * t) * 0.6
    pad = np.sin(2 * np.pi * 220 * t) * 0.1
    mono = click + pad
    if quiet:
        a, b = (int(x * rate) for x in quiet)
        mono[a:b] *= 0.001
    soundfile.write(str(path), np.stack([mono, mono], axis=1).astype(np.float32), rate)
    return path
