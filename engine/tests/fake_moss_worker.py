"""A stand-in for moss_worker.py: same stdin jobs and JSON-line replies, writing noise bursts instead of running MOSS."""
import json
import sys
from pathlib import Path

import numpy as np
import soundfile

print("model loaded in 0s (fake)", flush=True)
print("a warning on stderr is a log line, not an error", file=sys.stderr, flush=True)
for line in sys.stdin.read().splitlines():
    job = json.loads(line)
    if "fail" in job["prompt"]:
        print(json.dumps({"failed": job["out"], "error": "fake failure"}), flush=True)
        continue
    out = Path(job["out"])
    rate = 48000
    soundfile.write(str(out), (np.random.default_rng(job["seed"]).normal(0, 0.2, int(job["seconds"] * rate))).astype(np.float32), rate)
    print(json.dumps({"done": str(out), "seconds": 0.1}), flush=True)
