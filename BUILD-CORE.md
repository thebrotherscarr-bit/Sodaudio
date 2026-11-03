# Build plan: engine and UI (shared)

Both builds start here. These phases make the engine and the web UI; BUILD-PWA.md and BUILD-ELECTRON.md add the shell. Each phase ends in something you can use, and each has a "done when" test.

## Code layout

```
Sodaudio/
  engine/
    app.py              FastAPI app, routes, event socket
    settings.py         app settings, keyring for the API key
    project.py          project file load/save, prompt and plan composition, history
    importers.py        command (rimtera-tsv, csv, json), folder-scan
    jobs.py             SQLite queue, GPU lock, runner, cancel, reorder, pause
    supervisor.py       start/stop/watch ComfyUI, restart every N tracks, log capture
    generators/
      acestep.py        ComfyUI graph, submit, WebSocket progress, fetch
      moss.py           runs moss_worker.py, streams its JSON lines
      moss_worker.py    (from Rimtera) the MOSS model in its own venv
    post.py             level, trim, loop crossfade (from audio_post.py)
    promote.py          install, fill gaps, replace generated, promoted.tsv
    analysis/
      map.py            map data and PNG export (from music_map.py)
      loop.py           (from loop_cut.py)
      recompose.py      (from recompose.py)
      variations.py     reverse and pitch
    agent.py            Agent SDK session, MCP tools, permission bridge
    requirements.txt
    tests/
  ui/
    src/                React + TypeScript (Vite)
    package.json
  start.cmd             (PWA) start the engine and open the page
  electron/             (Electron build only)
```

## Phase 1: engine skeleton and project file (about 3 days)

- FastAPI app on 127.0.0.1:8770 with `/health`, `/settings`, the event socket and Origin checking.
- `project.py`: load and save `audio-project.json`; prompt and plan composition exactly as PROJECT-FORMAT.md describes; edit history with undo.
- `importers.py`: `rimtera-tsv`, `csv`, `json`, `folder-scan`; merge by id.
- Routes for projects and cues.
- Tests: composing the Rimtera prompts and chant plans gives the same text `Rimtera/tools/audio/gen_music.py` and `SoundCatalog.cs` give for a few cues (title, frozen day, final boss).

**Done when:** importing Rimtera lists all of its cues, and `GET /cues/mus_frozen_day` shows the same final prompt the game's exporter writes.

## Phase 2: job queue, supervisor and music generation (about 4 days)

- `jobs.py`: SQLite queue (jobs table: id, type, body, state, progress, created, started, finished, log path), one GPU worker and a CPU pool, pause, cancel, reorder; resumes after a restart.
- `supervisor.py`: find or start ComfyUI with the right flags for the GPU mode; health checks; capture its output to the log and the event stream; restart after N tracks or on a dropped connection.
- `generators/acestep.py`: port `audio_client.py`; use ComfyUI's WebSocket for progress; skip takes that exist; seeds as in Rimtera.
- `post.py`: port `audio_post.py`; runs after each take.

**Done when:** queuing 2 takes for one cue shows live progress, survives closing and restarting the engine mid-run, and leaves two levelled candidates.

## Phase 3: effects, ambience and variations (about 3 days)

- `generators/moss.py` and `moss_worker.py` (stdin jobs, JSON-line progress); unload ComfyUI first.
- `analysis/variations.py`: reverse and pitch (librosa `effects.pitch_shift`, with and without time-stretch).

**Done when:** queuing a batch of 10 effects runs them with progress, and variations of one appear as candidates.

## Phase 4: UI first pass (about 5 days)

- Vite + React + TypeScript; one API client module and one event-socket store.
- Screens: Projects, Cues (with the cue editor and final prompt preview), Queue (with live logs and the Server tab), Audition (play, A/B, keep, reject), Setup (checks only).
- Status bar with ComfyUI state, GPU memory and the running job.

**Done when:** a full loop works without a terminal: open the project, edit a cue, generate takes, watch them run, audition, keep one, and find it in the game's folder.

## Phase 5: install, map, loop and recompose (about 5 days)

- `promote.py`: port; install, fill gaps, replace generated; `promoted.tsv`.
- `analysis/map.py`, `loop.py`, `recompose.py`: port; map returns JSON (PIPELINE.md, step 7).
- UI: the Map and recompose screen (canvas drawing, bar-snapped selection, the recompose strip, playback with wrap-around).

**Done when:** you can rebuild the Rimtera title loop in the UI from `mus_title__c8` (the pieces 139.04–142.73 at +8.5 dB, 142.73–146.43 at +14 dB, 61.51–91.05) and the result matches the installed loop.

## Phase 6: agent (about 5 days)

- `agent.py`: Agent SDK client per session, the tools in AGENT.md as an in-process MCP server, built-in tools turned off, `can_use_tool` bridged to the UI, map summaries for the model.
- UI: the agent panel with tool cards and permission prompts.
- Settings: model choice, API key in the credential store, token use.

**Done when:** "give the frozen night track a breathier choir and two new takes" edits the cue, queues the takes after asking, and they appear in Audition.

## Phase 7: setup and hardening (about 4 days)

- Setup job: find or install ComfyUI, download the models, build the MOSS venv (SETUP.md); the smoke test.
- Logging per day, log rotation, crash reporting into the log, handling a full disk.
- Time-per-track chart in the Queue to watch the long-run slowdown.

**Done when:** on a clean Windows user account with only the GPU driver, the setup screen gets to a working smoke test.

## Testing

- **Engine:** pytest for composition, importers, the queue (with a fake generator), post-processing (known input, known LUFS), loop and recompose (synthetic click tracks with a known tempo).
- **ComfyUI and MOSS:** an integration test that runs only when they are present, with a 5-second take.
- **UI:** Playwright against the engine running with the fake generator, for the main flows.
