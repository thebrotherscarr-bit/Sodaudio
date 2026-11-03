"""Install (ported from Rimtera's promote.py): copies kept candidates into the game's output folders as the cue's next
take. An existing file is never overwritten: your recordings always win. Every install is logged in promoted.tsv
(cue, installed path, candidate, time), so "replace generated" removes only files the tool itself installed."""
import csv
import time
from pathlib import Path

import soundfile

from .project import rel
from .util import cue_of

HEADER = ["cue", "file", "candidate", "time"]


def log_path(project) -> Path:
    return project.ws("promoted.tsv")


def generated_files(project) -> set:
    path = log_path(project)
    if not path.exists():
        return set()
    with path.open(encoding="utf-8") as handle:
        return {line.split("\t")[1] for line in handle.read().splitlines()[1:] if "\t" in line}


def next_free(project, cue_id, category) -> Path:
    folder = project.output_folder(category)
    taken = {p.stem for p in folder.glob("*")} if folder.exists() else set()
    n = 1
    while Path(project.output_name(cue_id, category, n)).stem in taken:
        n += 1
    return folder / project.output_name(cue_id, category, n)


def _write_log(project, row):
    path = log_path(project)
    new = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        if new:
            writer.writerow(HEADER)
        writer.writerow(row)


def install(project, candidate: Path, cue: dict) -> Path:
    target = next_free(project, cue["id"], cue["category"])
    target.parent.mkdir(parents=True, exist_ok=True)
    audio, rate = soundfile.read(str(candidate), always_2d=True)
    partial = target.with_name(target.stem + ".part" + target.suffix)
    if target.suffix.lower() == ".ogg":
        # In blocks: libsndfile 1.2.2 crashes writing a long Vorbis file in one call.
        with soundfile.SoundFile(str(partial), "w", rate, audio.shape[1], format="OGG", subtype="VORBIS") as handle:
            for start in range(0, len(audio), 8192):
                handle.write(audio[start:start + 8192])
    elif target.suffix.lower() == ".flac":
        soundfile.write(str(partial), audio, rate, format="FLAC")
    else:
        soundfile.write(str(partial), audio, rate, subtype="PCM_16")
    if target.exists():  # someone put a file there meanwhile: never overwrite it
        partial.unlink()
        raise FileExistsError(target)
    partial.rename(target)
    _write_log(project, [cue["id"], rel(target, project.root), candidate.name, time.strftime("%Y-%m-%d %H:%M:%S")])
    return target


def find_candidate(project, name: str):
    cue_id = cue_of(name)
    cue = project.cue(cue_id)
    path = project.candidate_dir(cue["category"]) / (name if name.endswith(".wav") else name + ".wav")
    return cue, path


def promote(project, names, mode="add", log=print, emit=None) -> list:
    """mode add: each candidate becomes its cue's next take. replace-generated: first removes the cue's earlier files
    that the tool installed (and only those)."""
    installed, cleared = [], set()
    generated = generated_files(project)
    for name in names:
        try:
            cue, candidate = find_candidate(project, name)
        except KeyError:
            log(f"skipped {name}: no such cue")
            continue
        if not candidate.exists():
            log(f"skipped {name}: no such candidate")
            continue
        if cue.get("locked"):
            log(f"skipped {name}: {cue['id']} is locked")
            continue
        if mode == "replace-generated" and cue["id"] not in cleared:
            for path in project.installed(cue["id"], cue["category"]):
                if rel(path, project.root) in generated:
                    path.unlink()
                    log(f"removed generated {rel(path, project.root)}")
            cleared.add(cue["id"])
        target = install(project, candidate, cue)
        log(f"{candidate.name} -> {rel(target, project.root)}")
        installed.append({"cue": cue["id"], "path": rel(target, project.root), "candidate": candidate.name})
        if emit:
            emit({"type": "install", "cue": cue["id"], "path": rel(target, project.root)})
    return installed


def fill_gaps(project, log=print, emit=None) -> list:
    """Gives every cue with no file its first candidates (up to its variants), so the game never falls back to silence."""
    names = []
    for cue in project.cues(include_missing=False):
        if cue.get("locked") or project.installed(cue["id"], cue["category"]):
            continue
        folder = project.candidate_dir(cue["category"])
        candidates = sorted(folder.glob(f"{cue['id']}__c*.wav")) if folder.exists() else []
        # Plain takes first (cue__c1.wav), before loops, mixes and variations made from them.
        plain = [c for c in candidates if c.stem.rsplit("__c", 1)[1].isdigit()]
        names += [c.name for c in plain[: int(cue.get("variants") or 1)]]
    return promote(project, names, "add", log, emit)
