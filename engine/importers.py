"""Importers fill a project's cue list from the game (PROJECT-FORMAT.md, "Importers"). They merge by id: new cues are
added, cues the game no longer has are marked missing (never deleted), and fields edited in the tool are kept."""
import csv
import io
import json
import re
import shlex
import subprocess
from pathlib import Path

from .project import CUE_FIELDS, kind_of, new_cue
from .util import AUDIO_EXTS

TSV_COLUMNS = ["cue", "category", "trigger", "variants", "seconds", "loop", "offset", "lane", "fallback", "bpm", "key", "meter", "prompt"]


def _num(text, kind=float, empty=None):
    text = (text or "").strip()
    if not text:
        return empty
    value = kind(float(text))
    return value


def parse_rimtera_tsv(text: str) -> list:
    """The columns of Rimtera's audio_needs.tsv (dotnet run -- --sound-cues)."""
    rows = list(csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE))
    cues = []
    for row in rows:
        if not row.get("cue"):
            continue
        bpm, meter = _num(row.get("bpm"), int, 0), _num(row.get("meter"), int, 0)
        cues.append({
            "id": row["cue"], "category": row["category"], "trigger": row.get("trigger", ""),
            "variants": _num(row.get("variants"), int, 1), "seconds": _num(row.get("seconds"), float, 2.0),
            "loop": (row.get("loop") or "").strip().lower() in ("yes", "true", "1"),
            "offset_db": _num(row.get("offset"), float, 0.0),
            "bpm": bpm or None, "key": row.get("key", "") or "", "meter": meter or None,
            "prompt": row.get("prompt") or None, "lane": row.get("lane", ""), "fallback": row.get("fallback", ""),
        })
    return cues


def parse_csv(text: str) -> list:
    cues = []
    for row in csv.DictReader(io.StringIO(text)):
        cue = {k: v for k, v in row.items() if k and v not in (None, "")}
        for key, kind in (("variants", int), ("seconds", float), ("offset_db", float), ("bpm", int), ("meter", int)):
            if key in cue:
                cue[key] = _num(cue[key], kind)
        if "loop" in cue:
            cue["loop"] = str(cue["loop"]).lower() in ("yes", "true", "1")
        cues.append(cue)
    return cues


def parse_json(text: str) -> list:
    data = json.loads(text)
    return data["cues"] if isinstance(data, dict) else data


PARSERS = {"rimtera-tsv": parse_rimtera_tsv, "csv": parse_csv, "json": parse_json}


def folder_scan(project) -> list:
    """A cue for each audio file in the output folders, so a game with no cue list can still get new takes."""
    cues, seen = [], set()
    for key in (project.data.get("output") or {}):
        folder = project.output_folder(key)
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in AUDIO_EXTS:
                continue
            cue_id = re.sub(r"_\d+$", "", path.stem)
            if cue_id in seen:
                continue
            seen.add(cue_id)
            variants = len([p for p in folder.glob(cue_id + "*") if re.fullmatch(re.escape(cue_id) + r"(_\d+)?", p.stem)])
            cues.append({"id": cue_id, "category": key if key in ("music", "ambience") else ("sfx" if key == "sfx" else key),
                         "variants": max(1, variants), "loop": kind_of(key) != "sfx", "seconds": 150.0 if key == "music" else 2.0,
                         "trigger": f"found in {project.output(key)['folder']}"})
    return cues


def run_importer(project, run_command=True, log=print) -> list:
    spec = project.data.get("importer") or {"type": "manual"}
    kind = spec.get("type", "manual")
    if kind == "manual":
        return []
    if kind == "folder-scan":
        return folder_scan(project)
    if kind != "command":
        raise ValueError(f"unknown importer type {kind!r}")
    text = None
    if run_command and spec.get("command"):
        log(f"running {spec['command']} in {project.root}")
        try:
            result = subprocess.run(shlex.split(spec["command"], posix=False), cwd=project.root, capture_output=True, timeout=600)
            out = result.stdout.decode("utf-8", "replace")
            if result.returncode == 0 and out.strip() and not spec.get("output_is_file"):
                text = out
            elif result.returncode != 0:
                log(f"the importer command failed ({result.returncode}); using the existing output file")
                for line in result.stderr.decode("utf-8", "replace").splitlines()[-10:]:
                    log(line)
        except (OSError, subprocess.TimeoutExpired) as error:
            log(f"could not run the importer command ({error}); using the existing output file")
    if text is None:
        output = spec.get("output")
        if not output:
            raise ValueError("the importer has no output file to read")
        text = (project.root / output).read_text(encoding="utf-8")
    return PARSERS[spec.get("format", "rimtera-tsv")](text)


def merge(project, incoming: list) -> dict:
    """Merges by id. Fields the importer supplies are updated unless they were edited in the tool."""
    added, changed, missing = [], [], []
    with project.lock:
        existing = {c["id"]: c for c in project.data["cues"]}
        ids = set()
        for fields in incoming:
            ids.add(fields["id"])
            cue = existing.get(fields["id"])
            if cue is None:
                cue = new_cue({k: v for k, v in fields.items()})
                project.data["cues"].append(cue)
                added.append(cue["id"])
                continue
            edited = set(cue.get("edited", []))
            update = {k: v for k, v in fields.items() if k not in edited and cue.get(k) != v}
            if cue.get("missing"):
                update["missing"] = False
            if update:
                cue.update(update)
                changed.append(cue["id"])
            # A cue the importer creates may lack fields a new cue has (for example after a hand-written stub).
            for key, value in new_cue({}).items():
                cue.setdefault(key, value)
        for cue in project.data["cues"]:
            if cue["id"] not in ids and not cue.get("missing") and incoming:
                if cue.get("manual"):
                    continue
                cue["missing"] = True
                missing.append(cue["id"])
        project.save()
    return {"added": added, "changed": changed, "missing": missing}


__all__ = ["run_importer", "merge", "parse_rimtera_tsv", "folder_scan", "CUE_FIELDS"]
