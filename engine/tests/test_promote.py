"""Installing: never overwrite, next free name, OGG for music, replace only generated files, fill gaps, and importers."""
import json

import soundfile

from engine import promote
from engine.importers import folder_scan, merge, parse_csv

from .conftest import wait_idle


def make_candidates(services, cue, takes=2):
    services.queue.submit("generate", {"cues": [cue], "takes": takes, "seconds": 6})
    wait_idle(services)


def test_install_never_overwrites_a_recording(services):
    project = services.project
    recording = project.root / "Assets" / "Sounds" / "use_broadsword.wav"
    soundfile.write(str(recording), [0.1] * 4410, 44100)
    before = recording.read_bytes()
    make_candidates(services, "use_broadsword")
    installed = promote.promote(project, ["use_broadsword__c1.wav"])
    assert installed[0]["path"] == "Assets/Sounds/use_broadsword_2.wav"
    assert recording.read_bytes() == before
    log = project.ws("promoted.tsv").read_text(encoding="utf-8").splitlines()
    assert log[0] == "cue\tfile\tcandidate\ttime" and "use_broadsword_2.wav\tuse_broadsword__c1.wav" in log[1]


def test_music_installs_as_ogg_and_replace_generated(services):
    project = services.project
    make_candidates(services, "mus_forest_day")
    promote.promote(project, ["mus_forest_day__c1.wav"])
    path = project.root / "Assets" / "Music" / "mus_forest_day.ogg"
    assert soundfile.info(str(path)).format == "OGG"
    recording = project.root / "Assets" / "Music" / "mus_forest_day_2.ogg"
    recording.write_bytes(path.read_bytes())  # stands in for the user's own file, not in promoted.tsv
    promote.promote(project, ["mus_forest_day__c2.wav"], "replace-generated")
    names = sorted(p.name for p in (project.root / "Assets" / "Music").glob("mus_forest_day*"))
    assert names == ["mus_forest_day.ogg", "mus_forest_day_2.ogg"]  # c1's file removed, recording kept, c2 took slot 1
    assert recording.exists()


def test_locked_cues_are_never_replaced(services):
    services.project.edit_cue("use_shortsword", {"locked": True})
    (services.project.candidate_dir("weapons")).mkdir(parents=True, exist_ok=True)
    soundfile.write(str(services.project.candidate_dir("weapons") / "use_shortsword__c1.wav"), [0.1] * 4410, 44100)
    assert promote.promote(services.project, ["use_shortsword__c1.wav"]) == []


def test_fill_gaps_gives_first_takes(services):
    make_candidates(services, "use_broadsword", 3)  # wants 4 variants; 3 exist
    soundfile.write(str(services.project.root / "Assets" / "Sounds" / "use_shortsword.wav"), [0.1] * 4410, 44100)
    installed = promote.fill_gaps(services.project)
    assert [i["candidate"] for i in installed] == ["use_broadsword__c1.wav", "use_broadsword__c2.wav", "use_broadsword__c3.wav"]


def test_merge_keeps_edits_and_marks_missing(project):
    project.edit_cue("use_broadsword", {"trigger": "my words"})
    result = merge(project, [{"id": "use_broadsword", "category": "weapons", "trigger": "theirs", "variants": 9},
                             {"id": "brand_new", "category": "ui"}])
    cue = project.cue("use_broadsword")
    assert cue["trigger"] == "my words" and cue["variants"] == 9
    assert result["added"] == ["brand_new"]
    assert "use_shortsword" in result["missing"] and project.cue("use_shortsword")["missing"]
    assert project.has("use_shortsword")  # marked, never deleted


def test_folder_scan_and_csv(game, tmp_path):
    from engine.project import Project
    for name in ("door_open.wav", "door_open_2.wav", "mus_town.ogg"):
        soundfile.write(str(game / ("Assets/Sounds" if name.endswith(".wav") else "Assets/Music") / name), [0.0] * 100, 44100)
    proj = Project.open(game / "audio-project.json")
    cues = {c["id"]: c for c in folder_scan(proj)}
    assert cues["door_open"]["variants"] == 2 and cues["mus_town"]["category"] == "music"
    rows = parse_csv("id,category,seconds,loop\nzap,ui,0.5,yes\n")
    assert rows == [{"id": "zap", "category": "ui", "seconds": 0.5, "loop": True}]
