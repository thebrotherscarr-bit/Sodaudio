"""Post-processing, the map, loop cuts and recompose on synthetic audio with a known tempo."""
import numpy as np
import pytest
import soundfile

from engine import post
from engine.analysis import loop, map as map_mod, recompose

from .conftest import click_track


def test_post_levels_to_target(tmp_path):
    rate = 48000
    t = np.arange(rate * 3) / rate
    soundfile.write(str(tmp_path / "in.wav"), (np.sin(2 * np.pi * 440 * t) * 0.05).astype(np.float32), rate)
    info = post.process(tmp_path / "in.wav", tmp_path / "out.wav", "sfx", False, -16, -1)
    audio, out_rate = soundfile.read(str(tmp_path / "out.wav"))
    assert out_rate == 44100
    assert abs(post.loudness(audio[:, None], out_rate) + 16) < 0.5
    assert np.abs(audio).max() <= 10 ** (-1 / 20) + 1e-3
    assert info["seconds"] == pytest.approx(3, abs=0.1)


def test_post_trims_effects_and_crossfades_loops(tmp_path):
    rate = 44100
    burst = np.concatenate([np.zeros(rate), np.random.default_rng(1).normal(0, 0.2, rate // 2), np.zeros(rate)])
    soundfile.write(str(tmp_path / "fx.wav"), burst.astype(np.float32), rate)
    post.process(tmp_path / "fx.wav", tmp_path / "fx_out.wav", "sfx", False)
    assert soundfile.info(str(tmp_path / "fx_out.wav")).duration < 0.7
    click_track(tmp_path / "m.wav", 120, 20)
    post.process(tmp_path / "m.wav", tmp_path / "m_out.wav", "music", True)
    assert soundfile.info(str(tmp_path / "m_out.wav")).duration == pytest.approx(17, abs=0.05)  # 3 s folded into the head


def test_map_measures_the_tempo(tmp_path):
    click_track(tmp_path / "c.wav", 100, 30)
    data = map_mod.compute(tmp_path / "c.wav", bpm=100)
    assert data["tempo"] == pytest.approx(100, abs=1.5)
    assert len(data["bars"]) >= 10
    assert np.diff(data["bars"]).mean() == pytest.approx(2.4, abs=0.05)  # 4 beats at 100 BPM
    assert len(data["loudness"]) == pytest.approx(30 / data["hop_s"], rel=0.05)
    assert len(data["mel"]["frames"][0]) == 64 and len(data["chroma"][0]) == 12
    summary = map_mod.summary(data)
    assert summary["bar_count"] == len(data["bars"]) and "quiet_bars" in summary


def test_loop_is_whole_bars_and_avoids_quiet(tmp_path):
    click_track(tmp_path / "c.wav", 120, 60, quiet=(30, 34))
    data = map_mod.compute(tmp_path / "c.wav", bpm=120)
    info = loop.find_loop(tmp_path / "c.wav", tmp_path / "loop.wav", data["tempo"], 10, 14, bars=data["bars"])
    assert info["bars"] in (5, 6, 7)
    assert info["seconds"] == pytest.approx(info["bars"] * 2.0, abs=0.1)
    assert info["start"] + info["seconds"] <= 30 or info["start"] >= 34
    assert soundfile.info(str(tmp_path / "loop.wav")).duration == pytest.approx(info["seconds"], abs=0.01)


def test_loop_include_keeps_the_moment(tmp_path):
    click_track(tmp_path / "c.wav", 120, 60)
    info = loop.find_loop(tmp_path / "c.wav", tmp_path / "loop.wav", 120, 10, 14, include=[40, 45])
    assert info["start"] <= 40 and info["start"] + info["seconds"] >= 45


def test_recompose_length_and_gain(tmp_path):
    click_track(tmp_path / "c.wav", 120, 40)
    pieces = [{"from": 4, "to": 8, "gain_db": 6}, {"from": 20, "to": 28}]
    info = recompose.recompose(tmp_path / "c.wav", tmp_path / "mix.wav", pieces, fade=0.125, ring=29)
    assert info["seconds"] == pytest.approx(12, abs=0.01)
    assert soundfile.info(str(tmp_path / "mix.wav")).duration == pytest.approx(12, abs=0.01)


def test_beat_grid_reaches_both_ends():
    beats, bars, phase = map_mod.extend_beats([10.0, 11.0, 12.0, 13.0, 14.0], 20.0, 4, 1)
    assert beats[0] == pytest.approx(0.0) and beats[-1] == pytest.approx(19.0)
    assert bars[0] == pytest.approx(3.0) and phase == 3  # the downbeat at 11 s, carried back to 3 s
