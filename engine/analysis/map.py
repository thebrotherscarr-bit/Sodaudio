"""The music map (ported from Rimtera's music_map.py): beats and bars, loudness, a mel spectrogram, the low harmonic
band where a deep choir sits next to the drums, chroma and the measured tempo, as JSON for the UI to draw. Arrays are
downsampled to about 20 points a second. "Export PNG" keeps the matplotlib picture for sharing."""
import numpy as np

SR = 22050
HOP = 512
DOWN = 2  # 22050 / 512 / 2 = about 21.5 points a second
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def bar_grid(y, sr, bpm, meter=4):
    """Beat times from the tracker (started at the asked tempo) and bar starts every `meter` beats, the phase picked by
    onset strength."""
    import librosa
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    _, beats = librosa.beat.beat_track(onset_envelope=onset, sr=sr, start_bpm=bpm, tightness=400)
    if len(beats) == 0:
        return np.array([]), np.array([])
    phase = max(range(meter), key=lambda p: onset[beats[p::meter]].sum() if len(beats[p::meter]) else 0)
    times = librosa.frames_to_time(beats, sr=sr)
    return times, times[phase::meter]


def extend_beats(beats, duration, meter=4, phase=0):
    """The tracker drops beats in quiet or trailing stretches; carry the grid to both ends of the take at the mean beat
    period, so every part of the take has bar lines. Returns the beats, the bars and the bars' phase in the new list."""
    beats = list(map(float, beats))
    if len(beats) < 2:
        return beats, beats[::meter], 0
    period = (beats[-1] - beats[0]) / (len(beats) - 1)
    head = []
    while beats[0] - period * (len(head) + 1) >= 0:
        head.append(beats[0] - period * (len(head) + 1))
    tail = []
    while beats[-1] + period * (len(tail) + 1) < duration:
        tail.append(beats[-1] + period * (len(tail) + 1))
    full = sorted(head) + beats + tail
    phase = (phase + len(head)) % meter
    return full, full[phase::meter], phase


def estimate_key(chroma) -> str:
    profile = chroma.mean(axis=1)
    best = (-2.0, "")
    for shift in range(12):
        for mode, template in (("major", MAJOR), ("minor", MINOR)):
            score = float(np.corrcoef(np.roll(template, shift), profile)[0, 1])
            if score > best[0]:
                best = (score, f"{NOTES[shift]} {mode}")
    return best[1]


def _down(array, axis=-1):
    axis = axis % array.ndim
    n = array.shape[axis] // DOWN * DOWN
    array = np.take(array, range(n), axis=axis)
    shape = list(array.shape)
    shape[axis:axis + 1] = [n // DOWN, DOWN]
    return array.reshape(shape).mean(axis=axis + 1)


def compute(path, bpm=None, meter=4) -> dict:
    import librosa
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    tracked, tracked_bars = bar_grid(y, sr, float(bpm or 120), int(meter or 4))
    phase = list(tracked).index(tracked_bars[0]) if len(tracked_bars) else 0
    beats, bars, phase = extend_beats(tracked, len(y) / sr, int(meter or 4), phase)
    beats, bars = np.array(beats), np.array(bars)
    harmonic, percussive = librosa.effects.hpss(y)
    rms = librosa.amplitude_to_db(librosa.feature.rms(y=y, hop_length=HOP)[0], ref=1.0)
    spec = np.abs(librosa.stft(harmonic, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr)
    low = librosa.amplitude_to_db(spec[(freqs >= 80) & (freqs <= 500)].mean(axis=0), ref=1.0)
    drums = librosa.amplitude_to_db(librosa.feature.rms(y=percussive, hop_length=HOP)[0], ref=1.0)
    mel = librosa.amplitude_to_db(librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=64), ref=np.max)
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=HOP)
    frames = min(len(rms), len(low), len(drums), mel.shape[1], chroma.shape[1])
    tempo = float(60 * (len(tracked) - 1) / (tracked[-1] - tracked[0])) if len(tracked) > 1 else float(bpm or 0)
    return {
        "file": str(path), "duration": round(len(y) / sr, 3), "tempo": round(tempo, 2), "asked_bpm": bpm, "meter": int(meter or 4),
        "beats": [round(float(b), 3) for b in beats], "bars": [round(float(b), 3) for b in bars], "bar_phase": phase,
        "tracked": [round(float(tracked[0]), 3), round(float(tracked[-1]), 3)] if len(tracked) else None,
        "hop_s": HOP * DOWN / sr, "key": estimate_key(chroma),
        "loudness": np.round(_down(rms[:frames]), 1).tolist(), "low": np.round(_down(low[:frames]), 1).tolist(),
        "drums": np.round(_down(drums[:frames]), 1).tolist(),
        "mel": {"bins": mel.shape[0], "frames": np.round(_down(mel[:, :frames]).T).astype(int).tolist()},
        "chroma": np.round(_down(chroma[:, :frames]).T, 2).tolist(),
    }


def summary(data: dict) -> dict:
    """A short version for the agent: tempo, key, bars with their level, choir and drum presence, quiet stretches."""
    hop = data["hop_s"]
    loud = np.array(data["loudness"])
    low = np.array(data["low"])
    drums = np.array(data["drums"])
    bars = data["bars"] + [data["duration"]]
    median = float(np.median(loud)) if len(loud) else 0
    rows, quiet = [], []
    for number, (start, end) in enumerate(zip(bars[:-1], bars[1:]), 1):
        a, b = int(start / hop), max(int(start / hop) + 1, int(end / hop))
        level = float(loud[a:b].mean()) if b <= len(loud) else median
        rows.append({"bar": number, "start": start, "db": round(level - median, 1),
                     "choir": round(float(low[a:b].mean()) if b <= len(low) else 0, 1),
                     "drums": round(float(drums[a:b].mean()) if b <= len(drums) else 0, 1)})
        if level < median - 12:
            quiet.append(number)
    return {"tempo": data["tempo"], "asked_bpm": data.get("asked_bpm"), "key": data["key"], "duration": data["duration"],
            "bar_count": len(data["bars"]), "bars": rows, "quiet_bars": quiet,
            "note": "bar db is the level against the track's median; choir is the 80-500 Hz harmonic band, drums the percussive part (dB)"}


def export_png(path, out, bpm=None, meter=4):
    import librosa
    import librosa.display
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    y, sr = librosa.load(str(path), sr=SR, mono=True)
    tracked, tracked_bars = bar_grid(y, sr, float(bpm or 120), int(meter or 4))
    phase = list(tracked).index(tracked_bars[0]) if len(tracked_bars) else 0
    beats, bars, _ = extend_beats(tracked, len(y) / sr, int(meter or 4), phase)
    harmonic, percussive = librosa.effects.hpss(y)
    t = librosa.frames_to_time(np.arange(1 + len(y) // HOP), sr=sr, hop_length=HOP)
    rms = librosa.amplitude_to_db(librosa.feature.rms(y=y, hop_length=HOP)[0], ref=1.0)
    spec = np.abs(librosa.stft(harmonic, hop_length=HOP))
    freqs = librosa.fft_frequencies(sr=sr)
    low = librosa.amplitude_to_db(spec[(freqs >= 80) & (freqs <= 500)].mean(axis=0), ref=1.0)
    drums = librosa.amplitude_to_db(librosa.feature.rms(y=percussive, hop_length=HOP)[0], ref=1.0)
    fig, axes = plt.subplots(4, 1, figsize=(22, 13), sharex=True, gridspec_kw={"height_ratios": [1, 2, 1, 1.4]})
    axes[0].plot(t[:len(rms)], rms, lw=0.7)
    axes[0].set_ylabel("loudness dB")
    mel = librosa.amplitude_to_db(librosa.feature.melspectrogram(y=y, sr=sr, hop_length=HOP, n_mels=96), ref=np.max)
    librosa.display.specshow(mel, sr=sr, hop_length=HOP, x_axis="time", y_axis="mel", ax=axes[1], cmap="magma")
    axes[1].set_ylabel("mel Hz")
    axes[2].plot(t[:len(low)], low, lw=0.7, label="low harmonic 80-500 Hz (choir)")
    axes[2].plot(t[:len(drums)], drums, lw=0.7, alpha=0.7, label="percussive (drums)")
    axes[2].legend(loc="upper left", fontsize=8)
    axes[2].set_ylabel("dB")
    chroma = librosa.feature.chroma_cqt(y=harmonic, sr=sr, hop_length=HOP)
    librosa.display.specshow(chroma, sr=sr, hop_length=HOP, x_axis="time", y_axis="chroma", ax=axes[3], cmap="viridis")
    for ax in axes:
        for start in bars:
            ax.axvline(start, color="white" if ax in (axes[1], axes[3]) else "grey", lw=0.5, alpha=0.6)
    top = axes[0].get_ylim()[1]
    for number, start in enumerate(bars, 1):
        axes[0].text(start, top, str(number), fontsize=7, va="bottom", ha="center")
    axes[3].set_xticks(np.arange(0, len(y) / sr, 5))
    tempo = 60 * (len(tracked) - 1) / (tracked[-1] - tracked[0]) if len(tracked) > 1 else 0
    fig.suptitle(f"{getattr(path, 'name', path)}: {len(bars)} bars, about {tempo:.1f} BPM")
    fig.tight_layout()
    fig.savefig(str(out), dpi=80)
    plt.close(fig)
