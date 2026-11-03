"""The per-game project file (PROJECT-FORMAT.md): load and save, prompt and song plan composition, cue status and
the edit history behind undo."""
import copy
import fnmatch
import random
import re
import string
import threading
import time
from pathlib import Path

from .util import AUDIO_EXTS, read_json, seed, write_json

CUE_FIELDS = ["id", "category", "trigger", "variants", "seconds", "loop", "offset_db", "bpm", "key", "meter", "card",
              "voice", "context", "plan", "prompt", "locked", "notes"]
STYLE_FIELDS = ("style", "templates", "chant", "output", "levels", "importer", "name", "workspace")

NEW_PROJECT = {
    "format": 1,
    "name": "",
    "workspace": "audio-work",
    "output": {
        "sfx": {"folder": "Assets/Sounds", "format": "wav", "naming": "{cue}{_n}"},
        "music": {"folder": "Assets/Music", "format": "ogg", "naming": "{cue}{_n}"},
        "ambience": {"folder": "Assets/Ambience", "format": "ogg", "naming": "{cue}{_n}"},
    },
    "levels": {"lufs": -16, "peak_dbfs": -1},
    "style": {"house_sound": "", "music_suffix": "video game music, loopable", "choir": "", "sfx_suffix": "clean game sound effect, no music",
              "ambience_suffix": "seamless ambient loop"},
    "templates": {"music": "{card}. {house_sound}. video game {context} {music_suffix}", "sfx": "{card}. {sfx_suffix}",
                  "ambience": "{card}. {ambience_suffix}"},
    "chant": None,
    "importer": {"type": "manual"},
    "cues": [],
}


class Blank(dict):
    def __missing__(self, key):
        return ""


def kind_of(category: str) -> str:
    """Which generator and output folder a category uses: music, ambience or sfx."""
    return category if category in ("music", "ambience") else "sfx"


def new_cue(fields: dict) -> dict:
    cue = {"id": "", "category": "sfx", "trigger": "", "variants": 1, "seconds": 2.0, "loop": False, "offset_db": 0.0,
           "bpm": None, "key": "", "meter": None, "card": "", "voice": "", "context": "", "plan": None, "prompt": None,
           "locked": False, "notes": ""}
    cue.update(fields)
    if kind_of(cue["category"]) == "music" and cue.get("bpm") is None:
        cue["bpm"] = 120
    return cue


class Project:
    def __init__(self, path: Path, data: dict):
        self.path = Path(path).resolve()
        self.data = data
        self.lock = threading.RLock()

    # --- files ---------------------------------------------------------------------------------------------------
    @classmethod
    def open(cls, path, create=False, name=""):
        path = Path(path)
        if path.is_dir():
            path = path / "audio-project.json"
        if not path.exists():
            if not create:
                raise FileNotFoundError(f"no project file at {path}")
            data = copy.deepcopy(NEW_PROJECT)
            data["name"] = name or path.parent.name
            write_json(path, data)
        data = read_json(path)
        if not isinstance(data, dict) or "cues" not in data:
            raise ValueError(f"{path} is not an audio project file")
        project = cls(path, data)
        project.workspace.mkdir(parents=True, exist_ok=True)
        return project

    def save(self):
        with self.lock:
            write_json(self.path, self.data)

    @property
    def root(self) -> Path:
        """The game folder: output folders are relative to it. Normally the project file's own folder."""
        return (self.path.parent / self.data.get("root", ".")).resolve()

    @property
    def workspace(self) -> Path:
        return (self.path.parent / self.data.get("workspace", "audio-work")).resolve()

    def ws(self, *parts) -> Path:
        return self.workspace.joinpath(*parts)

    def summary(self) -> dict:
        info = {k: v for k, v in self.data.items() if k != "cues"}
        info.update(path=str(self.path), root=str(self.root), workspace_path=str(self.workspace),
                    cue_count=len([c for c in self.data["cues"] if not c.get("missing")]))
        return info

    # --- cues ----------------------------------------------------------------------------------------------------
    def cues(self, include_missing=True):
        return [c for c in self.data["cues"] if include_missing or not c.get("missing")]

    def cue(self, cue_id: str) -> dict:
        for cue in self.data["cues"]:
            if cue["id"] == cue_id:
                return cue
        raise KeyError(cue_id)

    def has(self, cue_id: str) -> bool:
        return any(c["id"] == cue_id for c in self.data["cues"])

    def add_cue(self, fields: dict) -> dict:
        with self.lock:
            if not fields.get("id") or self.has(fields["id"]):
                raise ValueError(f"cue id {fields.get('id')!r} is empty or taken")
            cue = new_cue({k: v for k, v in fields.items() if k in CUE_FIELDS})
            cue["manual"] = True  # the importer never marks a cue added here as missing
            self.data["cues"].append(cue)
            self._record("add", cue["id"], {}, {"id": cue["id"]})
            self.save()
            return cue

    def edit_cue(self, cue_id: str, fields: dict) -> dict:
        with self.lock:
            cue = self.cue(cue_id)
            fields = {k: v for k, v in fields.items() if k in CUE_FIELDS and k != "id"}
            before = {k: copy.deepcopy(cue.get(k)) for k in fields}
            cue.update(fields)
            cue.setdefault("edited", [])
            cue["edited"] = sorted(set(cue["edited"]) | set(fields))
            self._record("cue", cue_id, before, fields)
            self.save()
            return cue

    def edit_project(self, fields: dict) -> dict:
        with self.lock:
            fields = {k: v for k, v in fields.items() if k in STYLE_FIELDS}
            before = {k: copy.deepcopy(self.data.get(k)) for k in fields}
            self.data.update(fields)
            self._record("project", None, before, fields)
            self.save()
            return self.summary()

    # --- history -------------------------------------------------------------------------------------------------
    def _history(self):
        return read_json(self.ws("history.json"), []) or []

    def _record(self, kind, cue_id, before, after):
        history = self._history()
        history.append({"t": time.time(), "kind": kind, "cue": cue_id, "before": before, "after": copy.deepcopy(after)})
        write_json(self.ws("history.json"), history[-500:])

    def history(self, limit=50):
        return list(reversed(self._history()))[:limit]

    def undo(self) -> dict | None:
        with self.lock:
            history = self._history()
            if not history:
                return None
            entry = history.pop()
            if entry["kind"] == "cue":
                self.cue(entry["cue"]).update(entry["before"])
            elif entry["kind"] == "project":
                self.data.update(entry["before"])
            elif entry["kind"] == "add":
                self.data["cues"] = [c for c in self.data["cues"] if c["id"] != entry["cue"]]
            write_json(self.ws("history.json"), history)
            self.save()
            return entry

    # --- composition ---------------------------------------------------------------------------------------------
    def compose_prompt(self, cue: dict) -> str:
        """PROJECT-FORMAT.md, "How a prompt is built": the cue's own prompt, or its category's template."""
        if cue.get("prompt"):
            return cue["prompt"]
        kind = kind_of(cue["category"])
        templates = self.data.get("templates", {})
        template = templates.get(cue["category"]) or templates.get(kind) or "{card}"
        if kind == "music" and not cue.get("voice") and templates.get("music_instrumental"):
            template = templates["music_instrumental"]
        values = Blank({k: v for k, v in (self.data.get("style") or {}).items() if isinstance(v, str)})
        values.update({k: cue.get(k) or "" for k in ("card", "voice", "context", "trigger", "id")})
        return template.format_map(values)

    def shape_for(self, cue: dict) -> str:
        chant = self.data.get("chant") or {}
        bpm = int(cue.get("bpm") or 0)
        for rule in chant.get("shape_rules", []):
            match = rule.get("match")
            if match is not None:
                patterns = [match] if isinstance(match, str) else match
                if not any(fnmatch.fnmatchcase(cue["id"], p) for p in patterns):
                    continue
            if "bpm_at_least" in rule and not bpm >= rule["bpm_at_least"]:
                continue
            if "bpm_at_most" in rule and not bpm <= rule["bpm_at_most"]:
                continue
            return rule["shape"]
        return next(iter(chant.get("shapes", {"drive": []})))

    def compose_plan(self, cue: dict) -> str | None:
        """The lyrics field for a music cue: its own plan, or a shape with the chant lines shuffled for this cue."""
        if kind_of(cue["category"]) != "music":
            return None
        if cue.get("plan"):
            return cue["plan"]
        chant = self.data.get("chant")
        if not chant or not chant.get("lines") or not chant.get("shapes"):
            return "[Instrumental]"
        shape = self.shape_for(cue)
        lines = chant["lines"]
        lines = random.Random(seed(cue["id"], 0)).sample(lines, len(lines))
        sections = []
        for index, tag in enumerate(chant["shapes"][shape]):
            sung = [lines[(index * 2 + n) % len(lines)] for n in range(2)]
            if tag.startswith("[Chorus") and shape != "calm" and chant.get("uppercase_choruses", True):
                sung = [line.upper() for line in sung]
            sections.append("\n".join([tag, *sung]))
        return "\n\n".join(sections)

    def decompose(self) -> list:
        """Splits imported prompts into card, voice and context where they match the templates; clears `prompt`."""
        changed = []
        style = {k: v for k, v in (self.data.get("style") or {}).items() if isinstance(v, str)}
        with self.lock:
            for cue in self.data["cues"]:
                if not cue.get("prompt"):
                    continue
                kind = kind_of(cue["category"])
                template = (self.data.get("templates") or {}).get(cue["category"]) or (self.data.get("templates") or {}).get(kind)
                if not template:
                    continue
                pattern, names = "", []
                for literal, field, _, _ in string.Formatter().parse(template):
                    pattern += re.escape(literal)
                    if field is None:
                        continue
                    if field in ("card", "voice", "context") and field not in names:
                        names.append(field)
                        pattern += f"(?P<{field}>.+?)"
                    elif field in style:
                        pattern += re.escape(style[field])
                    else:
                        pattern += ".*?"
                found = re.fullmatch(pattern, cue["prompt"], re.S)
                if not found:
                    continue
                fields = {name: found.group(name) for name in names}
                fields["prompt"] = None
                before = {k: cue.get(k) for k in fields}
                cue.update(fields)
                cue["edited"] = sorted(set(cue.get("edited", [])) | set(fields))
                self._record("cue", cue["id"], before, fields)
                changed.append(cue["id"])
            self.save()
        return changed

    # --- where files are -----------------------------------------------------------------------------------------
    def output(self, category: str) -> dict:
        out = self.data.get("output", {})
        spec = out.get(category) or out.get(kind_of(category)) or NEW_PROJECT["output"][kind_of(category)]
        return {"folder": spec.get("folder", "."), "format": spec.get("format", "wav"), "naming": spec.get("naming", "{cue}{_n}")}

    def output_folder(self, category: str) -> Path:
        return (self.root / self.output(category)["folder"]).resolve()

    def output_name(self, cue_id: str, category: str, n: int) -> str:
        spec = self.output(category)
        return spec["naming"].format_map(Blank(cue=cue_id, _n="" if n == 1 else f"_{n}", n=n)) + "." + spec["format"]

    def levels(self, cue: dict):
        levels = self.data.get("levels", {})
        return float(levels.get("lufs", -16)) + float(cue.get("offset_db") or 0), float(levels.get("peak_dbfs", -1))

    def raw_dir(self, category):
        return self.ws("raw", category)

    def candidate_dir(self, category):
        return self.ws("candidates", category)

    def installed(self, cue_id: str, category: str, listing=None) -> list:
        """Files the game has for the cue (cue.ext, cue_2.ext …): recordings and installed takes alike."""
        folder = self.output_folder(category)
        names = listing if listing is not None else (list_dir(folder))
        found = []
        for name in names:
            stem, ext = name.rsplit(".", 1) if "." in name else (name, "")
            if "." + ext.lower() not in AUDIO_EXTS:
                continue
            if stem == cue_id or (stem.startswith(cue_id + "_") and stem[len(cue_id) + 1:].isdigit()):
                found.append(folder / name)
        return sorted(found)

    def status(self, cues=None) -> list:
        """Cues with installed files, take and candidate counts, from one listing per folder."""
        cues = self.data["cues"] if cues is None else cues
        listings, rejected = {}, set(read_json(self.ws("rejected.json"), []) or [])

        def listing(folder):
            if folder not in listings:
                listings[folder] = list_dir(folder)
            return listings[folder]

        result = []
        for cue in cues:
            cat = cue["category"]
            installed = self.installed(cue["id"], cat, listing(self.output_folder(cat)))
            prefix = cue["id"] + "__c"
            takes = [n for n in listing(self.raw_dir(cat)) if n.startswith(prefix) and ".part" not in n]
            cands = [n for n in listing(self.candidate_dir(cat)) if n.startswith(prefix) and n.endswith(".wav") and n not in rejected]
            result.append({**cue, "kind": kind_of(cat), "installed": [rel(p, self.root) for p in installed],
                           "takes": len(takes), "candidates": len(cands)})
        return result


def list_dir(folder: Path) -> list:
    try:
        return [p.name for p in Path(folder).iterdir() if p.is_file()]
    except (FileNotFoundError, NotADirectoryError):
        return []


def rel(path: Path, base: Path) -> str:
    try:
        return Path(path).resolve().relative_to(base).as_posix()
    except ValueError:
        return str(path)
