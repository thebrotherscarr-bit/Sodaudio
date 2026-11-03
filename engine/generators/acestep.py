"""Music with ACE-Step 1.5 turbo in ComfyUI (ported from Rimtera's audio_client.py). Builds the same graph as
ComfyUI's "ACE-Step 1.5" template, submits it, follows ComfyUI's WebSocket for progress (polling /history as the
fallback) and fetches the FLAC."""
import json
import threading
import time
import urllib.parse
import uuid

import httpx

UNET = "acestep_v1.5_turbo.safetensors"
CLIP_SMALL = "qwen_0.6b_ace15.safetensors"
CLIP_LARGE = "qwen_1.7b_ace15.safetensors"
VAE = "ace_1.5_vae.safetensors"
MODEL_FILES = {"diffusion_models": [UNET], "text_encoders": [CLIP_SMALL, CLIP_LARGE], "vae": [VAE]}


def music_workflow(tags, seconds=120.0, bpm=100, key="C minor", time_signature="4", seed=0, steps=8,
                   lyrics="[Instrumental]", prefix="sodaudio/music"):
    return {
        "unet": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}},
        "shift": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["unet", 0], "shift": 3}},
        "clip": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": CLIP_SMALL, "clip_name2": CLIP_LARGE, "type": "ace", "device": "default"}},
        "vae": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "encode": {"class_type": "TextEncodeAceStepAudio1.5", "inputs": {
            "clip": ["clip", 0], "tags": tags, "lyrics": lyrics, "seed": seed, "bpm": int(bpm), "duration": float(seconds),
            "timesignature": str(time_signature), "language": "en", "keyscale": key, "generate_audio_codes": True,
            "cfg_scale": 2.0, "temperature": 0.85, "top_p": 0.9, "top_k": 0, "min_p": 0.0}},
        "negative": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["encode", 0]}},
        "latent": {"class_type": "EmptyAceStep1.5LatentAudio", "inputs": {"seconds": float(seconds), "batch_size": 1}},
        "sample": {"class_type": "KSampler", "inputs": {
            "model": ["shift", 0], "positive": ["encode", 0], "negative": ["negative", 0], "latent_image": ["latent", 0],
            "seed": seed, "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        # Tiled: a whole-track decode asks MIOpen for gigabytes of conv workspace and crashes on 16 GB AMD cards.
        "decode": {"class_type": "VAEDecodeAudioTiled", "inputs": {"samples": ["sample", 0], "vae": ["vae", 0], "tile_size": 256, "overlap": 32}},
        "save": {"class_type": "SaveAudio", "inputs": {"audio": ["decode", 0], "filename_prefix": prefix}},
    }


class ComfyError(RuntimeError):
    pass


class AceStep:
    def __init__(self, url="http://127.0.0.1:8188"):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex
        self.http = httpx.Client(timeout=60)

    def interrupt(self):
        self.http.post(self.url + "/interrupt", json={})

    def _fetch(self, entry):
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            raise ComfyError(json.dumps(status.get("messages", []))[:2000])
        for output in entry.get("outputs", {}).values():
            for audio in output.get("audio", []):
                query = urllib.parse.urlencode({"filename": audio["filename"], "subfolder": audio["subfolder"], "type": audio["type"]})
                return self.http.get(f"{self.url}/view?{query}").raise_for_status().content
        raise ComfyError("the workflow finished without saving audio")

    def run(self, graph, on_progress=None, cancelled=lambda: False, timeout=3600) -> bytes:
        """Queues the graph and waits; on_progress(step, of) follows sampling. Returns the audio file's bytes."""
        ws_url = self.url.replace("http", "ws", 1) + f"/ws?clientId={self.client_id}"
        done = threading.Event()
        errors = []
        prompt_holder = {}

        def listen():
            try:
                from websockets.sync.client import connect
                with connect(ws_url, open_timeout=10, max_size=None) as socket:
                    while not done.is_set():
                        try:
                            raw = socket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if not isinstance(raw, str):
                            continue
                        message = json.loads(raw)
                        data = message.get("data", {})
                        if data.get("prompt_id") not in (None, prompt_holder.get("id")):
                            continue
                        if message.get("type") == "progress" and on_progress:
                            on_progress(int(data.get("value", 0)), int(data.get("max", 1)), data.get("node"))
                        elif message.get("type") == "execution_error":
                            errors.append(data.get("exception_message", "execution error"))
                            done.set()
                        elif message.get("type") == "executing" and data.get("node") is None and data.get("prompt_id"):
                            done.set()
            except Exception:
                pass  # /history polling below still finishes the job

        listener = threading.Thread(target=listen, daemon=True)
        listener.start()
        time.sleep(0.2)
        response = self.http.post(self.url + "/prompt", json={"prompt": graph, "client_id": self.client_id})
        if response.status_code != 200:
            done.set()
            raise ComfyError(f"ComfyUI refused the workflow: {response.text[:1500]}")
        prompt_id = response.json()["prompt_id"]
        prompt_holder["id"] = prompt_id
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                if cancelled():
                    self.interrupt()
                    raise InterruptedError("cancelled")
                if errors:
                    raise ComfyError(errors[0])
                history = self.http.get(f"{self.url}/history/{prompt_id}").json()
                if prompt_id in history and (history[prompt_id].get("outputs") or history[prompt_id].get("status", {}).get("completed") is not None
                                             or history[prompt_id].get("status", {}).get("status_str")):
                    return self._fetch(history[prompt_id])
                done.wait(2)
            raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish in {timeout}s")
        finally:
            done.set()
