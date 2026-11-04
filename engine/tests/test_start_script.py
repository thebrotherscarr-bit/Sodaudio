"""A start script that launches ComfyUI and returns (Rimtera's start_comfy.ps1 does) must not hold the engine until
ComfyUI exits: the ComfyUI it starts inherits the script's output handles."""
import subprocess
import sys
import threading
import time

from engine.tests.test_comfy import FAKE as FAKE_DIR, free_port

FAKE = FAKE_DIR / "ComfyUI" / "main.py"


def test_start_script_returns_while_its_comfy_keeps_running(services, tmp_path):
    port = free_port()
    script = tmp_path / "start_comfy.cmd"
    script.write_text(f'@echo off\r\necho launching\r\nstart "" /b "{sys.executable}" "{FAKE}" --port {port}\r\nexit /b 0\r\n',
                      encoding="utf-8")
    services.settings.data.update(comfy_url=f"http://127.0.0.1:{port}", comfy_start_script=str(script))
    supervisor = services.supervisor
    done = threading.Event()
    threading.Thread(target=lambda: (supervisor.start(services.log, wait=60), done.set()), daemon=True).start()
    try:
        assert done.wait(60), "start() waited for the launched ComfyUI to exit"
        assert supervisor.state == "up"
    finally:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(supervisor.script_pid)], capture_output=True)
        time.sleep(0.5)


def test_cancel_or_quit_while_comfy_loads_stops_the_comfy_the_script_started(services, tmp_path):
    port = free_port()
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])  # a ComfyUI that never answers
    script = tmp_path / "start_comfy.cmd"
    script.write_text(f"@echo off\r\necho ComfyUI starting (pid {sleeper.pid}, priority Normal)\r\nexit /b 0\r\n", encoding="utf-8")
    services.settings.data.update(comfy_url=f"http://127.0.0.1:{port}", comfy_start_script=str(script))
    supervisor = services.supervisor
    errors = []
    thread = threading.Thread(target=lambda: _start(supervisor, services, errors), daemon=True)
    thread.start()
    try:
        time.sleep(3)
        assert supervisor.script_pid == sleeper.pid and supervisor.ours
        supervisor.abort_start()
        thread.join(10)
        assert not thread.is_alive() and errors and isinstance(errors[0], InterruptedError)
        supervisor.shutdown()  # what quitting does: the ComfyUI it started is ended
        sleeper.wait(10)
    finally:
        sleeper.kill()


def _start(supervisor, services, errors):
    try:
        supervisor.start(services.log, wait=60)
    except Exception as error:
        errors.append(error)
