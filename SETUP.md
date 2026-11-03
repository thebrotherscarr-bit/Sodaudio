# Setup

The models and their Python environments are many gigabytes, so neither build ships them. On first run the tool checks what is there (`GET /setup/check`) and offers to install or point at what is missing. The checks and steps below are what that setup job does; they can also be done by hand.

## What is needed

| Part | What | Size (about) |
|---|---|---|
| Engine Python | Python 3.12 with the engine's packages (`engine/requirements.txt`), in `.venv` | 300 MB |
| ComfyUI | An existing install (Comfy Desktop or portable), or a fresh portable one | 5–8 GB with torch |
| GPU torch | ROCm build on AMD, CUDA build on NVIDIA, inside ComfyUI's environment | (in the above) |
| ACE-Step 1.5 turbo (MIT) | `diffusion_models/acestep_v1.5_turbo.safetensors`, `text_encoders/qwen_0.6b_ace15.safetensors`, `text_encoders/qwen_1.7b_ace15.safetensors`, `vae/ace_1.5_vae.safetensors` from `Comfy-Org/ace_step_1.5_ComfyUI_files` on Hugging Face, in ComfyUI's models folders | 8 GB |
| MOSS-SoundEffect v2.0 (Apache 2.0) | Model `OpenMOSS-Team/MOSS-SoundEffect-v2.0` in `models/diffusers/MOSS-SoundEffect-v2.0`; code from `github.com/OpenMOSS/MOSS-TTS` (`moss_soundeffect_v2`) | 6 GB |
| MOSS venv | Its own venv, because MOSS pins transformers 4.57, which ComfyUI's environment cannot take | 1 GB plus shared torch |

## Steps

1. **Engine:** install Python 3.12 (or let `start.cmd` use `uv` to fetch it), create `.venv`, `pip install -r engine/requirements.txt`.
2. **ComfyUI:** the setup job looks for an existing install (Comfy Desktop's instance folder under `%APPDATA%\Comfy Desktop`, a portable `ComfyUI\main.py`, or a server already answering on 8188). If none, it offers to download the portable build for your GPU. The path goes in the settings.
3. **ACE-Step models:** download the four files into ComfyUI's models folders (or the extra model paths file Comfy Desktop uses). Skip any already present.
4. **MOSS venv:** the Rimtera recipe, from `Rimtera/tools/audio/setup_audio.ps1`:
   - create the venv with ComfyUI's Python;
   - add a `.pth` file pointing at ComfyUI's `site-packages`, so it shares ComfyUI's GPU torch instead of downloading another;
   - pin the shared packages (`torch`, `torchaudio`, `torchvision`, `numpy`, `scipy`) to ComfyUI's versions with a constraints file, so pip never replaces the GPU torch;
   - `pip install -c constraints transformers==4.57.1 diffusers==0.37.1 ftfy==6.3.1 soundfile==0.13.1 descript-audiotools==0.7.2 einops==0.8.2`;
   - clone MOSS-TTS and `pip install --no-deps -e moss_soundeffect_v2`;
   - check: import `MossSoundEffectPipeline` and print the GPU name.
5. **Smoke test:** generate a 10-second music take and a 2-second effect, post-process both, play them. The setup screen shows each step's result.

## Starting ComfyUI

The supervisor starts ComfyUI headless with the options Rimtera uses (`Rimtera/tools/comfy/start_comfy.ps1`):

```
python ComfyUI/main.py --listen 127.0.0.1 --port 8188 --disable-dynamic-vram --disable-fast-disk
       [--reserve-vram <GB>] [--extra-model-paths-config <yaml>]
```

- **Focused mode:** `--reserve-vram 1.5`, normal priority.
- **Restricted mode:** `--reserve-vram 4`, below-normal priority, short rests between jobs.
- Its output goes to the day's log in the workspace and to the event stream, so you see it in the app.
- If something already answers on 8188, the engine uses it and does not start or stop it.

### Attach, or start it headless

- **Attach:** if ComfyUI already answers at the address (Comfy Desktop, or a ComfyUI a game's own tools or another agent started), the engine uses it as it is and never starts, stops or restarts it; the restart-every-N-tracks workaround applies only to a ComfyUI the engine started.
- **Start script:** `comfy_start_script` (Setup > Point at existing) starts ComfyUI the way Rimtera's overnight runs did: the script is called with `-ReserveVramGB 1.5 -Priority Normal` (focused) or `-ReserveVramGB 4 -Priority BelowNormal` (restricted), launches ComfyUI hidden and returns; the engine waits for it to answer and then treats it as its own (restarts and stops it).
- **One focus:** before each track the engine looks at ComfyUI's queue; while it is running or holding prompts from another client (an art run), audio waits and says so in the job log.
- **Adopting a game's tools:** Setup > "Use the game's own ComfyUI and MOSS" (or the setup job's find step) reads a game folder's `tools/comfy-app`, `tools/comfy/start_comfy.ps1`, `tools/moss-venv` and `tools/moss`, plus Comfy Desktop's model-paths file and shared input/output folders, and fills in the settings. Nothing in the game is changed.

## GPU notes

- **AMD (ROCm on Windows):** set `TORCH_BLAS_PREFER_HIPBLASLT=0` (hipBLASLt does not support RDNA2) and `MIOPEN_LOG_LEVEL=3` (quiets MIOpen's warnings). Rimtera runs this way on a 16 GB card.
- **NVIDIA (CUDA):** no special settings.
- **16 GB cards:** ACE-Step's language model and text encoder do not fit beside the diffusion model; `--disable-dynamic-vram` lets ComfyUI evict the text encoder while sampling.
- **Unloading:** before the MOSS worker loads, the engine calls ComfyUI `POST /free` with `{"unload_models": true, "free_memory": true}`; after MOSS exits its memory is freed with it.

## Claude API

The agent needs a Claude API key (or the Claude Code login the Agent SDK can use). The key is stored in Windows Credential Manager (Python `keyring`; Electron `safeStorage`), never in the settings file or the project. Without a key, everything except the agent panel works.

## Adopting an existing Rimtera install

Point the settings at Rimtera's existing pieces instead of installing new ones: ComfyUI at `Rimtera/tools/comfy-app`, the MOSS venv at `Rimtera/tools/moss-venv`, the Comfy Desktop extra model paths file. Then open `Rimtera/audio-project.json` (made by the Rimtera importer) with `workspace` set to `tools/audio` to keep every existing take and the promotion log.
