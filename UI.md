# Screens

One window: a sidebar of screens on the left, the agent panel docked on the right (collapsible), and a status bar along the bottom (ComfyUI state, GPU memory, the running job with its progress, focused or restricted mode).

## Projects

Recent projects, "Open project file", "New project" (pick the game folder; choose an importer). Shows each project's cue count and how many cues have no sound.

## Cues

A table of every cue: id, category, trigger, installed (a play button for what the game has now), takes, candidates, locked. Filter by category, text and status ("no sound", "has new candidates", "locked").

Selecting a cue opens its editor:
- card, voice, context, length, loop, level offset, bpm, key, meter, notes;
- the final prompt and the final song plan, as the model will get them (read-only, updated as you type);
- "Generate N takes" and "Make variations";
- the cue's takes and candidates, with play buttons.

## Style

The project's house sound, choir, templates, chant lines and shapes, and shape rules. A preview picks three cues (calm, drive and fight) and shows their final prompts and plans.

## Queue

The job queue and history. Each job: type, cue, state, progress bar with ETA, time taken. Drag to reorder; pause and resume; cancel. Selecting a job shows its live log. A "Server" tab shows ComfyUI's own output as it runs (its progress bars and messages), and an "Engine" tab the engine's log.

## Audition

Candidates grouped by category and cue, each beside what the game has now.
- Play, A/B (switch between two takes at the same position), loop preview (plays the wrap-around point for loops).
- Waveform with loudness, measured tempo and length.
- "Keep" installs as the next take; "Replace generated" swaps one the tool installed; "Reject" hides it.
- Filter: new since last visit, by category, music only.

## Map and recompose (music)

The music map, drawn from `/maps/{file}`: loudness, mel spectrogram, choir band against drums, chroma, numbered bar lines, measured tempo in the title.
- Click to play from any point; drag across bars to select a range; the selection snaps to bar lines.
- "Find a loop here": a loop cut with the selection as `include`, a length range, and the result added to Audition.
- Recompose strip below the map: drag selections into the strip as pieces; set each piece's gain; set the ring-out; play the loop with its wrap-around; "Render" makes the candidate.
- "Export PNG" saves the picture.

## Setup

The checks from SETUP.md with a status for each part, "Install missing" and "Point at existing", the GPU mode, ComfyUI's path and port, the model choice and API key for the agent.

## Agent panel

A chat docked on the right. Tool calls show as small cards (for example "Queued 3 takes of mus_frozen_night"), with links to the job or the candidate. Permission requests show "Allow", "Allow always for this tool" and "Deny".
