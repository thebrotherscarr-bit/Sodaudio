"""Post-processing: turns a generated take into a game-ready candidate WAV (ported from Rimtera's audio_post.py).

DC removed; effects trimmed of leading and trailing silence with short fades and made mono; loops get their tail
crossfaded into the head; 44.1 kHz 16-bit; levelled to the project's LUFS plus the cue's offset, peaks under the
ceiling. Raw takes are never edited: the candidate is a new file."""
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile
from scipy.signal import resample_poly

RATE = 44100


def load(path, rate=RATE, channels=2):
    """Reads any take as float64 frames x channels at `rate`, DC removed."""
    audio, source_rate = soundfile.read(str(path), always_2d=True, dtype="float64")
    audio = audio[:, :channels]
    if source_rate != rate:
        audio = resample_poly(audio, rate, source_rate, axis=0)
    return audio - audio.mean(axis=0)


def trim(audio, rate, floor_db=-50.0):
    level = np.abs(audio).max(axis=1) if audio.ndim > 1 else np.abs(audio)
    loud = np.nonzero(level > 10 ** (floor_db / 20) * max(level.max(), 1e-9))[0]
    if len(loud) == 0:
        return audio
    start = max(0, loud[0] - int(0.005 * rate))
    end = min(len(audio), loud[-1] + int(0.05 * rate))
    return audio[start:end]


def fade(audio, rate, fade_in=0.003, fade_out=0.02):
    n_in, n_out = min(len(audio), int(fade_in * rate)), min(len(audio), int(fade_out * rate))
    ramp = lambda n: np.linspace(0, 1, n)[:, None] if audio.ndim > 1 else np.linspace(0, 1, n)  # noqa: E731
    if n_in:
        audio[:n_in] *= ramp(n_in)
    if n_out:
        audio[-n_out:] *= ramp(n_out)[::-1]
    return audio


def make_loop(audio, rate, seconds):
    """Crossfades the last `seconds` into the first: the result ends exactly where it began."""
    n = min(int(seconds * rate), len(audio) // 3)
    if n < 2:
        return audio
    curve = np.sqrt(np.linspace(0, 1, n))
    if audio.ndim > 1:
        curve = curve[:, None]
    head, tail = audio[:n], audio[-n:]
    looped = audio[:-n].copy()
    looped[:n] = head * curve + tail * curve[::-1]
    return looped


def loudness(audio, rate) -> float:
    meter = pyloudnorm.Meter(rate)
    padded = audio if len(audio) >= rate // 2 else np.concatenate([audio, np.zeros((rate // 2 - len(audio),) + audio.shape[1:])])
    try:
        value = meter.integrated_loudness(padded)
    except ValueError:
        return -70.0
    return float(value) if np.isfinite(value) else -70.0


def level(audio, rate, target=-16.0, peak_dbfs=-1.0):
    ceiling = 10 ** (peak_dbfs / 20)
    measured = loudness(audio, rate)
    if measured > -70:
        audio = audio * 10 ** ((target - measured) / 20)
    peak = np.abs(audio).max() if len(audio) else 0
    if peak > ceiling:  # soft knee: keep the body, round off the peaks
        knee = ceiling * 0.6
        over = np.abs(audio) > knee
        audio[over] = np.sign(audio[over]) * (knee + (ceiling - knee) * np.tanh((np.abs(audio[over]) - knee) / (ceiling - knee)))
    return audio


def write(path, audio, rate=RATE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.stem + ".part.wav")
    soundfile.write(str(partial), audio.astype(np.float32), rate, subtype="PCM_16")
    partial.replace(path)


def process(source, out, kind: str, loop: bool, target=-16.0, peak_dbfs=-1.0):
    """kind is music, ambience or sfx (project.kind_of). Returns the candidate's length and loudness."""
    audio, rate = soundfile.read(str(source), always_2d=True, dtype="float64")
    stereo = kind in ("music", "ambience") and audio.shape[1] > 1
    audio = audio[:, :2] if stereo else audio.mean(axis=1)
    if rate != RATE:
        audio = resample_poly(audio, RATE, rate, axis=0)
        rate = RATE
    audio = audio - audio.mean(axis=0)
    if loop:
        audio = make_loop(audio, rate, 3.0 if kind == "music" else 1.5)
    else:
        audio = fade(trim(audio, rate), rate)
    audio = level(audio, rate, target, peak_dbfs)
    write(out, audio, rate)
    return {"seconds": len(audio) / rate, "lufs": loudness(audio, rate)}
