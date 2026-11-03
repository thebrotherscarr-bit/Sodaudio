"""The engine's shared state: settings, the open project and its queue, the ComfyUI supervisor, the event stream and
the day logs. Routes, job handlers and the agent all reach the engine through one Services object."""
import asyncio
import collections
import threading
import time
from pathlib import Path

from . import VERSION
from .jobs import Queue
from .project import Project
from .settings import Settings, home
from .supervisor import Supervisor
from .util import AUDIO_EXTS


class EventBus:
    """One WebSocket stream for everything. emit() is safe from any thread."""

    def __init__(self):
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set[asyncio.Queue] = set()
        self.recent = {"engine": collections.deque(maxlen=2000), "comfy": collections.deque(maxlen=4000)}

    def attach(self, loop):
        self.loop = loop

    def subscribe(self) -> asyncio.Queue:
        queue = asyncio.Queue(maxsize=5000)
        self.clients.add(queue)
        return queue

    def unsubscribe(self, queue):
        self.clients.discard(queue)

    def close_all(self):
        """Wakes every socket handler with a sentinel so it closes: an open window must not hold up shutting down."""
        for queue in list(self.clients):
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def _deliver(self, event):
        for queue in list(self.clients):
            if queue.full():
                continue  # a stalled client misses events rather than stalling the engine
            queue.put_nowait(event)

    def emit(self, event: dict):
        if event.get("type") == "log" and event.get("source") in self.recent:
            self.recent[event["source"]].append(event["line"])
        if self.loop is None or self.loop.is_closed():
            return
        try:
            self.loop.call_soon_threadsafe(self._deliver, event)
        except RuntimeError:
            pass


class Services:
    def __init__(self, settings: Settings | None = None, handlers=None):
        self.settings = settings or Settings()
        self.events = EventBus()
        self.project: Project | None = None
        self.queue: Queue | None = None
        self.supervisor = Supervisor(self)
        self.handlers = handlers or {}
        self.agent = None
        self._health = None
        self._log_lock = threading.Lock()
        self.started = time.time()

    # --- events and logs -----------------------------------------------------------------------------------------
    def emit(self, event):
        self.events.emit(event)

    def log_dir(self) -> Path:
        return self.project.ws("logs") if self.project else home() / "logs"

    def log(self, line, source="engine"):
        line = str(line).rstrip()
        stamp = time.strftime("%Y-%m-%d")
        try:
            with self._log_lock:
                folder = self.log_dir()
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / f"{source}-{stamp}.log").open("a", encoding="utf-8") as handle:
                    handle.write(f"{time.strftime('%H:%M:%S')} {line}\n")
        except OSError:
            pass  # a full disk must not stop the event stream
        self.emit({"type": "log", "source": source, "line": line})

    def rotate_logs(self):
        """Deletes day logs older than the setting (14 days by default)."""
        cutoff = time.time() - 86400 * int(self.settings["log_days"] or 14)
        for path in self.log_dir().glob("*.log"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass

    # --- health --------------------------------------------------------------------------------------------------
    def health(self) -> dict:
        running = None
        if self.queue and self.queue.running:
            ctx = next(iter(self.queue.running.values()))
            job = self.queue.store.get(ctx.id) or {}
            running = {"id": ctx.id, "type": job.get("type"), "progress": job.get("progress"), "message": job.get("message")}
        return {"ok": True, "version": VERSION, "comfy": self.supervisor.state, "gpu": self.supervisor.gpu(),
                "busy": bool(self.queue and self.queue.busy()), "paused": bool(self.queue and self.queue.paused),
                "mode": self.settings["gpu_mode"], "generator": self.settings["generator"], "running": running,
                "queued": len(self.queue.store.list("queued")) if self.queue else 0,
                "project": self.project.data.get("name") if self.project else None,
                "agent": self.settings.agent_ready()}

    def health_changed(self):
        health = self.health()
        key = {k: v for k, v in health.items() if k != "running"}
        key["running"] = (health["running"] or {}).get("id")
        if key != self._health:
            self._health = key
            self.emit({"type": "health", **health})

    # --- projects ------------------------------------------------------------------------------------------------
    def open_project(self, path, create=False, name=""):
        if self.queue and self.queue.busy():
            raise RuntimeError("a job is running: wait for it or cancel it before switching projects")
        project = Project.open(path, create=create, name=name)
        if self.queue:
            self.queue.stop(wait=True, timeout=10)
            self.queue.store.close()
        self.project = project
        self.queue = Queue(self, project.ws("jobs.sqlite"), self.handlers)
        self.queue.start()
        self.settings.remember_project(str(project.path))
        self.rotate_logs()
        if self.agent:
            self.agent.project_changed()
        self.log(f"opened {project.path}")
        self.health_changed()
        return project

    def require_project(self) -> Project:
        if not self.project:
            raise LookupError("no project is open")
        return self.project

    # --- files ---------------------------------------------------------------------------------------------------
    def resolve_audio(self, ident: str) -> Path:
        """An audio file from a workspace-relative path, "game:" plus a game-relative path, a bare take or candidate
        name, or (Electron, dropped files) an absolute path."""
        project = self.require_project()
        ident = str(ident).strip()
        if ident.startswith("game:"):
            path = (project.root / ident[5:]).resolve()
        elif Path(ident).is_absolute():
            path = Path(ident)
        elif "/" in ident or "\\" in ident:
            path = (project.workspace / ident).resolve()
        else:
            path = self.find_take(ident)
        if path is None or not path.is_file():
            raise FileNotFoundError(f"no audio file {ident!r}")
        if path.suffix.lower() not in AUDIO_EXTS:
            raise ValueError(f"{path.name} is not an audio file")
        return path

    def find_take(self, name: str) -> Path | None:
        """mus_title__c8 (a raw take) or mus_title__c8loop.wav (a candidate)."""
        project = self.require_project()
        from .util import cue_of
        try:
            category = project.cue(cue_of(name))["category"]
        except KeyError:
            return None
        stem = Path(name).stem if Path(name).suffix.lower() in AUDIO_EXTS else name
        for folder in (project.raw_dir(category), project.candidate_dir(category)):
            for ext in (".flac", ".wav", ".ogg"):
                if (folder / (stem + ext)).exists():
                    return folder / (stem + ext)
        return None

    def shutdown(self):
        if self.queue:
            self.queue.stop(wait=False)
        self.supervisor.shutdown()
