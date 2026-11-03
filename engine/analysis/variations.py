"""Variations: extra takes from one clip by reversing it and pitching it up and down, with and without changing its
length (a pitch shift keeps the length; a "speed" change pitches like tape and shortens or stretches it)."""
import numpy as np
import soundfile
from scipy.signal import resample_poly

from ..post import level


def make(source, out_dir, stem, kinds=("reverse", "pitch"), semitones=(-5, 5), stretch=True, target=-16.0, peak_dbfs=-1.0):
    """Writes <stem>_rev, <stem>_up5 / _down5 (same length) and <stem>_up5s / _down5s (tape-style). Returns the names."""
    import librosa
    audio, rate = soundfile.read(str(source), always_2d=True, dtype="float64")
    made = []

    def save(name, data):
        data = level(np.ascontiguousarray(data), rate, target, peak_dbfs)
        soundfile.write(str(out_dir / f"{name}.wav"), data.astype(np.float32), rate, subtype="PCM_16")
        made.append(f"{name}.wav")

    if "reverse" in kinds:
        save(f"{stem}_rev", audio[::-1].copy())
    if "pitch" in kinds:
        for steps in semitones:
            steps = int(steps)
            if steps == 0:
                continue
            tag = f"up{steps}" if steps > 0 else f"down{-steps}"
            shifted = np.stack([librosa.effects.pitch_shift(audio[:, c].copy(), sr=rate, n_steps=steps) for c in range(audio.shape[1])], axis=1)
            save(f"{stem}_{tag}", shifted)
            if stretch:
                # Tape-style: play faster (or slower) at the same rate, so pitch and length change together.
                ratio = 2 ** (steps / 12)
                up, down = (1000, int(round(1000 * ratio)))
                save(f"{stem}_{tag}s", resample_poly(audio, up, down, axis=0))
    return made
