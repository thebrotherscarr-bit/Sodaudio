# Build plan: Python PWA

The engine serves the web UI itself. You start it with `start.cmd`, and Edge or Chrome can install the page as an app with its own window and taskbar icon. Build BUILD-CORE.md first; this adds the shell.

## How it fits together

- The engine mounts the built UI (`ui/dist`) at `/` with FastAPI's `StaticFiles`, beside the API.
- `http://127.0.0.1:8770` counts as a secure origin, so the service worker and "Install app" work without HTTPS.
- The service worker caches only the app shell (HTML, JS, CSS, icons), so the window opens instantly. All data comes live from the engine; when the engine is not running the page shows "Engine not running – start it with start.cmd".

## Phase P1: serve and start (about 1 day)

- Mount `ui/dist` at `/`, with the API under `/api` (set the UI's API base to `/api`), and the event socket at `/api/events`.
- `start.cmd`:
  - creates `.venv` and installs `engine/requirements.txt` on first run (`py -3.12 -m venv`, or `uv` if present);
  - starts the engine (`.venv\Scripts\python.exe -m engine.app`) in its own window titled "Sodaudio engine", so you see its log and can close it;
  - waits for `/api/health`, then opens `http://127.0.0.1:8770` in the default browser.
- A second double-click while it runs only opens the page.
- `stop.cmd`: asks the engine to finish the current job and exit (`POST /api/shutdown`).

**Done when:** double-clicking `start.cmd` on a fresh checkout ends with the app open in the browser.

## Phase P2: installable app (about 1 day)

- `ui/public/manifest.webmanifest`: name "Sodaudio", `display: "standalone"`, `start_url: "/"`, theme colours, icons at 192 and 512 px.
- A service worker (Vite PWA plugin, `vite-plugin-pwa`, `registerType: "autoUpdate"`) that pre-caches the shell and never caches `/api`.
- An "Engine not running" screen when `/api/health` fails, with the command to run.

**Done when:** Edge's "Install this site as an app" gives a window with its own taskbar icon, which opens to the "Engine not running" screen when the engine is off.

## Phase P3: files without native dialogs (about 2 days)

A browser cannot learn a file's real path, so the engine does the file picking.
- **Folder browser:** `GET /api/fs?path=` lists folders and audio files (limited to drives and folders you allow in the settings). The UI's "Open project" and "Pick folder" use it.
- **Drop to map:** dropping an audio file on the Map screen uploads it to the workspace's `inbox/` and maps it there. For files already in the workspace or the game, the engine uses them in place.
- **Reveal in Explorer:** `POST /api/reveal {path}` opens Explorer at the file (the engine runs `explorer /select,`).

**Done when:** you can open a project, map a dropped file and reveal an installed take without typing a path.

## Phase P4: comfort (about 1 day)

- Desktop notifications from the page (Notifications API) when a long job or the whole queue finishes.
- Optional tray script (`tray.pyw`, `pystray`): shows queue state, "Open", "Pause queue", "Quit engine".
- Optional start with Windows: a shortcut to `start.cmd --no-open` in the Startup folder, made from the settings.

**Done when:** you get a notification when an overnight queue finishes.

## Total

BUILD-CORE.md (about 5–6 weeks) plus about 1 week here. A usable first version (core phases 1–4 plus P1–P2) is about 3 weeks.

## Trade-offs against Electron

- **For:** no Node runtime to ship, smallest download, one language for the backend, updates with `git pull`.
- **Against:** no real paths for dropped files (uploads or the folder browser instead), the engine is a separate window or tray icon you start yourself, and no installer or auto-update.
