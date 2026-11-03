"""Loop cut (ported from Rimtera's loop_cut.py): the best seamless loop of a given length inside a take.

Every candidate stretch is a whole number of bars between min and max seconds. Stretches with a quiet patch are
skipped (except inside `include`); of the rest it keeps the one whose end sounds most like its start and is loudest,
then crossfades the join over half a beat. Bar maths uses the measured tempo (and the map's bar lines when given),
never the tempo the cue asked for."""
import numpy as np

from ..post import RATE, level, load, write


def spectrum(audio, at, size):
    """Magnitude spectrum of `size` samples from `at`, as a unit vector (for comparing two moments of the track)."""
    piece = audio[at:at + size]
    shape = np.abs(np.fft.rfft(piece * np.hanning(len(piece))))
    return shape / (np.linalg.norm(shape) + 1e-12)


def find_loop(source, out, tempo, min_s=30.0, max_s=45.0, include=None, meter=4, bars=None, target=-16.0, peak_dbfs=-1.0):
    audio = load(source)
    mono = audio.mean(axis=1)
    beat = RATE * 60 / float(tempo)
    bar = beat * int(meter or 4)
    hop, window = RATE // 4, RATE
    loudness = np.array([10 * np.log10(np.mean(mono[i:i + window] ** 2) + 1e-12) for i in range(0, len(mono) - window + 1, hop)])
    floor = np.median(loudness) - 12
    size = int(beat)
    keep = [int(float(x) * RATE) for x in include] if include else None

    # Candidate (start, length, bars): on the map's bar lines when there are any, else on a quarter-beat grid.
    candidates = []
    if bars and len(bars) > 1:
        starts = [int(b * RATE) for b in bars]
        for i, start in enumerate(starts):
            for j in range(i + 1, len(starts)):
                length = starts[j] - start
                if length > max_s * RATE:
                    break
                if length >= min_s * RATE:
                    candidates.append((start, length, j - i))
    else:
        for count in range(int(np.ceil(min_s * RATE / bar)), int(max_s * RATE / bar) + 1):
            length = int(round(count * bar))
            candidates += [(start, length, count) for start in range(0, len(mono) - length, int(beat / 4))]

    best = None
    for start, length, count in candidates:
        if start + length + int(beat / 2) > len(mono):
            continue
        if keep and not (start <= keep[0] and start + length >= keep[1]):
            continue
        span = loudness[start // hop:(start + length - window) // hop + 1]
        if len(span) == 0 or (not keep and span.min() < floor):
            continue
        match = float(spectrum(mono, start, size) @ spectrum(mono, start + length - size, size))
        score = match + 0.01 * float(span.mean())
        if best is None or score > best[0]:
            best = (score, start, length, count, match)
    if best is None:
        raise ValueError(f"no stretch of {min_s:g}-{max_s:g} s without a quiet patch")
    _, start, length, count, match = best
    fade = int(beat / 2)
    # The half beat that follows the loop in the take is laid over its head, so on repeat the start carries on from
    # the end the way the music would have.
    loop = audio[start:start + length + fade].copy()
    curve = np.sqrt(np.linspace(0, 1, fade))[:, None]
    loop[:fade] = loop[:fade] * curve + loop[-fade:] * curve[::-1]
    loop = loop[:length]
    loop = level(loop, RATE, target, peak_dbfs)
    write(out, loop)
    return {"start": start / RATE, "seconds": length / RATE, "bars": count, "match": round(match, 3)}
