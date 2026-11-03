# Build plan: Electron app

An installed Windows app. Its main process starts the engine, shows the same web UI in a window, and adds native features: real file paths, folder pickers, a tray icon, notifications, an installer and auto-update. Build BUILD-CORE.md first; this adds the shell. If the PWA already exists, everything from it carries over.

## How it fits together

```
electron/
  main.ts        app lifecycle, engine process, window, tray, menus, auto-update
  preload.ts     the small native API exposed to the UI (contextBridge)
  engine.ts      start, health-wait, restart and stop the engine
  package.json   electron, electron-builder, electron-updater
```

- **Security:** `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`. The UI reaches native features only through `window.native` from the preload script; everything else goes to the engine API.
- **Loading the UI:** in development, the Vite dev server; when packaged, `ui/dist` served by the engine (same as the PWA), so there is a single origin for the API and the event socket.
- **The engine:** a child process. Electron does not run any Python itself.

## Phase E1: shell and engine process (about 2 days)

- `engine.ts`: start `python -m engine.app --port 8770` with the engine's Python, read its stdout and stderr as UTF-8 lines into the log, wait for `/health`, restart it if it exits unexpectedly (at most 3 times in a minute, then show the error).
- On quit: `POST /api/shutdown` (finish or stop the current job, as you choose in a dialog when a job is running), then end the process tree.
- Single instance lock: a second launch focuses the existing window.
- Window state (size and position) remembered.

**Done when:** launching the app starts the engine and shows the UI; quitting leaves no Python, ComfyUI or MOSS processes behind.

## Phase E2: native features (about 3 days)

Exposed by `preload.ts` as `window.native`, which the UI checks for and falls back to the PWA's ways when it is missing:

| Call | Does |
|---|---|
| `pickFolder()`, `pickFile(filters)` | Native dialogs; return real paths |
| `pathForFile(file)` | Real path of a dropped file (`webUtils.getPathForFile`), so dropped files are used in place, never uploaded |
| `reveal(path)` | `shell.showItemInFolder` |
| `notify(title, body)` | Native notification with a click that opens the job |
| `setProgress(0..1)` | Taskbar progress bar for the running queue |

**Done when:** dropping a FLAC from Explorer onto the Map screen maps it in place, and the taskbar shows the queue's progress.

## Phase E3: tray and background running (about 1 day)

- Closing the window keeps the app in the tray while jobs run (a setting); the tray menu shows the running job and has "Open", "Pause queue", "Quit".
- Optional start with Windows (`app.setLoginItemSettings`), minimised to the tray.
- Optional "keep the PC awake while the queue runs" (`powerSaveBlocker`).

**Done when:** an overnight queue keeps running with the window closed, and the PC does not sleep mid-run.

## Phase E4: packaging (about 3 days)

- **electron-builder**, NSIS installer for Windows, per-user install (no admin).
- **Bundled Python:** ship the engine with a portable Python 3.12 (the python.org embeddable package, or a `uv`-installed Python) and the engine's packages pre-installed into `resources/engine`. Size: about 300 MB. ComfyUI, torch and the models are never bundled; the Setup screen installs or finds them (SETUP.md).
- **API key:** stored with `safeStorage` and handed to the engine at start through an environment variable, never written to disk in plain text.
- **Auto-update:** electron-updater from GitHub Releases on a private or public repo you choose; updates the app and the bundled engine together.
- **Code signing:** optional. Without it, Windows SmartScreen warns on first install.

**Done when:** the installer works on a clean Windows user account, the app updates itself from a new release, and an update keeps the settings and projects.

## Total

BUILD-CORE.md (about 5–6 weeks) plus about 2 weeks here. A usable first version (core phases 1–4 plus E1–E2) is about 3½ weeks.

## Trade-offs against the PWA

- **For:** real file paths and drag-drop, native dialogs, tray and background running, one installer with updates, the engine starts and stops with the app.
- **Against:** about 150 MB more to download, a Node toolchain to maintain, and an extra packaging step for each release.

## Alternative: the agent in Node

If you ever want the agent in Electron's main process instead of the engine, the Agent SDK also comes as a TypeScript package with the same ideas (custom tools through an in-process MCP server, a `canUseTool` callback). The tools would then call the engine's HTTP API. Keeping it in the engine (AGENT.md) is simpler, because both builds share it.
