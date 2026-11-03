"""A stand-in ComfyUI for tests: answers /system_stats, /prompt, /history, /view, /interrupt and /free like ComfyUI does,
and "renders" a short FLAC for each prompt. No GPU, no models. Started by the engine's supervisor exactly like the
real main.py (same arguments)."""
import argparse
import io
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import soundfile

PROMPTS = {}


def flac(seconds, bpm):
    rate = 48000
    t = np.arange(int(seconds * rate)) / rate
    beat = 60 / bpm
    audio = np.exp(-((t % beat) / beat) * 40) * np.sin(2 * np.pi * 800 * t) * 0.5 + np.sin(2 * np.pi * 220 * t) * 0.1
    buffer = io.BytesIO()
    soundfile.write(buffer, np.stack([audio, audio], 1).astype(np.float32), rate, format="FLAC")
    return buffer.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        print("fake comfy:", self.command, self.path, flush=True)

    def send(self, data, content="application/json", code=200):
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/system_stats":
            return self.send({"system": {"comfyui_version": "fake"}, "devices": [{"name": "Fake GPU", "vram_total": 16 * 2**30, "vram_free": 12 * 2**30}]})
        if url.path.startswith("/history/"):
            prompt_id = url.path.split("/")[-1]
            job = PROMPTS.get(prompt_id)
            if not job or time.time() < job["ready"]:
                return self.send({})
            return self.send({prompt_id: {"status": {"status_str": "success", "completed": True},
                                          "outputs": {"save": {"audio": [{"filename": f"{prompt_id}.flac", "subfolder": "", "type": "output"}]}}}})
        if url.path == "/queue":
            waiting = [[1, pid, {}, {"client_id": job["client"]}, []] for pid, job in PROMPTS.items() if time.time() < job["ready"]]
            return self.send({"queue_running": waiting[:1], "queue_pending": waiting[1:]})
        if url.path == "/view":
            name = parse_qs(url.query)["filename"][0]
            return self.send(PROMPTS[name.split(".")[0]]["audio"], "audio/flac")
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/prompt":
            encode = body["prompt"]["encode"]["inputs"]
            prompt_id = uuid.uuid4().hex
            PROMPTS[prompt_id] = {"ready": time.time() + float(body.get("extra_data", {}).get("seconds", 1.0)), "client": body.get("client_id"), "audio": flac(min(float(encode["duration"]), 12), encode["bpm"]),
                                  "tags": encode["tags"], "lyrics": encode["lyrics"], "seed": encode["seed"]}
            print("fake comfy: prompt", prompt_id, "seed", encode["seed"], flush=True)
            return self.send({"prompt_id": prompt_id, "number": 1})
        if self.path in ("/interrupt", "/free"):
            return self.send({})
        self.send({"error": "not found"}, code=404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8188)
    args, _ = parser.parse_known_args()
    server = ThreadingHTTPServer((args.listen, args.port), Handler)
    print(f"fake ComfyUI on {args.listen}:{args.port}", flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
