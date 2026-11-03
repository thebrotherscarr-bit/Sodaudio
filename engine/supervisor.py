"""Finds or starts ComfyUI, watches it, captures its output and restarts it every N tracks (the long-run slowdown
workaround). Three ways to have ComfyUI:
- attach: a ComfyUI already answering at the address (Comfy Desktop, or one a game's own tools started) is used as it
  is, and never started, stopped or restarted by the engine;
- headless, started by the engine: ComfyUI's main.py with Rimtera's flags, its output in the Server log;
- headless, started by a script (settings.comfy_start_script), such as Rimtera's tools/comfy/start_comfy.ps1, called
  with -ReserveVramGB and -Priority for the GPU mode, the way Rimtera's overnight runs started it."""
import os
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx


def pid_on_port(port: int):
    """The process listening on a local port (Windows netstat), or None."""
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
            return int(parts[4])
    return None


class Supervisor:
    def __init__(self, services):
        self.services = services
        self.state = "down"
        self.process = None
        self.script_pid = None  # a ComfyUI our start script launched: ours to restart and stop
        self.tracks = 0
        self.stats = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    @property
    def url(self):
        return self.services.settings["comfy_url"].rstrip("/")

    @property
    def managed(self) -> bool:
        return self.process is not None and self.process.poll() is None

    # --- watching ------------------------------------------------------------------------------------------------
    def probe(self) -> bool:
        try:
            response = httpx.get(self.url + "/system_stats", timeout=2)
            self.stats = response.json() if response.status_code == 200 else None
            return response.status_code == 200
        except (httpx.HTTPError, ValueError):
            self.stats = None
            return False

    def gpu(self):
        devices = (self.stats or {}).get("devices") or []
        if not devices:
            return None
        device = devices[0]
        return {"name": device.get("name", ""), "vram_free_gb": round((device.get("vram_free") or 0) / 2**30, 2),
                "vram_total_gb": round((device.get("vram_total") or 0) / 2**30, 2)}

    def watch(self, interval=5.0):
        def loop():
            while not self._stop.is_set():
                if self.state != "starting":
                    new = "up" if self.probe() else "down"
                    if new != self.state:
                        self.state = new
                        self.services.health_changed()
                self._stop.wait(interval)
        threading.Thread(target=loop, name="comfy-watch", daemon=True).start()

    # --- starting and stopping -----------------------------------------------------------------------------------
    def command(self):
        settings = self.services.settings
        base = Path(settings["comfy_path"] or "")
        main = next((p for p in (base / "ComfyUI" / "main.py", base / "main.py") if p.exists()), None)
        if main is None:
            raise FileNotFoundError("ComfyUI is not set up: point the Setup screen at an install (settings.comfy_path)")
        python = settings["comfy_python"] or next(
            (str(p) for p in (base / "ComfyUI" / ".venv" / "Scripts" / "python.exe", base / ".venv" / "Scripts" / "python.exe",
                              base / "python_embeded" / "python.exe") if p.exists()), "")
        if not python:
            raise FileNotFoundError("ComfyUI's Python was not found: set it on the Setup screen (settings.comfy_python)")
        port = urllib.parse.urlparse(self.url).port or 8188
        reserve = "1.5" if settings["gpu_mode"] != "restricted" else "4"
        args = [python, "-s", str(main), "--listen", "127.0.0.1", "--port", str(port), "--disable-dynamic-vram",
                "--disable-fast-disk", "--reserve-vram", reserve]
        if settings["comfy_extra_model_paths"]:
            args += ["--extra-model-paths-config", settings["comfy_extra_model_paths"]]
        if settings["comfy_extra_args"]:
            import shlex
            args += shlex.split(settings["comfy_extra_args"], posix=False)
        return args, str(main.parent.parent if main.parent.name == "ComfyUI" else main.parent)

    def port(self):
        return urllib.parse.urlparse(self.url).port or 8188

    def start_with_script(self, log, wait):
        """Rimtera's way: the script launches ComfyUI hidden and returns; we wait for it to answer."""
        settings = self.services.settings
        script = settings["comfy_start_script"]
        focused = settings["gpu_mode"] != "restricted"
        args = [script]
        if script.lower().endswith(".ps1"):
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script,
                    "-ReserveVramGB", "1.5" if focused else "4", "-Priority", "Normal" if focused else "BelowNormal"]
        log(f"starting ComfyUI with {' '.join(args)}")
        self.state = "starting"
        self.services.health_changed()
        result = subprocess.run(args, capture_output=True, timeout=120, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in (result.stdout + result.stderr).decode("utf-8", "replace").splitlines():
            if line.strip():
                self.services.log(line, source="comfy")
        deadline = time.time() + wait
        while time.time() < deadline:
            if self.probe():
                self.state = "up"
                self.script_pid = pid_on_port(self.port())
                self.tracks = 0
                self.services.health_changed()
                return
            time.sleep(2)
        self.state = "down"
        raise RuntimeError("ComfyUI did not answer after its start script; see the script's own log")

    def start(self, log=None, wait=360):
        with self._lock:
            if self.probe():
                self.state = "up"
                return
            if self.services.settings["comfy_start_script"]:
                return self.start_with_script(log or self.services.log, wait)
            args, cwd = self.command()
            env = os.environ.copy()
            env.update(TORCH_BLAS_PREFER_HIPBLASLT="0", MIOPEN_LOG_LEVEL="3", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
            flags = 0
            if sys.platform == "win32":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                if self.services.settings["gpu_mode"] == "restricted":
                    flags |= 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
            (log or self.services.log)(f"starting ComfyUI: {' '.join(args)}")
            self.state = "starting"
            self.services.health_changed()
            self.process = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
            threading.Thread(target=self._pump, args=(self.process,), name="comfy-log", daemon=True).start()
            self.tracks = 0
            deadline = time.time() + wait
            while time.time() < deadline:
                if self.process.poll() is not None:
                    self.state = "down"
                    self.services.health_changed()
                    raise RuntimeError(f"ComfyUI exited while starting (code {self.process.returncode}); see the Server log")
                if self.probe():
                    self.state = "up"
                    self.services.health_changed()
                    return
                time.sleep(2)
            self.state = "down"
            raise RuntimeError("ComfyUI did not answer in time; see the Server log")

    def _pump(self, process):
        """ComfyUI's output goes to the day's log and the event stream. stderr lines are log lines, not errors."""
        for raw in process.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.services.log(line, source="comfy")

    def stop(self):
        if self.script_pid:
            pid, self.script_pid = self.script_pid, None
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True)
            self.state = "down"
            self.services.health_changed()
        process, self.process = self.process, None
        if process is None or process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True)
        else:
            process.kill()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            pass
        self.state = "down"
        self.services.health_changed()

    def restart(self, log=None):
        if not self.process and not self.script_pid:
            return False  # not ours to restart
        (log or self.services.log)("restarting ComfyUI")
        self.stop()
        self.start(log)
        return True

    def ensure_up(self, log=None):
        if self.probe():
            self.state = "up"
            return
        self.start(log)

    def track_done(self, log=None):
        self.tracks += 1
        every = int(self.services.settings["restart_every"] or 0)
        if (self.process or self.script_pid) and every and self.tracks >= every:
            self.restart(log)

    def free(self, log=None):
        """Asks ComfyUI to unload its models before MOSS takes the GPU."""
        if self.probe():
            (log or self.services.log)("asking ComfyUI to unload its models")
            httpx.post(self.url + "/free", json={"unload_models": True, "free_memory": True}, timeout=30)

    def busy_elsewhere(self, client_id=None) -> int:
        """How many prompts ComfyUI is running or holding for other clients (an art run, the user's own graphs)."""
        try:
            queue = httpx.get(self.url + "/queue", timeout=5).json()
        except (httpx.HTTPError, ValueError):
            return 0
        items = (queue.get("queue_running") or []) + (queue.get("queue_pending") or [])
        # Each item is [number, prompt_id, prompt, extra_data, outputs]; extra_data holds the client_id.
        return sum(1 for item in items if not (len(item) > 3 and isinstance(item[3], dict) and item[3].get("client_id") == client_id))

    def wait_for_gpu(self, client_id, log, cancelled=lambda: False):
        """One focus on the GPU: while ComfyUI works for someone else, wait instead of queuing behind or beside it."""
        told = False
        while self.busy_elsewhere(client_id):
            if not told:
                log("ComfyUI is busy with other work (an art run?); waiting for it to finish before generating audio")
                told = True
            if cancelled():
                raise InterruptedError("cancelled")
            time.sleep(10)
        if told:
            log("ComfyUI is free; continuing")

    def shutdown(self):
        self._stop.set()
        self.stop()
