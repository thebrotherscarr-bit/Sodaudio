"""What exists for a cue: raw takes, candidates (with length, loudness and measured tempo) and what is installed."""
from pathlib import Path

import soundfile

from . import post
from .project import rel
from .promote import generated_files, log_path
from .util import read_json, take_number, write_json


def _loudness(project, path: Path, compute=True):
    """Integrated loudness, cached by name and modification time (a 150 s take takes about half a second)."""
    cache_path = project.ws("loudness.json")
    cache = read_json(cache_path, {}) or {}
    key = f"{path.name}|{path.stat().st_mtime_ns}"
    if key not in cache:
        if not compute:
            return None
        audio, rate = soundfile.read(str(path), always_2d=True, dtype="float64")
        cache[key] = round(post.loudness(audio, rate), 1)
        write_json(cache_path, cache)
    return cache[key]


def _tempo(project, path: Path):
    from .pipeline import map_key
    data = read_json(project.ws("maps", map_key(path) + ".json"))
    return data["tempo"] if data else None


def installed_candidates(project) -> dict:
    """candidate name -> installed path, from promoted.tsv."""
    path = log_path(project)
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("\t")
        if len(parts) >= 3:
            result[parts[2]] = parts[1]
    return result


def takes_for(project, cue_id: str, loudness=True) -> dict:
    cue = project.cue(cue_id)
    raw_dir, cand_dir = project.raw_dir(cue["category"]), project.candidate_dir(cue["category"])
    rejected = set(read_json(project.ws("rejected.json"), []) or [])
    installs = installed_candidates(project)
    generated = generated_files(project)

    def info(path: Path, kind):
        try:
            meta = soundfile.info(str(path))
            seconds = round(meta.frames / meta.samplerate, 2)
        except RuntimeError:
            seconds = None
        return {"name": path.name, "kind": kind, "path": rel(path, project.workspace), "take": take_number(path.name),
                "seconds": seconds, "lufs": _loudness(project, path, loudness) if kind == "candidate" else None,
                "tempo": _tempo(project, path), "installed": installs.get(path.name), "rejected": path.name in rejected,
                "modified": path.stat().st_mtime}

    prefix = cue_id + "__c"
    raws = sorted(p for p in raw_dir.glob(prefix + "*") if ".part" not in p.name) if raw_dir.exists() else []
    cands = sorted(p for p in cand_dir.glob(prefix + "*.wav") if ".part" not in p.name) if cand_dir.exists() else []
    installed = [{"path": rel(p, project.root), "generated": rel(p, project.root) in generated,
                  "seconds": _seconds(p)} for p in project.installed(cue_id, cue["category"])]
    return {"cue": cue_id, "raw": [info(p, "raw") for p in raws], "candidates": [info(p, "candidate") for p in cands],
            "installed": installed}


def _seconds(path):
    try:
        meta = soundfile.info(str(path))
        return round(meta.frames / meta.samplerate, 2)
    except RuntimeError:
        return None
