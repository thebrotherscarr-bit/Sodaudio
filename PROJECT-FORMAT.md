# Project format

Each game gets one project file, `audio-project.json`, normally in the game's repo root. It says what the game needs and how it should sound. The tool never needs the game's code; an importer can fill the cue list from it.

## Example

```json
{
  "format": 1,
  "name": "Rimtera",
  "workspace": "audio-work",
  "output": {
    "sfx":      { "folder": "Assets/Sounds",   "format": "wav", "naming": "{cue}{_n}" },
    "music":    { "folder": "Assets/Music",    "format": "ogg", "naming": "{cue}{_n}" },
    "ambience": { "folder": "Assets/Ambience", "format": "ogg", "naming": "{cue}{_n}" }
  },
  "levels": { "lufs": -16, "peak_dbfs": -1 },
  "style": {
    "house_sound": "driving techno pulse and drum and bass breakbeats under funky classical strings and baroque runs, MIDI synth-pop leads, upbeat, classical with a semi-modern edge",
    "music_suffix": "in a MIDI style: General MIDI sound-module instruments (synth leads, sampled strings, brass, a drum kit), 16-bit era like a classic sandbox game soundtrack, loopable",
    "choir": "epic post-apocalyptic, very deep basso profundo male monk choir, slow sustained low chanting with long held vowels, Gregorian-style, invented words only",
    "sfx_suffix": "clean game sound effect, no music"
  },
  "templates": {
    "music": "{card}. {house_sound}. {choir}. {voice}, with a low droning hum underneath. video game {context} {music_suffix}",
    "sfx": "{card}. {sfx_suffix}",
    "ambience": "{card}. seamless ambient loop"
  },
  "chant": {
    "lines": ["Riiim... teee... raaa...", "Riiim-terr-raaa...", "Riiim-ta-raaa...", "Raaa... te... riiim..."],
    "shapes": {
      "calm":  ["[Intro - low monk drone alone, slow and wide]", "[Verse - the music enters, monks hum low underneath]", "[Chorus - the choir swells, slow held chant]"],
      "drive": ["[Intro - low monk drone, the beat builds in]", "[Chorus - full choir, slow half-time chant over the beat]"],
      "fight": ["[Intro - low monk drone alone, ominous, war drums]", "[Chorus - overwhelming, full choir and full band]"]
    },
    "shape_rules": [
      { "match": "mus_boss_*", "shape": "fight" },
      { "bpm_at_least": 144, "shape": "fight" },
      { "match": ["*_night", "*_underground"], "shape": "calm" },
      { "bpm_at_most": 110, "shape": "calm" },
      { "shape": "drive" }
    ],
    "uppercase_choruses": true
  },
  "importer": { "type": "command", "command": "dotnet run -- --sound-cues", "format": "rimtera-tsv", "output": "tools/audio/audio_needs.tsv" },
  "cues": [
    {
      "id": "mus_frozen_day",
      "category": "music",
      "trigger": "frozen, daytime",
      "variants": 1,
      "seconds": 150,
      "loop": true,
      "offset_db": -4,
      "bpm": 120, "key": "D major", "meter": 4,
      "card": "crystalline baroque counterpoint, harpsichord and bells, cold airy shimmer, half-time beat",
      "voice": "cold hollow monks, breathy held vowels, icy reverb",
      "context": "frozen biome music",
      "plan": null,
      "prompt": null,
      "locked": false,
      "notes": ""
    }
  ]
}
```

## Cue fields

| Field | Meaning |
|---|---|
| `id` | The cue's name; files are named after it. |
| `category` | `sfx` (or a finer group such as `weapons`, `ui`), `music` or `ambience`. Decides the generator and output folder. |
| `trigger` | When the game plays it, in words. For people and the agent. |
| `variants` | How many takes the game wants installed (`cue`, `cue_2`, …). |
| `seconds` | Length to generate. |
| `loop` | Loops in the game: post-processing crossfades the tail into the head. |
| `offset_db` | Level against the project's LUFS target. |
| `bpm`, `key`, `meter` | Music only. What is asked for, not what the model delivers (PIPELINE.md, "measure, don't trust"). |
| `card` | The style card: what this cue is, in a line. |
| `voice` | Music: how the choir sings here. Empty for instrumental cues. |
| `context` | Music: what kind of track ("frozen biome music", "final boss battle theme"). |
| `plan` | Music: a hand-written song plan (lyrics field with section tags). `null` builds one from `chant`. `"[Instrumental]"` for none. |
| `prompt` | A full prompt that overrides the template. Importers that compose prompts themselves fill this. |
| `locked` | The installed file is final: no new takes are generated and nothing replaces it (the Rimtera title theme). |
| `notes` | Free text: what you liked, what to avoid. The agent reads it. |

## How a prompt is built

1. If `prompt` is set, it is used as is.
2. Otherwise the category's template is filled from the cue (`card`, `voice`, `context`) and `style`.
3. Music gets a lyrics field: `plan`, or one built from `chant`: pick the shape by the first matching `shape_rules` entry, shuffle `lines` with a seed from the cue id, give each section two lines, and capitalise chorus lines unless the shape is `calm`.

The UI shows the final prompt and plan for every cue, so nothing is hidden.

## Seeds

Take *n* of a cue uses seed `int(sha1("{id}#{n}")[:8], 16)`, as in Rimtera. Regenerating a take with the same prompt reproduces it; a changed prompt gives a new take at the same seed.

## Importers

An importer fills or refreshes `cues` from the game. It merges by `id`: new cues are added, removed cues are marked `missing` (never deleted), and fields you edited in the tool are kept unless the importer owns them.

| Type | Does |
|---|---|
| `command` | Runs a command in the game folder and reads its output file. Formats: `rimtera-tsv` (the columns of `audio_needs.tsv`: cue, category, trigger, variants, seconds, loop, offset, lane, fallback, bpm, key, meter, prompt), `csv`, `json`. |
| `folder-scan` | Lists existing audio files in the output folders and makes a cue for each, so you can regenerate or add takes to a game that has no cue list. |
| `manual` | No importer; you add cues in the UI or ask the agent to. |

A Rimtera import fills `prompt` with the already-composed prompts from `SoundCatalog.cs`. To edit the house sound or voices in the tool instead, run "Decompose prompts" once: it splits `prompt` into `card`, `voice` and `context` where the text matches the known pieces, and clears `prompt`.

## Workspace layout

Inside the project's `workspace` folder (git-ignored):

```
audio-work/
  raw/<category>/<cue>__c<n>.<flac|wav>      generated takes, never edited
  candidates/<category>/<name>.wav           post-processed, levelled takes for audition
  maps/<name>.json                           music map data (and .png when exported)
  promoted.tsv                               every file the tool installed: cue, path, source take, time
  jobs.sqlite                                the job queue and history
  logs/                                      engine, ComfyUI and MOSS logs per day
```

`promoted.tsv` keeps Rimtera's columns (cue, installed path, candidate name), so an existing Rimtera workspace can be adopted by pointing `workspace` at `tools/audio` and moving `raw/`, `candidates/` and `promoted.tsv` as they are.
