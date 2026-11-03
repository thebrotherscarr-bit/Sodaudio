"""First-run setup (SETUP.md): checks what is installed, and a setup job that finds ComfyUI, downloads the models,
builds the MOSS venv and runs the smoke test. The models are many gigabytes; nothing here runs unless asked."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .generators.acestep import MODEL_FILES
from .settings import home

ACE_REPO = "Comfy-Org/ace_step_1.5_ComfyUI_files"
MOSS_REPO = "OpenMOSS-Team/MOSS-SoundEffect-v2.0"
MOSS_CODE = "https://github.com/OpenMOSS/MOSS-TTS.git"
MOSS_PINS = ["transformers==4.57.1", "diffusers==0.37.1", "ftfy==6.3.1", "soundfile==0.13.1", "descript-audiotools==0.7.2", "einops==0.8.2"]


# --- finding things --------------------------------------------------------------------------------------------------
def comfy_candidates():
    env = os.environ
    roots = [Path(env.get("APPDATA", "")) / "Comfy Desktop", Path(env.get("LOCALAPPDATA", "")) / "Comfy-Desktop",
             Path.home() / "Documents" / "ComfyUI", Path.home() / "ComfyUI", Path("C:/ComfyUI_windows_portable")]
    found = []
    for root in roots:
        if not root.exists():
            continue
        for main in [root / "ComfyUI" / "main.py", root / "main.py", *root.glob("*/ComfyUI/main.py")]:
            if main.exists():
                found.append(main.parent.parent if main.parent.name == "ComfyUI" else main.parent)
    return found


def adopt_game_tools(root) -> dict:
    """The pieces a game's own audio tools already run (SETUP.md, "Adopting an existing Rimtera install"): its ComfyUI
    (tools/comfy-app, a Comfy Desktop instance) with Comfy Desktop's model paths and shared input/output folders, its
    start script (tools/comfy/start_comfy.ps1, as the overnight runs used), its MOSS venv, code and model. Returns the
    settings found; nothing is installed or changed in the game."""
    root = Path(root)
    found = {}
    comfy = root / "tools" / "comfy-app"
    if (comfy / "ComfyUI" / "main.py").exists():
        found["comfy_path"] = str(comfy)
        python = comfy / "ComfyUI" / ".venv" / "Scripts" / "python.exe"
        if python.exists():
            found["comfy_python"] = str(python)
    script = root / "tools" / "comfy" / "start_comfy.ps1"
    if script.exists():
        found["comfy_start_script"] = str(script)
    desktop = Path(os.environ.get("APPDATA", "")) / "Comfy Desktop" / "instance-model-paths"
    yamls = sorted(desktop.glob("*.yaml"), key=lambda p: p.stat().st_mtime) if desktop.exists() else []
    if yamls:
        found["comfy_extra_model_paths"] = str(yamls[-1])
    shared = Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Shared"
    if (shared / "input").exists() and (shared / "output").exists():
        found["comfy_extra_args"] = f'--input-directory "{shared / "input"}" --output-directory "{shared / "output"}"'
    moss = root / "tools" / "moss-venv" / "Scripts" / "python.exe"
    if moss.exists():
        found["moss_python"] = str(moss)
    if (root / "tools" / "moss" / "moss_soundeffect_v2").exists():
        found["moss_code_dir"] = str(root / "tools" / "moss")
    model = shared / "models" / "diffusers" / "MOSS-SoundEffect-v2.0"
    if model.exists():
        found["moss_model_dir"] = str(model)
    return found


def extra_model_dirs(yaml_path: str) -> list:
    """The folders an extra_model_paths yaml points at (a small reader: base_path plus per-type folders)."""
    dirs = []
    if not yaml_path or not Path(yaml_path).exists():
        return dirs
    base = None
    for line in Path(yaml_path).read_text(encoding="utf-8", errors="replace").splitlines():
        found = re.match(r"\s*base_path:\s*(.+)", line)
        if found:
            base = Path(found.group(1).strip().strip("'\""))
            dirs.append(base / "models")
            dirs.append(base)
            continue
        found = re.match(r"\s*(diffusion_models|text_encoders|vae|diffusers|checkpoints|unet|clip):\s*(.+)", line)
        if found and base is not None:
            for part in found.group(2).split("|"):
                part = part.strip().strip("'\"")
                if part:
                    dirs.append((base / part).parent)
    return [d for d in dict.fromkeys(dirs)]


def model_dirs(settings) -> list:
    dirs = [Path(d) for d in settings["comfy_models_dirs"] or []]
    if settings["comfy_path"]:
        base = Path(settings["comfy_path"])
        dirs += [base / "ComfyUI" / "models", base / "models"]
    dirs += extra_model_dirs(settings["comfy_extra_model_paths"])
    dirs.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Comfy-Desktop" / "ComfyUI-Shared" / "models")
    return [d for d in dict.fromkeys(dirs) if d.exists()]


def find_model(settings, sub, name):
    for base in model_dirs(settings):
        for folder in (base / sub, base / ("unet" if sub == "diffusion_models" else sub), base / ("clip" if sub == "text_encoders" else sub)):
            if (folder / name).exists():
                return folder / name
    return None


def moss_model_dir(settings) -> Path:
    if settings["moss_model_dir"]:
        return Path(settings["moss_model_dir"])
    dirs = model_dirs(settings)
    return (dirs[0] if dirs else home() / "models") / "diffusers" / "MOSS-SoundEffect-v2.0"


def check(services) -> dict:
    settings = services.settings
    parts = []
    parts.append({"part": "engine", "ok": sys.version_info >= (3, 11), "detail": f"Python {sys.version.split()[0]} at {sys.executable}"})
    up = services.supervisor.probe()
    comfy_path = settings["comfy_path"]
    found = comfy_candidates()
    has_main = bool(comfy_path) and any((Path(comfy_path) / p).exists() for p in ("ComfyUI/main.py", "main.py"))
    parts.append({"part": "comfyui", "ok": up or has_main,
                  "detail": ("answering at " + settings["comfy_url"] if up else "") + (f"; install at {comfy_path}" if has_main else ""),
                  "found": [str(f) for f in found],
                  "fix": None if up or has_main else "point at an existing ComfyUI (Comfy Desktop or portable) or install one"})
    for sub, names in MODEL_FILES.items():
        for name in names:
            path = find_model(settings, sub, name)
            parts.append({"part": f"model {name}", "ok": path is not None, "detail": str(path) if path else f"not in any {sub} folder",
                          "fix": None if path else f"download from {ACE_REPO}"})
    moss_python = settings["moss_python"]
    parts.append({"part": "moss venv", "ok": bool(moss_python) and Path(moss_python).exists(), "detail": moss_python or "not set",
                  "fix": None if moss_python and Path(moss_python).exists() else "build it (needs ComfyUI's Python)"})
    model = moss_model_dir(settings)
    parts.append({"part": "moss model", "ok": (model / "model_index.json").exists() or any(model.glob("*.json")) if model.exists() else False,
                  "detail": str(model), "fix": f"download {MOSS_REPO}"})
    parts.append({"part": "claude api key", "ok": bool(settings.api_key()), "detail": "set" if settings.api_key() else "not set (the agent panel is hidden)",
                  "optional": True})
    return {"parts": parts, "ready": all(p["ok"] for p in parts if not p.get("optional"))}


# --- the setup job ---------------------------------------------------------------------------------------------------
def hf_files(repo):
    response = httpx.get(f"https://huggingface.co/api/models/{repo}/tree/main?recursive=true", timeout=60, follow_redirects=True)
    response.raise_for_status()
    return [item["path"] for item in response.json() if item.get("type") == "file"]


def download(ctx, url, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    partial = target.with_name(target.name + ".part")
    have = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with httpx.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as response:
        if response.status_code not in (200, 206):
            raise RuntimeError(f"download failed ({response.status_code}): {url}")
        total = int(response.headers.get("content-length", 0)) + (have if response.status_code == 206 else 0)
        mode = "ab" if response.status_code == 206 else "wb"
        done, last = (have if mode == "ab" else 0), 0.0
        with partial.open(mode) as handle:
            for chunk in response.iter_bytes(1 << 20):
                ctx.check()
                handle.write(chunk)
                done += len(chunk)
                if time.time() - last > 1:
                    last = time.time()
                    ctx.progress(done / total if total else 0, message=f"{target.name}: {done / 2**30:.2f} of {total / 2**30:.2f} GB")
    partial.replace(target)
    ctx.log(f"downloaded {target}")


def game_roots(services):
    roots = []
    if services.project:
        roots.append(services.project.root)
    for path in services.settings["recent_projects"]:
        try:
            from .project import Project
            roots.append(Project.open(path).root)
        except Exception:
            continue
    return list(dict.fromkeys(roots))


def step_find(ctx):
    settings = ctx.services.settings
    if ctx.services.supervisor.probe():
        ctx.log(f"ComfyUI answers at {settings['comfy_url']}; the engine will attach to it as it is and never stop it")
    for root in game_roots(ctx.services):
        found = adopt_game_tools(root)
        unset = {k: v for k, v in found.items() if not settings[k]}
        if unset:
            settings.update(unset)
            for key, value in unset.items():
                ctx.log(f"adopted from {root}: {key} = {value}")
    if not settings["comfy_path"]:
        found = comfy_candidates()
        if found:
            ctx.services.settings.update({"comfy_path": str(found[0])})
            ctx.log(f"found ComfyUI at {found[0]}")
        else:
            ctx.log("no ComfyUI install found: install Comfy Desktop (comfy.org) or the portable build for your GPU, "
                    "then use \"Point at existing\" on the Setup screen")


def step_models(ctx):
    settings = ctx.services.settings
    dirs = model_dirs(settings)
    if not dirs:
        raise RuntimeError("no ComfyUI models folder found: point at ComfyUI first")
    missing = [(sub, name) for sub, names in MODEL_FILES.items() for name in names if not find_model(settings, sub, name)]
    if missing:
        files = hf_files(ACE_REPO)
        for sub, name in missing:
            path = next((f for f in files if f.endswith("/" + name) or f == name), None)
            if path is None:
                raise RuntimeError(f"{name} is not in {ACE_REPO}")
            download(ctx, f"https://huggingface.co/{ACE_REPO}/resolve/main/{path}", dirs[0] / sub / name)
    target = moss_model_dir(settings)
    if not (target / "model_index.json").exists():
        for path in hf_files(MOSS_REPO):
            download(ctx, f"https://huggingface.co/{MOSS_REPO}/resolve/main/{path}", target / path)
    settings.update({"moss_model_dir": str(target)})


def _run(ctx, args, **kwargs):
    ctx.log("> " + " ".join(str(a) for a in args))
    process = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    ctx.on_cancel(process.kill)
    for raw in process.stdout:
        ctx.log(raw.decode("utf-8", "replace").rstrip())
    if process.wait() != 0:
        raise RuntimeError(f"{Path(str(args[0])).name} failed with code {process.returncode}")


def step_moss(ctx):
    """The Rimtera recipe (setup_audio.ps1): a venv made with ComfyUI's Python that shares ComfyUI's GPU torch through
    a .pth file, with the shared packages pinned so pip never replaces that torch."""
    settings = ctx.services.settings
    comfy_python = settings["comfy_python"]
    if not comfy_python:
        args, _ = ctx.services.supervisor.command()
        comfy_python = args[0]
    venv = home() / "moss-venv"
    python = venv / "Scripts" / "python.exe"
    if not python.exists():
        _run(ctx, [comfy_python, "-m", "venv", venv])
    site = subprocess.run([comfy_python, "-c", "import site; print(site.getsitepackages()[-1])"], capture_output=True, text=True).stdout.strip()
    (venv / "Lib" / "site-packages" / "zz_comfy_shared.pth").write_text(site + "\n", encoding="ascii")
    freeze = subprocess.run([comfy_python, "-m", "pip", "freeze"], capture_output=True, text=True).stdout
    pins = [line for line in freeze.splitlines() if re.match(r"^(torch|torchaudio|torchvision|numpy|scipy)==", line)]
    constraints = home() / "moss-constraints.txt"
    constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
    _run(ctx, [python, "-m", "pip", "install", "-c", constraints, *MOSS_PINS])
    code = Path(settings["moss_code_dir"] or home() / "MOSS-TTS")
    if not code.exists():
        _run(ctx, ["git", "clone", "--depth", "1", MOSS_CODE, code])
    _run(ctx, [python, "-m", "pip", "install", "--no-deps", "-e", code / "moss_soundeffect_v2"])
    _run(ctx, [python, "-c", "import torch; from moss_soundeffect_v2 import MossSoundEffectPipeline; "
                             "print('MOSS ready on', torch.cuda.get_device_name(0))"])
    settings.update({"moss_python": str(python), "moss_code_dir": str(code)})


def step_smoke(ctx):
    """A 10-second music take and a 2-second effect, post-processed, in the app's smoke folder."""
    from . import post
    from .generators import moss
    from .generators.acestep import AceStep, music_workflow
    services = ctx.services
    folder = home() / "smoke"
    folder.mkdir(parents=True, exist_ok=True)
    for path in folder.glob("*"):
        path.unlink()
    services.supervisor.ensure_up(ctx.log)
    flac = AceStep(services.settings["comfy_url"]).run(music_workflow("upbeat test tune, synth lead, drums", seconds=10, bpm=120, seed=1),
                                                       lambda s, o, n=None: ctx.progress(s / o * 0.5, message=f"music step {s}/{o}"),
                                                       lambda: ctx.cancelled)
    (folder / "smoke_music.flac").write_bytes(flac)
    post.process(folder / "smoke_music.flac", folder / "smoke_music_post.wav", "music", False)
    services.supervisor.free(ctx.log)
    moss.run_batch(services.settings, [{"prompt": "a short laser pistol shot, clean game sound effect", "seconds": 2, "seed": 1,
                                        "out": str(folder / "smoke_effect.wav")}], ctx.log, lambda o, t: None,
                   lambda o, e: ctx.log(f"FAILED {o}: {e}"), ctx.on_cancel)
    if not (folder / "smoke_effect.wav").exists():
        raise RuntimeError("the effect was not made; see the log")
    post.process(folder / "smoke_effect.wav", folder / "smoke_effect_post.wav", "sfx", False)
    ctx.log(f"smoke test passed: {folder}")
    return {"music": str(folder / "smoke_music_post.wav"), "effect": str(folder / "smoke_effect_post.wav")}


STEPS = {"find": step_find, "models": step_models, "moss": step_moss, "smoke": step_smoke}


def run_setup(ctx, body):
    steps = body.get("steps") or ["find", "models", "moss", "smoke"]
    results = {}
    for index, name in enumerate(steps):
        ctx.check()
        ctx.log(f"--- setup: {name}")
        ctx.progress(index / len(steps), message=name, force=True)
        results[name] = STEPS[name](ctx) or "ok"
    return results
