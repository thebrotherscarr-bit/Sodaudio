"""Composing Rimtera's prompts and chant plans gives the same text Rimtera's gen_music.py and SoundCatalog.cs give."""
import random

import pytest

from engine.util import seed

from .conftest import RIMTERA_TSV

pytestmark = pytest.mark.skipif(not RIMTERA_TSV.exists(), reason="needs the Rimtera checkout for its cue list")

# Copied verbatim from Rimtera/tools/audio/gen_music.py (plan(), SHAPES, LINES) as the reference.
SHAPES = {
    "calm": ["[Intro - low monk drone alone, slow and wide]", "[Verse - the music enters, monks hum low underneath]",
             "[Chorus - the choir swells, slow held chant]", "[Interlude - the melody leads, the drone holds]",
             "[Chorus - the chant returns, fuller]", "[Outro - the drone holds and leads back to the start]"],
    "drive": ["[Intro - low monk drone, the beat builds in]", "[Verse - driving groove, the low drone holds underneath]",
              "[Chorus - full choir, slow half-time chant over the beat]", "[Bridge - leads race, monks drone low]",
              "[Build - drums rise, the choir grows deeper]", "[Chorus - full band and full choir]"],
    "fight": ["[Intro - low monk drone alone, ominous, war drums]", "[Verse - the fight starts, the low drone holds underneath]",
              "[Chorus - full choir, slow half-time chant over the driving fight]", "[Bridge - strings and synth leads race, monks drone low]",
              "[Build - drums double, the choir grows deeper]", "[Chorus - overwhelming, full choir and full band]"],
}
LINES = ["Riiim... teee... raaa...", "Riiim-terr-raaa...", "Riiim-ta-raaa...", "Raaa... te... riiim...",
         "Ohhh... riiim-te-raaa...", "Mmmmm... riiim...", "Riiim-te-raaa... riiim-ta-raaa...", "Mmmmm... ohhh..."]


def reference_plan(cue, bpm):
    shape = ("fight" if cue.startswith("mus_boss_") or bpm >= 144
             else "calm" if cue.endswith(("_night", "_underground")) or bpm <= 110 else "drive")
    lines = random.Random(seed(cue, 0)).sample(LINES, len(LINES))
    sections = []
    for index, tag in enumerate(SHAPES[shape]):
        sung = [lines[(index * 2 + n) % len(lines)] for n in range(2)]
        if tag.startswith("[Chorus") and shape != "calm":
            sung = [line.upper() for line in sung]
        sections.append("\n".join([tag, *sung]))
    return "\n\n".join(sections)


def tsv_prompts():
    import csv
    with RIMTERA_TSV.open(encoding="utf-8", newline="") as handle:
        return {row["cue"]: row for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE)}


def test_import_lists_every_rimtera_cue(project):
    rows = tsv_prompts()
    ids = {c["id"] for c in project.cues(include_missing=False)}
    assert ids == set(rows)
    assert len(ids) == len(rows) > 500


def test_final_prompt_matches_the_games_exporter(project):
    rows = tsv_prompts()
    for cue_id in ("mus_frozen_day", "mus_title", "mus_boss_final", "use_broadsword", "amb_grassland_day"):
        assert project.compose_prompt(project.cue(cue_id)) == rows[cue_id]["prompt"]


def test_decompose_round_trips_every_prompt(project):
    rows = tsv_prompts()
    changed = project.decompose()
    assert "mus_frozen_day" in changed and "use_broadsword" in changed and "amb_grassland_day" in changed
    assert "mus_title" not in changed  # the title has its own wording; it keeps its prompt
    frozen = project.cue("mus_frozen_day")
    assert frozen["prompt"] is None
    assert frozen["card"] == "crystalline baroque counterpoint, harpsichord and bells, cold airy shimmer, half-time beat"
    assert frozen["voice"] == "cold hollow monks, breathy held vowels, icy reverb"
    assert frozen["context"] == "frozen biome music"
    for cue in project.cues():
        assert project.compose_prompt(cue) == rows[cue["id"]]["prompt"], cue["id"]


def test_reimport_keeps_decomposed_fields(project):
    from engine.importers import merge, run_importer
    project.decompose()
    merge(project, run_importer(project, run_command=False, log=lambda *_: None))
    assert project.cue("mus_frozen_day")["prompt"] is None
    assert project.cue("mus_frozen_day")["card"].startswith("crystalline")


@pytest.mark.parametrize("cue_id", ["mus_frozen_day", "mus_frozen_night", "mus_boss_easy", "mus_molten", "mus_space", "mus_event_cryo_brigade"])
def test_chant_plan_matches_gen_music(project, cue_id):
    cue = project.cue(cue_id)
    assert project.compose_plan(cue) == reference_plan(cue_id, int(cue["bpm"]))


def test_every_music_plan_matches_gen_music(project):
    for cue in project.cues():
        if cue["category"] != "music" or cue["id"] in ("mus_title", "mus_boss_final"):
            continue
        assert project.compose_plan(cue) == reference_plan(cue["id"], int(cue["bpm"])), cue["id"]


def test_hand_written_plans_are_kept(project):
    assert project.compose_plan(project.cue("mus_title")).startswith("[Intro - lone pan flute over harpsichord")
    assert "RIIIM... TEEE... RAAA..." in project.compose_plan(project.cue("mus_boss_final"))
    assert project.compose_plan(project.cue("use_broadsword")) is None


def test_edit_and_undo(project):
    project.edit_cue("mus_frozen_day", {"notes": "too busy"})
    assert project.cue("mus_frozen_day")["notes"] == "too busy"
    project.undo()
    assert project.cue("mus_frozen_day")["notes"] == ""
