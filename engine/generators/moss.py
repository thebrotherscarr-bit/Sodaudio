"""Runs moss_worker.py with the MOSS venv's Python and streams its JSON lines back as progress."""
import json
import os
import subprocess
import sys
from pathlib import Path

WORKER = Path(__file__).with_name("moss_worker.py")


def gpu_env(settings) -> dict:
    env = os.environ.copy()
    env.update(MIOPEN_LOG_LEVEL="3", TORCH_BLAS_PREFER_HIPBLASLT="0", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    focused = settings["gpu_mode"] != "restricted"
    env.update(AD_PRIORITY="normal" if focused else "below", AD_VRAM_FREE_GB="1.5" if focused else "4",
               AD_CPU_FRACTION="0.8" if focused else "0.6", AD_REST_SECONDS="0" if focused else "2",
               AD_MOSS_STEPS=str(settings["moss_steps"]), AD_MOSS_MODEL_DIR=str(settings["moss_model_dir"]))
    return env


def run_batch(settings, jobs, on_line, on_done, on_failed, register_stop):
    """jobs: [{prompt, seconds, seed, out}]. Blocks until the worker exits; returns its exit code."""
    python = settings["moss_python"]
    if not python or not Path(python).exists():
        raise FileNotFoundError("the MOSS venv's python is not set up (Setup screen, or settings.moss_python)")
    flags = 0x00004000 if sys.platform == "win32" and settings["gpu_mode"] == "restricted" else 0  # BELOW_NORMAL
    process = subprocess.Popen([python, str(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               env=gpu_env(settings), creationflags=flags)
    register_stop(process.kill)
    process.stdin.write(("\n".join(json.dumps(job) for job in jobs) + "\n").encode("utf-8"))
    process.stdin.close()
    for raw in process.stdout:
        line = raw.decode("utf-8", "replace").rstrip()
        if not line:
            continue
        message = None
        if line.startswith("{"):
            try:
                message = json.loads(line)
            except ValueError:
                message = None
        if message and "done" in message:
            on_done(message["done"], message.get("seconds", 0))
        elif message and "failed" in message:
            on_failed(message["failed"], message.get("error", ""))
        else:
            on_line(line)
    return process.wait()
