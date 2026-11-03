"""Sound effects and ambience: MOSS-SoundEffect v2.0 (Apache 2.0), run with the MOSS venv's Python (ported from
Rimtera's moss_worker.py). MOSS pins transformers 4.57, which ComfyUI's environment cannot take, so it runs in its own
process and venv. The engine starts it per batch:

    <moss-venv>/Scripts/python.exe moss_worker.py < jobs.jsonl

Each stdin line is {"prompt", "seconds", "seed", "out"}; jobs whose file exists are skipped. For each finished clip it
prints one JSON line, {"done": out, "seconds": t}, or {"failed": out, "error": ...}; anything else it prints is a log
line. Output is 48 kHz mono WAV. The model loads once per batch.

Settings come from the environment: AD_MOSS_MODEL_DIR, AD_PRIORITY (normal | below), AD_VRAM_FREE_GB, AD_CPU_FRACTION,
AD_MOSS_STEPS, AD_REST_SECONDS.
"""
import json
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")  # torch.compile + Triton is CUDA-only; ROCm on Windows runs eager.
NEGATIVE = "speech, talking, voice, singing, music, low quality, distorted, muffled, noise"


def say(**message):
    print(json.dumps(message), flush=True)


def set_power(torch):
    if sys.platform == "win32" and os.environ.get("AD_PRIORITY", "below") != "normal":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # BELOW_NORMAL
    total = torch.cuda.get_device_properties(0).total_memory / 2**30
    free = float(os.environ.get("AD_VRAM_FREE_GB", "4"))
    torch.cuda.set_per_process_memory_fraction(max(0.3, (total - free) / total))
    torch.set_num_threads(max(1, int((os.cpu_count() or 4) * float(os.environ.get("AD_CPU_FRACTION", "0.75")))))


def main():
    jobs = [json.loads(line) for line in sys.stdin.read().splitlines() if line.strip()]
    jobs = [job for job in jobs if not Path(job["out"]).exists()]
    if not jobs:
        print("nothing to generate", flush=True)
        return
    import torch
    set_power(torch)
    from moss_soundeffect_v2 import MossSoundEffectPipeline
    import soundfile

    started = time.time()
    pipe = MossSoundEffectPipeline.from_pretrained(os.environ["AD_MOSS_MODEL_DIR"], torch_dtype=torch.bfloat16, device="cuda")
    print(f"model loaded in {time.time() - started:.0f}s; {len(jobs)} clips", flush=True)
    steps = int(os.environ.get("AD_MOSS_STEPS", "50"))
    rest = float(os.environ.get("AD_REST_SECONDS", "0") or 0)
    for job in jobs:
        clip_started = time.time()
        try:
            seconds = min(float(job["seconds"]), pipe.max_inference_seconds)
            # The model denoises a fixed window (30 s by default) and crops it; sizing the window to the clip is many
            # times faster on short sounds.
            window = min(pipe.max_inference_seconds, math.ceil(seconds) + 1)
            audio = pipe(prompt=job["prompt"], seconds=seconds, max_inference_seconds=window, num_inference_steps=steps,
                         cfg_scale=float(job.get("cfg", 4.0)), seed=int(job.get("seed", 0)),
                         negative_prompt=job.get("negative", NEGATIVE), progress_bar_cmd=lambda x, **_: x)
            out = Path(job["out"])
            out.parent.mkdir(parents=True, exist_ok=True)
            partial = out.with_suffix(".part.wav")
            # soundfile, not pipe.save_audio: newer torchaudio needs torchcodec to write files.
            soundfile.write(str(partial), audio[0].float().cpu().numpy().T, pipe.sample_rate, subtype="PCM_16")
            partial.replace(out)
            say(done=str(out), seconds=round(time.time() - clip_started, 2))
        except Exception as error:  # one bad clip should not end an overnight run
            say(failed=job["out"], error=str(error)[:500])
            torch.cuda.empty_cache()
        if rest:
            time.sleep(rest)


if __name__ == "__main__":
    main()
