# Sodaudio

A desktop tool for directing a game's audio with local AI models and an agent. You describe the sound of a game. The tool generates music, effects and ambience on your own GPU, lets you audition and compare takes, map and recompose music on bar lines, and install the keepers into the game. An agent panel can do any of it on request ("make the frozen biome colder and give me three takes"), and you watch every job run.

It is the Rimtera audio pipeline (`Rimtera/tools/audio`), made independent of any one game.

## Two ways to build it

Both builds share the same engine (Python) and the same interface (web UI). They differ only in the shell around them.

| | Electron app | Python PWA |
|---|---|---|
| What you open | An installed desktop app with its own window and tray icon | A browser tab served by the engine, installable as an app from Edge or Chrome |
| Starts the engine | The app, on launch | `start.cmd` (or a tray script) |
| File paths and drag-drop | Native: drop a file from Explorer and the engine gets its real path | Through the engine's folder browser, or uploads |
| Installer and updates | electron-builder installer and auto-update | `git pull` or a zip; no installer |
| Extra size | About 150 MB for Electron | None beyond Python |
| Build effort, everything | About 7–8 weeks | About 6–7 weeks |
| Build effort, first usable version | About 3½ weeks | About 3 weeks |

**Recommendation:** build the PWA first. It is the engine plus the UI, which both builds need anyway. Wrap it in Electron later if you want the installer, the tray and native drag-drop. Nothing is thrown away, because the Electron build loads the same UI and starts the same engine.

## Running it

- **Browser or installed PWA:** double-click `start.cmd`. In a git checkout it updates itself first (fast-forward only, so your own changes are never overwritten; `--no-update` skips it) and installs any new engine packages. The first run makes `.venv` (Python 3.12, with `uv` if present) and can take a couple of minutes; later runs open the page at once. `stop.cmd` stops the engine, its jobs and the ComfyUI it started (`stop.cmd --finish` lets the running job finish first). Quitting the desktop app does the same: nothing it started keeps running or starts again by itself. `start.cmd --tray` also shows a tray icon. In Edge or Chrome, "Install this site as an app" gives it its own window.
- **Desktop app:** `electron/dist/Sodaudio Setup <version>.exe` installs it per user (no admin). Installed copies update themselves from this repository's GitHub Releases: they check at start and every 4 hours, download a new version in the background, and install it when Sodaudio quits (or at once with "Restart to update" in the status bar or the tray). A running job is finished or stopped first, as you choose. Settings and projects are kept. To publish one: raise `version` in `electron/package.json`, set `GH_TOKEN` (a token with access to this repository), and run `npm run release` in `electron/`; it builds the installer and uploads it with its update files. Build it with `npm install` and `npm run dist` in `electron/` (it bundles a private Python 3.12 with the engine's packages; ComfyUI and the models are never bundled). `npm start` there runs it from the source checkout.
- **Trying it without a GPU:** set Setup > Generator to "Fake", or start the engine with `.venv\Scripts\python.exe -m engine.app --fake`.
- **ComfyUI:** a ComfyUI already running at the address in Setup (Comfy Desktop, or one a game's own tools started) is used as it is and never started, stopped or restarted. Otherwise the engine starts it headless, itself or through a game's start script (Rimtera's `tools/comfy/start_comfy.ps1`), the way the overnight runs did. Setup > "Use the game's own ComfyUI and MOSS" fills all of it in from a game's `tools/` folder. While ComfyUI works for someone else (an art run), audio jobs wait.
- **Agents:** the panel runs Anthropic's Agent SDK or any OpenAI-compatible API (OpenAI, Gemini, Mistral, Groq, OpenRouter, a local Ollama or LM Studio). Any agent that speaks MCP (Codex, Gemini CLI, opencode, Cursor) can drive it too, through `engine/mcp_server.py`; Setup shows the config lines. Installs and big runs always wait for "Allow" in the app, whichever agent asks.
- **Rimtera:** `examples/rimtera/audio-project.json` is Rimtera's project (house sound, choir, chant, the title and final-boss plans). It points at the Rimtera checkout beside this one (`../Rimtera`); installs from it go into Rimtera's `Assets/`.
- **Tests:** `.venv\Scripts\python.exe -m pytest engine/tests` (no GPU: a fake generator, a stand-in ComfyUI and a stand-in MOSS worker). With the engine running: `node ui/e2e/pwa-check.mjs` and `node ui/e2e/notify-check.mjs` (installed Edge), `node electron/e2e-check.mjs` (Electron).

## Documents

Read them in this order.

1. [ARCHITECTURE.md](ARCHITECTURE.md): the parts, how they talk, where data lives.
2. [PROJECT-FORMAT.md](PROJECT-FORMAT.md): the per-game project file (cues, house sound, voices, chants, output folders) and importers.
3. [PIPELINE.md](PIPELINE.md): every pipeline step, ported from the Rimtera scripts, with the lessons learned.
4. [ENGINE-API.md](ENGINE-API.md): the engine's HTTP and WebSocket API, which both shells use.
5. [SETUP.md](SETUP.md): first-run setup: ComfyUI, the models, the Python environments, GPU notes.
6. [AGENT.md](AGENT.md): the agent panel: tools, permissions, prompts.
7. [UI.md](UI.md): the screens.
8. [BUILD-CORE.md](BUILD-CORE.md): phased build plan for the engine and UI, which both builds need first.
9. [BUILD-PWA.md](BUILD-PWA.md): the Python PWA shell.
10. [BUILD-ELECTRON.md](BUILD-ELECTRON.md): the Electron shell.

## Ground rules (carried over from Rimtera)

- Everything runs locally: ComfyUI, the models and the engine all listen on 127.0.0.1 only. The agent is the only part that calls out (its provider's API, unless it is a local model).
- Your own recordings always win. A file already in the game is never overwritten except a generated take the tool itself installed, and only when you ask.
- One focus at a time on the GPU. Audio jobs and art jobs never run together; the engine holds a single GPU lock.
- Long runs are visible and controllable: every job streams its log and progress to the UI, and you can pause, stop or reorder the queue.
- Free, open models only: ACE-Step 1.5 (MIT) for music, MOSS-SoundEffect v2.0 (Apache 2.0) for effects and ambience.
