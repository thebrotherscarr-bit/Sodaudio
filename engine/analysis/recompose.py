"""Recompose (ported from Rimtera's recompose.py): a loop built from chosen pieces of a take, joined on bar lines.

Each piece is copied from the uncut raw take with its gain in dB (the audio itself is untouched); joins are
equal-power crossfades a quarter beat long, centred on the cut; `ring` (where the last piece's sound really dies away)
lays that ring-out over the loop's head; the result is levelled like every candidate. `overlay` mixes a stretch of the
take over the loop; `clean` (expert, off by default: cleaning vocals "sounds weird") subtracts a steady drone from it."""
import numpy as np
from scipy.signal import istft, stft

from ..post import RATE, level, load, write


def rms_db(audio):
    return 10 * np.log10(np.mean(audio ** 2) + 1e-12)


def subtract(audio, noise, rate):
    """Spectral subtraction: removes the average spectrum of `noise` (a steady drone) from each channel of `audio`."""
    out = np.zeros_like(audio)
    for channel in range(audio.shape[1]):
        _, _, spec = stft(audio[:, channel], rate, nperseg=2048)
        _, _, hum = stft(noise[:, channel], rate, nperseg=2048)
        profile = np.abs(hum).mean(axis=1, keepdims=True) * 1.5
        magnitude = np.maximum(np.abs(spec) - profile, 0)
        _, clean = istft(magnitude * np.exp(1j * np.angle(spec)), rate, nperseg=2048)
        out[:, channel] = clean[:len(audio)]
    return out


def recompose(source, out, pieces, fade=0.23, ring=None, overlay=None, target=-16.0, peak_dbfs=-1.0):
    """pieces: [{from, to, gain_db}] in seconds of the take, in playing order. overlay: {from, to, at, db?, clean?: [c, d]}.
    `fade` is the crossfade at each join (a quarter beat at the measured tempo is the usual choice)."""
    audio = load(source)
    half = int(fade * RATE / 2)
    spans = [(int(float(p["from"]) * RATE), int(float(p["to"]) * RATE), 10 ** (float(p.get("gain_db") or 0) / 20)) for p in pieces]
    if not spans:
        raise ValueError("recompose needs at least one piece")
    rise = np.sqrt(np.linspace(0, 1, 2 * half))[:, None]
    result = audio[spans[0][0]:spans[0][1] + half] * spans[0][2]
    for start, end, gain in spans[1:]:
        # The outgoing piece plays half a fade past its cut and the incoming one starts half a fade early.
        incoming = audio[max(0, start - half):end + half] * gain
        result[-2 * half:] = result[-2 * half:] * rise[::-1] + incoming[:2 * half] * rise
        result = np.concatenate([result, incoming[2 * half:]])
    result = result[:len(result) - half]
    if ring:
        tail = audio[spans[-1][1]:int(float(ring) * RATE)] * spans[-1][2]
        n = min(len(tail), len(result))
        result[:n] += tail[:n]
    if overlay:
        a, b = int(float(overlay["from"]) * RATE), int(float(overlay["to"]) * RATE)
        voice = audio[a:b].copy()
        if overlay.get("clean"):
            c, d = (int(float(x) * RATE) for x in overlay["clean"])
            voice = subtract(voice, audio[c:d], RATE)
        at = int(float(overlay.get("at", 0)) * RATE)
        n = min(len(voice), len(result) - at)
        voice = voice[:n]
        edge = min(int(0.05 * RATE), n // 2)
        voice[:edge] *= np.linspace(0, 1, edge)[:, None]
        voice[-edge:] *= np.linspace(1, 0, edge)[:, None]
        voice *= 10 ** ((rms_db(result[at:at + n]) + float(overlay.get("db", -4.0)) - rms_db(voice)) / 20)
        result[at:at + n] += voice
    result = level(result, RATE, target, peak_dbfs)
    write(out, result)
    return {"seconds": len(result) / RATE, "pieces": len(spans)}
