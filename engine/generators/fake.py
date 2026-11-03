"""A stand-in generator for tests and demos: no GPU, no ComfyUI, no MOSS. Music is a click track with a chord pad at
the cue's tempo; effects are shaped noise bursts. It reports progress like the real ones."""
import time

import numpy as np
import soundfile

RATE = 48000


def music(path, seconds, bpm, seed, on_progress=None, cancelled=lambda: False, step_delay=0.05, steps=8):
    for step in range(1, steps + 1):
        if cancelled():
            raise InterruptedError("cancelled")
        time.sleep(step_delay)
        if on_progress:
            on_progress(step, steps, None)
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    root = 110 * 2 ** (rng.integers(0, 12) / 12)
    pad = sum(np.sin(2 * np.pi * root * r * t) for r in (1, 1.25, 1.5)) * 0.08
    beat = 60.0 / float(bpm or 120)
    phase = (t % beat) / beat
    click = np.exp(-phase * 40) * np.sin(2 * np.pi * 1000 * t) * 0.5
    downbeat = ((t // beat) % 4 == 0) * np.exp(-phase * 30) * np.sin(2 * np.pi * 60 * t) * 0.6
    mono = pad + click + downbeat
    stereo = np.stack([mono, mono * 0.95], axis=1)
    soundfile.write(str(path), stereo.astype(np.float32), RATE, format="FLAC" if str(path).endswith(".flac") else None)


def effect(path, seconds, seed, step_delay=0.05):
    time.sleep(step_delay)
    rng = np.random.default_rng(seed)
    n = max(1, int(seconds * RATE))
    t = np.arange(n) / RATE
    body = rng.normal(0, 0.3, n) * np.exp(-t * (3 + rng.random() * 8))
    tone = np.sin(2 * np.pi * (200 + rng.random() * 800) * t) * np.exp(-t * 6) * 0.3
    silence = np.zeros(int(0.1 * RATE))
    soundfile.write(str(path), np.concatenate([silence, body + tone, silence]).astype(np.float32), RATE, subtype="PCM_16")
