"""The real ComfyUI path against a stand-in ComfyUI (tests/fake_comfy): the supervisor starts it with ComfyUI's
arguments, the ACE-Step client submits the graph and fetches the take, the supervisor restarts it every N tracks, and
stopping leaves no process behind. No GPU and no models are touched."""
import socket
import sys
from pathlib import Path

import pytest
import soundfile

from .conftest import wait_idle

FAKE = Path(__file__).parent / "fake_comfy"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def comfy_services(services):
    services.settings.data.update(generator="comfy", comfy_url=f"http://127.0.0.1:{free_port()}", comfy_path=str(FAKE),
                                  comfy_python=sys.executable, restart_every=2, comfy_extra_model_paths="", comfy_extra_args="")
    yield services
    services.supervisor.stop()


def test_generate_through_comfyui(comfy_services):
    svc = comfy_services
    svc.project.edit_cue("mus_frozen_day", {"seconds": 8})
    job = svc.queue.submit("generate", {"cues": ["mus_frozen_day"], "takes": 3})
    wait_idle(svc, 120)
    result = svc.queue.store.get(job)
    assert result["state"] == "done", result["error"]
    assert result["result"]["made"] == ["mus_frozen_day__c1.flac", "mus_frozen_day__c2.flac", "mus_frozen_day__c3.flac"]
    assert soundfile.info(str(svc.project.raw_dir("music") / "mus_frozen_day__c1.flac")).format == "FLAC"
    log = (svc.queue.log_dir / f"{job}.log").read_text(encoding="utf-8")
    assert "starting ComfyUI" in log and "--disable-dynamic-vram" in log and "--reserve-vram 1.5" in log
    assert "restarting ComfyUI" in log  # restart_every = 2
    assert svc.supervisor.gpu()["name"] == "Fake GPU"
    process = svc.supervisor.process
    assert process is not None and process.poll() is None
    svc.supervisor.stop()
    assert process.poll() is not None


def test_an_external_comfyui_is_never_restarted(comfy_services):
    """A ComfyUI already answering when the engine looked is used as it is: not started, stopped or restarted."""
    import subprocess
    svc = comfy_services
    port = svc.settings["comfy_url"].rsplit(":", 1)[1]
    external = subprocess.Popen([sys.executable, str(FAKE / "ComfyUI" / "main.py"), "--port", port])
    try:
        from .conftest import wait_for
        wait_for(svc.supervisor.probe, 30)
        svc.project.edit_cue("mus_frozen_day", {"seconds": 4})
        job = svc.queue.submit("generate", {"cues": ["mus_frozen_day"], "takes": 3})
        wait_idle(svc, 120)
        assert svc.queue.store.get(job)["state"] == "done"
        assert svc.supervisor.process is None and external.poll() is None
    finally:
        external.kill()


def test_effects_through_the_moss_worker_protocol(comfy_services, monkeypatch):
    """The engine's side of the MOSS worker: ComfyUI unloads first, jobs go in on stdin, JSON lines come back."""
    from engine.generators import moss
    svc = comfy_services
    monkeypatch.setattr(moss, "WORKER", Path(__file__).parent / "fake_moss_worker.py")
    svc.settings.data.update(moss_python=sys.executable)
    svc.project.edit_cue("use_spear", {"prompt": "please fail"})
    job = svc.queue.submit("generate", {"cues": ["use_broadsword", "use_spear"], "takes": 2})
    wait_idle(svc, 120)
    result = svc.queue.store.get(job)
    assert result["state"] == "done", result["error"]
    assert result["result"]["made"] == ["use_broadsword__c1.wav", "use_broadsword__c2.wav"]
    assert result["result"]["failed"] == ["use_spear__c1.wav", "use_spear__c2.wav"]
    log = (svc.queue.log_dir / f"{job}.log").read_text(encoding="utf-8")
    assert "asking ComfyUI to unload" not in log or svc.supervisor.state == "up"
    assert "a warning on stderr is a log line" in log
    assert len(list(svc.project.candidate_dir("weapons").glob("use_broadsword__c*.wav"))) == 2


def test_start_script_like_the_overnight_runs(comfy_services, tmp_path, monkeypatch):
    """settings.comfy_start_script: ComfyUI started headless by the game's own script with the GPU mode's arguments,
    then restarted and stopped by the engine as its own."""
    svc = comfy_services
    record = tmp_path / "args.txt"
    monkeypatch.setenv("FAKE_COMFY_PORT", svc.settings["comfy_url"].rsplit(":", 1)[1])
    monkeypatch.setenv("FAKE_COMFY_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_COMFY_RECORD", str(record))
    svc.settings.data.update(comfy_start_script=str(FAKE / "start_comfy.ps1"), gpu_mode="restricted", rest_seconds=0, restart_every=0)
    svc.project.edit_cue("mus_town", {"seconds": 4})
    job = svc.queue.submit("generate", {"cues": ["mus_town"], "takes": 1})
    wait_idle(svc, 120)
    assert svc.queue.store.get(job)["state"] == "done", svc.queue.store.get(job)["error"]
    assert record.read_text().split() == ["4", "BelowNormal"]
    pid = svc.supervisor.script_pid
    assert pid
    svc.supervisor.stop()
    from .conftest import wait_for
    wait_for(lambda: not svc.supervisor.probe(), 20)


def test_waits_while_comfyui_works_for_someone_else(comfy_services):
    """One focus on the GPU: a prompt from another client (an art run) is let finish before audio starts."""
    import subprocess
    import httpx
    svc = comfy_services
    port = svc.settings["comfy_url"].rsplit(":", 1)[1]
    external = subprocess.Popen([sys.executable, str(FAKE / "ComfyUI" / "main.py"), "--port", port])
    try:
        from .conftest import wait_for
        wait_for(svc.supervisor.probe, 30)
        art = {"prompt": {"encode": {"inputs": {"duration": 2, "bpm": 100, "tags": "art", "lyrics": "", "seed": 1}}},
               "client_id": "art-run", "extra_data": {"seconds": 6}}
        httpx.post(svc.settings["comfy_url"] + "/prompt", json=art)
        svc.project.edit_cue("mus_town", {"seconds": 4})
        job = svc.queue.submit("generate", {"cues": ["mus_town"], "takes": 1})
        wait_idle(svc, 120)
        log = (svc.queue.log_dir / f"{job}.log").read_text(encoding="utf-8")
        assert "busy with other work" in log and "ComfyUI is free; continuing" in log
        assert svc.queue.store.get(job)["state"] == "done"
    finally:
        external.kill()


def test_adopts_a_games_own_tools(tmp_path, services, monkeypatch):
    from engine import setup
    root = tmp_path / "MyGame"
    for path in ("tools/comfy-app/ComfyUI/main.py", "tools/comfy-app/ComfyUI/.venv/Scripts/python.exe", "tools/comfy/start_comfy.ps1",
                 "tools/moss-venv/Scripts/python.exe", "tools/moss/moss_soundeffect_v2/__init__.py"):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    found = setup.adopt_game_tools(root)
    assert found["comfy_path"].endswith("comfy-app") and found["comfy_start_script"].endswith("start_comfy.ps1")
    assert found["moss_python"].endswith("python.exe") and found["moss_code_dir"].endswith("moss")
