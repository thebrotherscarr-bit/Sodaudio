"""The agent's tools (AGENT.md), each a thin wrapper over an engine function, and the rule for which need approval.
Kept apart from the Agent SDK so they can be tested without it. Results are short: the model pays for every token."""
import json

from . import catalog, promote
from .analysis import map as map_mod
from .pipeline import get_map, plan_takes
from .project import kind_of

EDITABLE = {"card", "voice", "context", "plan", "notes", "bpm", "key", "meter", "seconds", "variants", "offset_db", "loop", "trigger"}
STYLE_KEYS = {"style", "templates", "chant"}
OBJ = {"type": "object"}
STR = {"type": "string"}
NUM = {"type": "number"}

SCHEMAS = {
    "list_cues": ("List the project's cues with status (installed files, takes, candidates, locked). Filters are optional; "
                  "status is one of no-sound, has-candidates, locked.",
                  {"type": "object", "properties": {"category": STR, "query": STR, "status": STR}}),
    "get_cue": ("One cue with its final prompt and song plan as the model gets them.", {"type": "object", "properties": {"id": STR}, "required": ["id"]}),
    "edit_cue": ("Change a cue's fields: card, voice, context, plan, notes, bpm, key, meter, seconds, variants, offset_db, loop, trigger. Undoable.",
                 {"type": "object", "properties": {"id": STR, "fields": OBJ}, "required": ["id", "fields"]}),
    "edit_style": ("Change the project's style (house_sound, choir, suffixes), templates or chant (lines, shapes, shape_rules). "
                   "Pass whole replacement objects for the keys you change.",
                   {"type": "object", "properties": {"fields": OBJ}, "required": ["fields"]}),
    "add_cue": ("Add a cue. fields needs id and category (sfx, a finer sfx group, music or ambience).",
                {"type": "object", "properties": {"fields": OBJ}, "required": ["fields"]}),
    "queue_generate": ("Top the cues up to `takes` takes each (c1..cN; existing takes are kept). Returns job ids and an estimate.",
                       {"type": "object", "properties": {"cues": {"type": "array", "items": STR}, "takes": {"type": "integer"}, "seconds": NUM},
                        "required": ["cues"]}),
    "queue_variations": ("Reversed and pitched variants of a candidate (kinds: reverse, pitch; semitones like [-5, 5]).",
                         {"type": "object", "properties": {"candidate": STR, "kinds": {"type": "array", "items": STR},
                                                           "semitones": {"type": "array", "items": {"type": "integer"}}}, "required": ["candidate"]}),
    "list_jobs": ("The queue and recent history. state filters: queued, running, done, failed, cancelled (comma-separated).",
                  {"type": "object", "properties": {"state": STR}}),
    "cancel_job": ("Cancel a queued job or interrupt a running one.", {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
    "get_takes": ("A cue's raw takes and candidates with length, loudness (LUFS) and measured tempo, and what is installed.",
                  {"type": "object", "properties": {"cue": STR}, "required": ["cue"]}),
    "map_track": ("Map a take or candidate (name like mus_title__c8, or a workspace path): measured tempo, key, bars with level, "
                  "choir band and drums, quiet bars. Use it before cutting.",
                  {"type": "object", "properties": {"file": STR}, "required": ["file"]}),
    "cut_loop": ("Queue a loop cut: the best seamless loop of min..max seconds, on bar lines; include [a, b] keeps a moment inside it.",
                 {"type": "object", "properties": {"take": STR, "min": NUM, "max": NUM, "include": {"type": "array", "items": NUM}},
                  "required": ["take"]}),
    "recompose": ("Queue a recompose: pieces [{from, to, gain_db}] in seconds of the take, in playing order, joined on bar lines; "
                  "ring = where the last piece's sound dies away; overlay = {from, to, at, db}.",
                  {"type": "object", "properties": {"take": STR, "pieces": {"type": "array", "items": OBJ}, "ring": NUM, "overlay": OBJ},
                   "required": ["take", "pieces"]}),
    "promote": ("Install candidates into the game. mode add (next take) or replace-generated (swap files the tool installed).",
                {"type": "object", "properties": {"candidates": {"type": "array", "items": STR}, "mode": STR}, "required": ["candidates"]}),
    "fill_gaps": ("Install first takes for every cue that has no file.", {"type": "object", "properties": {}}),
    "import_cues": ("Run the project's importer to refresh the cue list from the game.", {"type": "object", "properties": {}}),
}

ALWAYS_ASK = {"promote", "fill_gaps"}


def needs_approval(services, name, args) -> str | None:
    """Why this call needs the user's OK, or None."""
    if name in ALWAYS_ASK:
        return "installs into the game"
    if name == "edit_style":
        return "changes the house style for every cue"
    if name == "cancel_job":
        return "cancels a job"
    if name == "import_cues":
        return "runs the game's importer"
    if name == "queue_generate":
        items, _ = plan_takes(services.require_project(), args.get("cues", []), args.get("takes"))
        tracks = sum(1 for cue, _, _ in items if kind_of(cue["category"]) == "music")
        clips = len(items) - tracks
        if tracks > 5 or clips > 30:
            return f"queues {tracks} music tracks and {clips} clips"
    return None


def _status_filter(row, status):
    if status == "no-sound":
        return not row["installed"]
    if status == "has-candidates":
        return row["candidates"] > 0
    if status == "locked":
        return bool(row.get("locked"))
    return True


def list_cues(services, category=None, query=None, status=None):
    project = services.require_project()
    rows = project.status(project.cues(include_missing=False))
    if category:
        rows = [r for r in rows if r["category"] == category or r["kind"] == category]
    if query:
        q = query.lower()
        rows = [r for r in rows if q in r["id"].lower() or q in (r.get("trigger") or "").lower()]
    rows = [r for r in rows if _status_filter(r, status)]
    short = [{"id": r["id"], "category": r["category"], "trigger": r.get("trigger"), "installed": len(r["installed"]),
              "takes": r["takes"], "candidates": r["candidates"], **({"locked": True} if r.get("locked") else {})} for r in rows[:200]]
    return {"count": len(rows), "cues": short, **({"truncated": True} if len(rows) > 200 else {})}


def get_cue(services, id):
    project = services.require_project()
    cue = project.cue(id)
    return {**{k: v for k, v in cue.items() if k != "edited"}, "prompt_final": project.compose_prompt(cue), "plan_final": project.compose_plan(cue)}


def edit_cue(services, id, fields):
    bad = set(fields) - EDITABLE
    if bad:
        return {"error": f"not editable here: {sorted(bad)}"}
    cue = services.require_project().edit_cue(id, fields)
    services.emit({"type": "cues", "changed": [id]})
    return {"ok": True, "id": id, "changed": sorted(fields), "prompt_final": services.project.compose_prompt(cue)}


def edit_style(services, fields):
    bad = set(fields) - STYLE_KEYS
    if bad:
        return {"error": f"only {sorted(STYLE_KEYS)} can be changed"}
    services.require_project().edit_project(fields)
    services.emit({"type": "project", "changed": sorted(fields)})
    return {"ok": True, "changed": sorted(fields)}


def add_cue(services, fields):
    cue = services.require_project().add_cue(fields)
    services.emit({"type": "cues", "changed": [cue["id"]]})
    return {"ok": True, "id": cue["id"]}


def estimate(services, items):
    tracks = services.queue.store.tracks(200)
    out = 0.0
    for cue, _, _ in items:
        kind = "music" if kind_of(cue["category"]) == "music" else "sfx"
        times = [t["seconds"] for t in tracks if (t["kind"] == "music") == (kind == "music")]
        out += sum(times) / len(times) if times else (240 if kind == "music" else 20)
    return round(out / 60, 1)


def queue_generate(services, cues, takes=None, seconds=None):
    project = services.require_project()
    items, locked = plan_takes(project, cues, takes)
    if not items:
        return {"queued": [], "note": "nothing to make: those takes exist" + (f"; locked: {locked}" if locked else "")}
    body = {"cues": cues, **({"takes": takes} if takes else {}), **({"seconds": seconds} if seconds else {})}
    job = services.queue.submit("generate", body)
    return {"queued": [job], "takes": [p.name for _, _, p in items], "estimated_minutes": estimate(services, items),
            **({"locked_skipped": locked} if locked else {})}


def queue_variations(services, candidate, kinds=None, semitones=None):
    job = services.queue.submit("variations", {"candidate": candidate, "kinds": kinds or ["reverse", "pitch"], "semitones": semitones or [-5, 5]})
    return {"queued": [job]}


def list_jobs(services, state=None):
    jobs = services.queue.store.list(state, 40)
    return {"jobs": [{"id": j["id"], "type": j["type"], "state": j["state"], "progress": round(j["progress"] or 0, 2),
                      "body": j["body"], **({"error": j["error"]} if j.get("error") else {})} for j in jobs], "paused": services.queue.paused}


def cancel_job(services, id):
    return {"ok": services.queue.cancel(int(id))}


def get_takes(services, cue):
    data = catalog.takes_for(services.require_project(), cue)
    slim = lambda items: [{k: v for k, v in t.items() if k in ("name", "seconds", "lufs", "tempo", "installed") and v is not None} for t in items if not t.get("rejected")]  # noqa: E731
    return {"cue": cue, "raw": slim(data["raw"]), "candidates": slim(data["candidates"]), "installed": data["installed"]}


def map_track(services, file):
    _, data = get_map(services, file)
    return map_mod.summary(data)


def cut_loop(services, take, min=30, max=45, include=None):
    return {"queued": [services.queue.submit("loop", {"take": take, "min": min, "max": max, **({"include": include} if include else {})})]}


def recompose(services, take, pieces, ring=None, overlay=None):
    body = {"take": take, "pieces": pieces, **({"ring": ring} if ring else {}), **({"overlay": overlay} if overlay else {})}
    return {"queued": [services.queue.submit("recompose", body)]}


def promote_tool(services, candidates, mode="add"):
    return {"installed": promote.promote(services.require_project(), candidates, mode, services.log, services.emit)}


def fill_gaps(services):
    return {"installed": promote.fill_gaps(services.require_project(), services.log, services.emit)}


def import_cues(services):
    return {"queued": [services.queue.submit("import", {})]}


FUNCTIONS = {
    "list_cues": list_cues, "get_cue": get_cue, "edit_cue": edit_cue, "edit_style": edit_style, "add_cue": add_cue,
    "queue_generate": queue_generate, "queue_variations": queue_variations, "list_jobs": list_jobs, "cancel_job": cancel_job,
    "get_takes": get_takes, "map_track": map_track, "cut_loop": cut_loop, "recompose": recompose, "promote": promote_tool,
    "fill_gaps": fill_gaps, "import_cues": import_cues,
}


def call(services, name, args) -> str:
    try:
        result = FUNCTIONS[name](services, **args)
    except (KeyError, ValueError, LookupError, FileNotFoundError) as error:
        result = {"error": f"{type(error).__name__}: {error}"}
    return json.dumps(result, ensure_ascii=False, default=str)
