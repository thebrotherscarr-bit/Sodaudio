"""App settings in %APPDATA%\\Sodaudio\\settings.json. The Claude API key lives in Windows Credential Manager
(keyring), or arrives from Electron in ANTHROPIC_API_KEY; it is never written to the settings file."""
import os
import threading
from pathlib import Path

from .util import read_json, write_json

KEYRING_SERVICE = "Sodaudio"
KEYRING_USER = "anthropic-api-key"
AGENT_KEY_USER = "agent-api-key"  # the key for an OpenAI-compatible provider (local servers need none)

DEFAULTS = {
    "comfy_url": "http://127.0.0.1:8188",
    "comfy_path": "",              # folder holding ComfyUI/main.py (portable) or the Comfy Desktop install
    "comfy_python": "",            # the Python of ComfyUI's environment
    "comfy_extra_model_paths": "",  # Comfy Desktop's extra model paths yaml
    "comfy_extra_args": "",        # anything else for main.py, e.g. --input-directory
    "comfy_start_script": "",      # optional: start ComfyUI with this script instead (e.g. Rimtera's tools/comfy/start_comfy.ps1)
    "comfy_models_dirs": [],       # where to look for the ACE-Step files (setup check)
    "moss_python": "",             # the MOSS venv's python.exe
    "moss_model_dir": "",          # models/diffusers/MOSS-SoundEffect-v2.0
    "moss_code_dir": "",           # the MOSS-TTS checkout
    "moss_steps": 50,
    "gpu_mode": "focused",         # focused | restricted
    "restart_every": 10,           # restart a ComfyUI we started after this many tracks
    "rest_seconds": 20,            # restricted mode: pause between GPU jobs
    "generator": "comfy",          # comfy | fake (tests and demos: no GPU)
    "agent_provider": "anthropic", # anthropic (Anthropic's Agent SDK) | openai (any OpenAI-compatible chat API)
    "agent_model": "claude-opus-5-5",
    "agent_base_url": "",          # openai provider: e.g. https://api.openai.com/v1 or http://127.0.0.1:11434/v1 (Ollama)
    "fs_roots": [],                # folders the folder browser may show; empty: the user's home and project folders
    "recent_projects": [],
    "notify": True,
    "keep_in_tray": True,
    "keep_awake": True,
    "start_with_windows": False,
    "log_days": 14,
    "fake_delay": 0.05,            # fake generator: seconds per sampling step
}


def home() -> Path:
    base = os.environ.get("SODAUDIO_HOME") or Path(os.environ.get("APPDATA", Path.home())) / "Sodaudio"
    return Path(base)


class Settings:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else home() / "settings.json"
        self._lock = threading.Lock()
        self.data = {**DEFAULTS, **(read_json(self.path, {}) or {})}

    def __getitem__(self, key):
        return self.data.get(key, DEFAULTS.get(key))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, changes: dict) -> dict:
        changes = {k: v for k, v in changes.items() if k in DEFAULTS}
        with self._lock:
            self.data.update(changes)
            write_json(self.path, self.data)
        return self.public()

    def remember_project(self, path: str) -> None:
        recent = [p for p in self.data.get("recent_projects", []) if p != path]
        self.update({"recent_projects": [path, *recent][:12]})

    # --- API key -------------------------------------------------------------------------------------------------
    def api_key(self) -> str | None:
        if os.environ.get("ANTHROPIC_API_KEY"):
            return os.environ["ANTHROPIC_API_KEY"]
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        except Exception:
            return None

    def set_api_key(self, key: str | None) -> None:
        import keyring
        if key:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
            except Exception:
                pass

    def agent_key(self) -> str | None:
        if os.environ.get("SODAUDIO_AGENT_KEY"):
            return os.environ["SODAUDIO_AGENT_KEY"]
        try:
            import keyring
            return keyring.get_password(KEYRING_SERVICE, AGENT_KEY_USER)
        except Exception:
            return None

    def set_agent_key(self, key: str | None) -> None:
        import keyring
        if key:
            keyring.set_password(KEYRING_SERVICE, AGENT_KEY_USER, key)
        else:
            try:
                keyring.delete_password(KEYRING_SERVICE, AGENT_KEY_USER)
            except Exception:
                pass

    def agent_ready(self) -> bool:
        """Whether the agent panel can run: Claude needs a key (or a Claude login); an OpenAI-compatible provider needs
        an address and a model (a key only if the provider wants one)."""
        if self["agent_provider"] == "openai":
            return bool(self["agent_base_url"] and self["agent_model"])
        return bool(self.api_key() or os.environ.get("SODAUDIO_AGENT_LOGIN"))

    def public(self) -> dict:
        return {**self.data, "api_key_set": bool(self.api_key()), "agent_key_set": bool(self.agent_key()),
                "agent_ready": self.agent_ready(), "home": str(home())}
