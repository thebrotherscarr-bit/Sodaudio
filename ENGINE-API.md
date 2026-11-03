# Engine API

The engine listens on `http://127.0.0.1:8770`. Both shells, and the agent's tools, use only this API. JSON in and out. Requests from any `Origin` other than the engine's own page (or Electron's `app://` origin) are refused.

## Health and settings

| Method | Path | Does |
|---|---|---|
| GET | `/health` | `{ok, version, comfy: "up"|"down"|"starting", gpu: {name, vram_free_gb}, busy}` |
| GET | `/settings` | App settings (paths, GPU mode, model choice). The API key is never returned, only whether one is set. |
| PUT | `/settings` | Update settings. |
| GET | `/setup/check` | What SETUP.md's checks find: Python environments, ComfyUI, each model file. |
| POST | `/setup/run` | Starts the setup job for the missing parts. |

## Projects and cues

| Method | Path | Does |
|---|---|---|
| GET | `/projects` | Recent projects. |
| POST | `/projects/open` | `{path}` opens a project file (or creates one with `{path, name, create: true}`). |
| GET | `/project` | The open project, without cues. |
| PATCH | `/project` | Edit style, templates, chant, output folders. |
| POST | `/project/import` | Runs the importer; returns added, changed and missing cue ids. |
| GET | `/cues?category=&q=&status=` | Cues with status: `{id, …fields, installed: [paths], takes: n, candidates: n, locked}`. |
| GET | `/cues/{id}` | One cue, with its composed `prompt_final` and `plan_final`. |
| PATCH | `/cues/{id}` | Edit fields. Each edit is recorded in the project's history so it can be undone. |
| POST | `/cues` | Add a cue. |

## Takes, candidates and installs

| Method | Path | Does |
|---|---|---|
| GET | `/cues/{id}/takes` | Raw takes and their candidates, with length, measured tempo, loudness, and whether installed. |
| GET | `/files/{workspace-relative path}` | Streams an audio file (with range requests, for seeking). |
| GET | `/installed/{cue}` | What the game has now for the cue. |
| POST | `/promote` | `{candidates: [names], mode: "add"|"replace-generated"}`. |
| POST | `/promote/fill-gaps` | Installs first takes for cues with no file. |

## Jobs

| Method | Path | Does |
|---|---|---|
| POST | `/jobs` | Queue jobs. Body: `{type, ...}`; types below. Returns job ids. |
| GET | `/jobs?state=` | Queue and history. |
| GET | `/jobs/{id}` | One job with its log. |
| POST | `/jobs/{id}/cancel` | Cancel a queued job, or interrupt a running one (ComfyUI `POST /interrupt`; MOSS worker is stopped). |
| POST | `/jobs/reorder` | `{ids: [...]}` new queue order. |
| POST | `/queue/pause` / `/queue/resume` | Finish the current job, then hold. |
| POST | `/queue/mode` | `{mode: "focused"|"restricted"}` GPU power mode (PIPELINE.md, lessons). |

Job types:

| type | Body | Runs on |
|---|---|---|
| `generate` | `{cues: [ids], takes: n, seconds?: s}` | GPU |
| `variations` | `{candidate, kinds: ["reverse","pitch"], semitones: [-5,5]}` | CPU |
| `post` | `{takes: [names]}` (automatic after generate) | CPU |
| `map` | `{file, bpm?}` | CPU |
| `loop` | `{take, min, max, include?: [a,b]}` | CPU |
| `recompose` | `{take, pieces: [{from, to, gain_db}], ring?, overlay?}` | CPU |
| `import` | `{}` | CPU |

## Analysis

| Method | Path | Does |
|---|---|---|
| GET | `/maps/{file}` | The map JSON: `{tempo, beats: [s], bars: [s], hop_s, loudness: [], low: [], drums: [], mel: {bins, frames}, chroma: [[12]]}`. Queues a `map` job if not cached (202 with the job id). |
| GET | `/maps/{file}.png` | The exported picture. |

## Agent

| Method | Path | Does |
|---|---|---|
| POST | `/agent/sessions` | Start a conversation; returns an id. |
| POST | `/agent/sessions/{id}/messages` | Send a message. Replies stream on the event socket. |
| POST | `/agent/sessions/{id}/approve` | `{request_id, allow: true|false, always?: bool}` answers a permission request. |
| POST | `/agent/sessions/{id}/stop` | Stop the agent's current turn. |

### Any agent

| Method | Path | Does |
|---|---|---|
| POST | `/agent/external/call` | `{tool, args}`: a tool call from an outside agent (`engine/mcp_server.py`); recorded under the conversation `external`, approval-needing calls wait for the user's answer. Returns `{text}`. |
| GET | `/agent/tools` | The tools with their input schemas. |
| GET | `/agent/mcp` | The MCP server's command line and config snippets for common agent CLIs. |

Settings for the panel's agent: `agent_provider` (`claude` or `openai`), `agent_model`, `agent_base_url`; `PUT /settings` takes `api_key` (Claude) and `agent_key` (OpenAI-compatible), which go to the credential store.

### Setup additions

| Method | Path | Does |
|---|---|---|
| POST | `/setup/adopt` | `{root?, apply?}`: finds a game's own ComfyUI, start script and MOSS setup; with `apply`, saves them. |
| POST | `/setup/point` | Saves paths (`comfy_path`, `comfy_python`, `comfy_extra_model_paths`, `comfy_extra_args`, `comfy_start_script`, `moss_python`, `moss_model_dir`, `comfy_url`). |

### Files without native dialogs

| Method | Path | Does |
|---|---|---|
| GET | `/fs?path=` | Folders and audio files under the allowed roots (`fs_roots`). |
| POST | `/upload` | An audio file into the workspace's `inbox/`. |
| POST | `/reveal` | `{path, open?}`: shows the file in Explorer (or, with `open: false`, only resolves its full path). |
| GET | `/abs?path=`, `/game/{path}`, `/files/{path}` | Audio by absolute path (Electron's dropped files), game-relative path or workspace path, with range requests. |
| POST | `/shutdown` | `{mode: finish|stop}`: finish or interrupt the running job, then exit. |

## Event stream

`ws://127.0.0.1:8770/events`. One socket for everything; each message is `{type, ...}`:

| type | Fields |
|---|---|
| `job` | `{id, state: queued|running|done|failed|cancelled, progress: 0..1, eta_s?, message?}` |
| `log` | `{source: engine|comfy|moss|job, job_id?, line}` |
| `comfy` | `{state, step, of}` (from ComfyUI's own WebSocket) |
| `take` | `{cue, name, kind: raw|candidate}` a new file exists |
| `install` | `{cue, path}` |
| `agent` | `{session, kind: text|tool_use|tool_result|permission|done, ...}` |
| `health` | the `/health` body, on any change |

The UI keeps no state of its own that the engine does not have: reloading the page or reopening the app shows the queue exactly as it is.
