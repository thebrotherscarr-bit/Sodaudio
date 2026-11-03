# Pipeline

Each step is a job type in the engine. Every step is ported from a working Rimtera script in `Rimtera/tools/audio/`; the "Port" line says what changes.

```
import cues → generate takes → post-process → audition → install
                                    │
                         music: map → loop cut / recompose → audition → install
```

## 1. Import cues

Fills the project's cue list (PROJECT-FORMAT.md, "Importers").
- **From:** `overnight_audio.py` step 1 (`dotnet run -- --sound-cues` → `audio_needs.tsv`) and `audio_common.load_needs`.
- **Port:** an `importers/` module with one function per type; merge by id.

## 2. Generate music (ACE-Step 1.5 in ComfyUI)

For each cue and take: build the prompt and plan, send the ACE-Step graph to ComfyUI, wait, download the FLAC to `raw/music/<cue>__c<n>.flac`.
- **From:** `gen_music.py`, `audio_client.py` (`AceStep.music_workflow`, `run_audio`), the `PLANS`, `SHAPES` and `plan()` chant code.
- **Talking to ComfyUI:** `POST /prompt` with `{prompt: graph, client_id}` returns a `prompt_id`; the engine listens on `ws://127.0.0.1:8188/ws?clientId=<id>` for `progress` (step, of steps), `executing` and `execution_error`, and reads `GET /history/<prompt_id>` for the output file, fetched with `GET /view?filename=…&subfolder=…&type=output`. Rimtera polls `/history`; the engine uses the WebSocket so the UI gets live progress, with `/history` as the fallback.
- **Inputs to the graph:** tags (the prompt), lyrics (the plan), seconds, bpm, key, time signature, seed, 8 steps (the turbo model).
- **Port:** a `generators/acestep.py` module; the graph builder moves as is. Takes are queued per cue; `--takes N` becomes "top up to N takes".
- **Retry:** on a dropped connection, restart ComfyUI once and retry the take (Rimtera does this).
- **Known issue:** in Rimtera, long runs slowed from 2–4 minutes a track to 8–24. Until the cause is found, the supervisor restarts ComfyUI after a configurable number of tracks (start with 10) and logs the time per track so the slowdown can be seen in the UI.

## 3. Generate effects and ambience (MOSS-SoundEffect v2.0)

Batches of `{prompt, seconds, seed, out}` jobs go to the MOSS worker, which loads the model once per batch and writes 48 kHz mono WAVs.
- **From:** `gen_sfx.py`, `moss_worker.py` (JSON-lines jobs file, skip existing outputs).
- **Port:** the worker stays a separate script run with the MOSS venv's Python. It reads jobs from stdin and prints one JSON line per finished clip (`{"done": out, "seconds": t}`), so the engine streams progress. Before it starts, the engine calls ComfyUI's `POST /free` with `{"unload_models": true, "free_memory": true}`.
- **Variations (new):** the user's idea from Rimtera: make extra takes from one clip by reversing it and pitching it up and down (±3, ±5, ±7 semitones, with and without time-stretch). A CPU job; results land in `candidates/` as `<cue>__c<n>_rev`, `_up5`, and so on.

## 4. Post-process

For each new take, write a game-ready candidate WAV.
- **From:** `audio_post.py` and `finish_one.py`.
- **Does:** remove DC; effects: trim leading and trailing silence with short fades, make mono; loops: crossfade the tail into the head; 44.1 kHz 16-bit; level to the project LUFS plus the cue's offset with peaks under the ceiling (pyloudnorm).
- **Port:** a `post.py` module; runs automatically after each generated take (CPU, outside the GPU lock).

## 5. Audition

Play candidates next to what the game has now, compare, mark keepers.
- **From:** `make_audition.py` (a static HTML page with checkboxes and "Copy keep list").
- **Port:** becomes the Audition screen (UI.md). Keeping a take is a button, not a pasted list.

## 6. Install (promote)

Copy kept candidates into the game's output folders as the cue's next take.
- **From:** `promote.py` (`--keep`, `--fill-gaps`, `--replace-generated`) and its `promoted.tsv` log.
- **Rules:** never overwrite an existing file; your recordings always win. Music and ambience become OGG Vorbis. Every install is logged, so "replace generated" can swap only files the tool installed. "Fill gaps" gives every cue with no file its first takes, so the game never falls back to silence or a synth placeholder.
- **Port:** a `promote.py` module; output folder, format and naming come from the project file. Install actions in the agent always ask you first (AGENT.md).

## 7. Map (music)

Analyse a take so you can see its sections before cutting it.
- **From:** `music_map.py` and `music_map.cmd`.
- **Computes:** beat times (librosa's tracker started at the cue's tempo) and bar starts (every 4 beats from the strongest downbeat); loudness; a mel spectrogram; the harmonic part's 80–500 Hz band (where a deep choir sits) next to the percussive part (drums); chroma (which notes sound); the measured tempo.
- **Port:** `analysis/map.py` returns all of it as JSON (arrays downsampled to about 20 points a second), cached in `maps/`. The UI draws it on a canvas, so you can hover, zoom, select bar ranges and play from any point. "Export PNG" keeps the matplotlib picture for sharing.

## 8. Loop cut (music)

Find the best seamless loop of a given length inside a take.
- **From:** `loop_cut.py` (`--min`, `--max`, `--include A-B`).
- **Does:** every candidate stretch is a whole number of bars between min and max seconds; skip stretches with a quiet patch (except inside `include`); keep the one whose end sounds most like its start and is loudest; crossfade the join over half a beat.
- **Port:** `analysis/loop.py`; in the UI, select a range on the map to set `include`.

## 9. Recompose (music)

Build a loop from chosen pieces of a take, joined on bar lines.
- **From:** `recompose.py` (pieces `A-B[:gain]`, `--ring`, `--overlay A-B@T`, `--clean C-D`, `--overlay-db`).
- **Does:** copies each piece from the uncut raw take, applies its gain in dB (the audio itself is untouched), joins pieces with equal-power crossfades a quarter beat long centred on the cut, lays the last piece's ring-out over the loop's head, and levels the result like every candidate.
- **Port:** `analysis/recompose.py`; in the UI, drag bar ranges from the map into a strip, set gains, press play to hear the loop wrap around.

## Lessons from Rimtera (build these in)

- **Measure, don't trust.** The model does not keep the tempo it is asked for: the Rimtera title was asked for 76 BPM and came out at 65; the final boss asked for 170 and came out at 86. All bar maths uses the measured tempo from the map, never the cue's `bpm`.
- **Map first, then cut.** Recomposing on bar lines from the map worked; cutting by ear did not.
- **Don't clean vocals.** Spectral subtraction to remove a drone under a vocal "sounds weird". Prefer gain-only pieces; keep `--clean` as an expert option, off by default.
- **Never drop to near-silence.** Loop finding skips quiet patches; recompose evens out levels with per-piece gain.
- **No automatic gap splicing.** Closing gaps automatically created jarring "cuts out and jumps" moments. Only splice where the user chose to.
- **Short loops for themes.** A theme of 30–45 seconds that loops beats a 150-second take that wanders.
- **Song plans give shape.** With only "[Instrumental]" the model builds, drops and wanders. Section tags in the lyrics field give a track a beginning, a build and a climax.
- **Invented words only.** Sung parts use invented syllables (the "Rim-te-ra" chant), never real words.
- **One focus on the GPU.** Audio and art never run together. "Focused" uses about 80% of the machine (all but 1.5 GB of VRAM, normal priority); "restricted" leaves 4 GB of VRAM free with below-normal priority and short rests between jobs so the PC stays usable.
- **Recordings always win** and **raw takes are never edited**; everything derived is a new file.
- **Windows process care.** Read child output as UTF-8 bytes, treat stderr lines as log lines rather than errors (libraries print warnings there), and set `MIOPEN_LOG_LEVEL=3` on AMD to quiet MIOpen.
