# Agent

The agent panel lets you direct the audio in plain words: "the frozen biome should sound colder, give me three new takes", "map boss take 4 and build a 40-second loop that starts on the chant", "which cues still have no sound?" The agent works only through the engine's own actions, and you watch each one happen in the queue.

## Any agent

The panel is not tied to one vendor. Setup > Agent picks:
- **Claude** through the Claude Agent SDK (below), with a Claude API key or the Claude Code login.
- **Any OpenAI-compatible chat API** (`engine/agent_openai.py`): OpenAI, Google Gemini's OpenAI endpoint, Mistral, Groq, OpenRouter, or a local server (Ollama, LM Studio, llama.cpp, vLLM). A plain tool-calling loop over the same tools, rules and approvals; the model must support tool calls. A local model shares the GPU with ACE-Step and MOSS, so the one-focus rule applies to it.
- **Outside agents over MCP** (`engine/mcp_server.py`): Codex, Gemini CLI, Claude Code, opencode, Cursor or anything else that speaks MCP runs the same tools against the running engine. Their calls appear in the panel under "Outside agents (MCP)", and the approval rules below apply to them exactly as to the panel's own agent: the call waits until you answer in the app. Setup shows the command line and config snippets.

## Where it runs

In the engine, in both builds, using the Claude Agent SDK for Python (`claude-agent-sdk`). One implementation serves the PWA and Electron alike; the UI only shows its messages and permission requests from the event stream (ENGINE-API.md, "Agent").

- **Model:** `claude-opus-5-5` by default; `claude-sonnet-5-5` as a cheaper setting. Pick in the settings.
- **Tools:** an in-process MCP server (`create_sdk_mcp_server`) with the tools below, each a thin wrapper over an engine function. The SDK's built-in tools (shell, file editing, web) are turned off: the agent cannot run commands or edit files except through these tools.
- **Permissions:** the SDK's `can_use_tool` callback sends each request that needs approval to the UI and waits for your answer.
- **Sessions:** a conversation per project, kept in the workspace, so you can come back to it.

## Tools

| Tool | Does | Approval |
|---|---|---|
| `list_cues(category?, query?, status?)` | Cues with status: installed files, takes, candidates | No |
| `get_cue(id)` | One cue with its final prompt and plan | No |
| `edit_cue(id, fields)` | Change card, voice, context, plan, notes, bpm, key, length | No (undoable) |
| `edit_style(fields)` | Change the house sound, choir, templates, chant lines or shapes | Yes |
| `add_cue(fields)` | New cue | No |
| `queue_generate(cues, takes, seconds?)` | Queue generation; returns job ids and an estimated time | Yes when more than 5 tracks or 30 clips |
| `queue_variations(candidate, kinds, semitones)` | Reversed and pitched variants | No |
| `list_jobs(state?)` / `cancel_job(id)` | Look at or cancel the queue | Cancel: yes |
| `get_takes(cue)` | Takes and candidates with measured tempo and loudness | No |
| `map_track(file)` | Map data, summarised for the model: tempo, bar times, loud and quiet stretches, where the choir and drums sit, the key | No |
| `cut_loop(take, min, max, include?)` | Loop cut job | No |
| `recompose(take, pieces, ring?, overlay?)` | Recompose job | No |
| `promote(candidates, mode)` | Install into the game | Always |
| `fill_gaps()` | Install first takes for cues with no file | Always |
| `import_cues()` | Run the project's importer | Yes |

The agent cannot listen. It judges audio only from the map data (tempo, bars, loudness, choir and drum bands, chroma), which is how the Rimtera title loop was found and rebuilt. It must say so when it is choosing by numbers, and it leaves "which take sounds best" to you.

## System prompt (outline)

1. **Role:** you direct the audio of the open game project with the tools given. The user's taste decides; you propose, they listen.
2. **The project:** its name, house sound, choir and chant (from the project file), and each cue's notes (what the user liked and disliked).
3. **Rules:**
   - Never install over a file that was not installed by the tool. Ask before any install.
   - Sung parts use invented syllables only, never real words.
   - Measure, don't trust: use the measured tempo from `map_track`, not the cue's `bpm`.
   - Map before cutting; cut on bar lines; never leave a near-silent stretch in a loop; never splice automatically.
   - Keep vocals untouched; raise a quiet piece with gain rather than processing it.
   - Say how long a queued run will take and how much GPU time it uses before queuing a large run.
   - One GPU focus: do not queue art and audio together.
4. **Style of replies:** short. After queuing, say what was queued and where to listen.

## Example

> **You:** The frozen night track is too busy. Make it sparse and breathy, and give me two takes.
>
> **Agent:** calls `get_cue("mus_frozen_night")`, then `edit_cue` with voice "cold hollow monks, breathy held vowels, icy reverb, hushed and sparse" and notes "user: too busy, wants sparse", then `queue_generate(["mus_frozen_night"], takes=3)`, which tops the cue up from 1 take to 3. Two tracks are under the approval limit, so it runs without asking.
>
> "Queued two new takes of frozen night, about 10 minutes. They'll appear in Audition under Frozen when done."

## Cost and limits

- Each message costs Claude API tokens. Tool results are kept short (map data is summarised, not sent whole) to keep turns cheap.
- The settings show the session's token use.
- Without an API key the panel is hidden; everything else works.
