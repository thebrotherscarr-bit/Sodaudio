"""Small helpers shared by the engine modules."""
import hashlib
import json
import os
import time
from pathlib import Path

AUDIO_EXTS = (".wav", ".flac", ".ogg", ".mp3")


def seed(cue: str, take: int) -> int:
    """Take n of a cue uses this seed, as in Rimtera, so a take can be reproduced."""
    return int(hashlib.sha1(f"{cue}#{take}".encode()).hexdigest()[:8], 16)


def now() -> float:
    return time.time()


def write_json(path: Path, data) -> None:
    """Writes through a temporary file, so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    partial.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(partial, path)


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def take_name(cue: str, take: int) -> str:
    return f"{cue}__c{take}"


def cue_of(name: str) -> str:
    """The cue a take or candidate belongs to: mus_title__c8loop -> mus_title."""
    return Path(name).stem.rsplit("__c", 1)[0]


def take_number(name: str):
    tail = Path(name).stem.rsplit("__c", 1)
    if len(tail) < 2:
        return None
    digits = ""
    for ch in tail[1]:
        if not ch.isdigit():
            break
        digits += ch
    return int(digits) if digits else None
