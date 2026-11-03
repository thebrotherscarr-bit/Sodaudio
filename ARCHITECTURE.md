# Architecture

## The parts

```
┌──────────────── Shell ────────────────┐
│  PWA: browser tab (Edge/Chrome)       │
│  or Electron: BrowserWindow + main    │
│        Web UI (React + Vite)          │
└───────────────┬───────────────────────┘
                │ HTTP + WebSocket, 127.0.0.1:8770
┌───────────────▼───────────────────────┐
│  Engine (Python 3.12, FastAPI)        │
│  projects · cue store · job queue     │
│  GPU lock · analysis · agent host     │
└──┬──────────────┬──────────────┬──────┘
   │ HTTP + WS    │ subprocess   │ HTTPS
┌──▼──────────┐ ┌─▼───────────┐ ┌▼────────────┐
│ ComfyUI     │ │ MOSS worker │ │ Agent API   │
│ :8188       │ │ (moss-venv) │ │ (agent only)│
│ ACE-Step 1.5│ │ SFX/ambience│ └─────────────┘
└─────────────┘ └─────────────┘
```

### Engine (shared by both builds)

One Python process, light on dependencies: FastAPI, uvicorn, numpy, scipy, soundfile, pyloudnorm, librosa, and Anthropic's Agent SDK (or any OpenAI-compatible API). It never imports torch. The heavy model code runs in separate processes (ComfyUI and the MOSS worker), so the engine starts in a second and a model crash never takes it down.

It owns:
- **Projects:** opens a project file (PROJECT-FORMAT.md) and its workspace folder.
- **Cue store:** the cues, with their prompts and plans, editable and versioned.
- **Job queue:** generation, post-processing, mapping, loop cuts, recomposes and promotions, one at a time on the GPU, others in parallel on the CPU. Stored in SQLite, so a restart resumes.
- **GPU lock:** one GPU job at a time, across music, effects and art: `gpu.lock` in `%LOCALAPPDATA%\Soda` (or `SODA_LOCK_DIR`), shared with SodaPixel. Only the lock holder may start, restart or stop ComfyUI. Before MOSS loads, ComfyUI is told to unload its models (`POST /free`), and the other way round.
- **ComfyUI belongs to one lock holder at a time.** A GPU run is the GPU jobs the queue runs back to back; the lock is taken by the run's first job and held until no GPU job is left to start (none queued, the queue paused, or the engine stopping), so the other app cannot slip in between two jobs of one run. When the run ends, a ComfyUI this engine started (itself or with the start script) is stopped while the lock is still held, and only then is the lock released: the next holder, in Sodaudio or SodaPixel, starts its own fresh ComfyUI with its own GPU mode and never inherits another app's server or VRAM. An attached ComfyUI (Comfy Desktop, or one someone else started) is never stopped.
- **Quitting never pulls ComfyUI from under the other app.** On quit, the engine stops a ComfyUI it started only when the GPU lock is free or its own. If the other app holds the lock, that ComfyUI may be running the holder's job, so it is left running and handed over: the quitting engine forgets it and the holder treats it as attached (never restarted or stopped by it). The Electron shell follows the same rule when it must end an engine that would not exit: it ends the engine's whole process tree only when the other app does not hold the lock, and otherwise ends the engine alone. (Normally there is nothing to hand over: ComfyUI is only started inside GPU jobs and stopped when the run ends.)
- **Stale locks.** A lock is stale, and is cleared, when its process is gone, when its pid now names a later process (the lock records the holder's process creation time), when it was written before the PC last started, or when it is unreadable. `engine/gpu_lock.py` is byte-identical in SodaPixel and Sodaudio.
- **Process supervisor:** starts, watches and restarts ComfyUI and the MOSS worker; captures their logs.
- **Analysis:** the music map (as JSON data for the UI to draw), loop finding, recomposing.
- **Agent host:** runs the agent (AGENT.md) with the pipeline actions as its tools.
- **Event stream:** one WebSocket that sends job progress, log lines, ComfyUI progress and agent messages to the UI.

### Web UI (shared by both builds)

React, TypeScript and Vite. It talks to the engine only through ENGINE-API.md, so it runs unchanged in a browser tab or in Electron. Screens are in UI.md. Audio plays in the browser (`<audio>` and the Web Audio API for A/B and loop preview); waveforms use wavesurfer.js; the music map is drawn on a canvas from the engine's JSON.

### Shells

- **PWA (BUILD-PWA.md):** the engine serves the built UI at `http://127.0.0.1:8770/` with a web manifest and a service worker, so Edge or Chrome can install it as an app window. `start.cmd` starts the engine and opens the page.
- **Electron (BUILD-ELECTRON.md):** the main process starts the engine as a child process, waits for `/health`, then opens a window on the same URL. A preload script adds a few native calls (folder pickers, real paths for dropped files, notifications, the tray).

## Where things live

| What | Where |
|---|---|
| The tool's code | This repository (Sodaudio): `engine/`, `ui/`, `electron/` |
| App settings (model paths, GPU mode, API key reference) | `%APPDATA%\Sodaudio\settings.json` |
| ComfyUI, MOSS venv, models | Wherever SETUP.md installs or finds them; paths in the settings |
| A game's project file | In the game's repo, for example `Rimtera/audio-project.json` |
| A game's workspace (raw takes, candidates, maps, job database, logs) | Next to the project file in `audio-work/`, git-ignored |
| What the game ships | The project's output folders, for example `Assets/Music` |

## Processes and ports

| Process | Port | Started by |
|---|---|---|
| Engine | 127.0.0.1:8770 | `start.cmd` (PWA) or Electron main |
| ComfyUI | 127.0.0.1:8188 | The engine's supervisor, or an existing ComfyUI if one answers |
| MOSS worker | none (stdin/stdout JSON lines) | The engine, per effects batch |

Everything binds to 127.0.0.1. The engine checks the `Origin` header so only its own UI can call it.

## Why this shape

- **Same engine and UI for both builds:** the two builds differ by a thin shell, so building both costs little more than building one.
- **Models out of process:** ComfyUI and MOSS pin different library versions (MOSS needs transformers 4.57, which ComfyUI's environment cannot take). Rimtera already runs them apart; the engine keeps that.
- **Python engine:** the whole Rimtera pipeline is Python (librosa, soundfile, pyloudnorm). Porting it is mostly moving functions into modules, not rewriting.
- **SQLite job queue:** runs last all night; a crash, a reboot or closing the window must not lose the queue.
