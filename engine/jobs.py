"""The job queue: SQLite-backed so a restart resumes it, one GPU worker (the GPU lock: one GPU job at a time) and a
small CPU pool. Jobs can be paused, cancelled (a running one is interrupted) and reordered."""
import errno
import json
import sqlite3
import threading
import time
import traceback
from pathlib import Path

STATES = ("queued", "running", "done", "failed", "cancelled")
CPU_WORKERS = 2


class Cancelled(Exception):
    pass


class JobContext:
    """What a running job handler gets: logging, progress, cancellation and the engine's services."""

    def __init__(self, queue, job):
        self.queue, self.job, self.id = queue, job, job["id"]
        self.services = queue.services
        self.project = queue.services.project
        self.cancelled = False
        self._on_cancel = []
        self._last_emit = 0.0
        self.log_path = queue.log_dir / f"{self.id}.log"

    def log(self, line: str, source="job"):
        line = str(line).rstrip()
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.services.emit({"type": "log", "source": source, "job_id": self.id, "line": line})

    def progress(self, value: float, eta_s=None, message=None, force=False):
        value = max(0.0, min(1.0, float(value)))
        self.queue.store.update(self.id, progress=value, message=message)
        if force or time.time() - self._last_emit > 0.25:
            self._last_emit = time.time()
            self.services.emit({"type": "job", "id": self.id, "state": "running", "progress": value, "eta_s": eta_s,
                                "message": message, "job_type": self.job["type"]})

    def on_cancel(self, callback):
        self._on_cancel.append(callback)
        return callback

    def check(self):
        if self.cancelled:
            raise Cancelled()

    def submit(self, type_, body):
        return self.queue.submit(type_, body)

    def cancel(self):
        self.cancelled = True
        for callback in list(self._on_cancel):
            try:
                callback()
            except Exception as error:  # an interrupt that fails must not crash the request that asked for it
                self.log(f"interrupt failed: {error}")


class JobStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, body TEXT, state TEXT, progress REAL DEFAULT 0,
                message TEXT, position REAL, gpu INTEGER, created REAL, started REAL, finished REAL, result TEXT, error TEXT)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS tracks (
                job INTEGER, cue TEXT, kind TEXT, seconds REAL, audio_seconds REAL, finished REAL)""")

    def row(self, r):
        if r is None:
            return None
        job = dict(r)
        job["body"] = json.loads(job["body"] or "{}")
        job["result"] = json.loads(job["result"]) if job["result"] else None
        return job

    def add(self, type_, body, gpu):
        with self.lock:
            position = (self.db.execute("SELECT MAX(position) FROM jobs").fetchone()[0] or 0) + 1
            cur = self.db.execute("INSERT INTO jobs (type, body, state, position, gpu, created) VALUES (?,?,?,?,?,?)",
                                  (type_, json.dumps(body), "queued", position, int(gpu), time.time()))
            return cur.lastrowid

    def get(self, job_id):
        with self.lock:
            return self.row(self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list(self, state=None, limit=300):
        with self.lock:
            if state:
                states = state.split(",")
                rows = self.db.execute(f"SELECT * FROM jobs WHERE state IN ({','.join('?' * len(states))}) ORDER BY position LIMIT ?",
                                       (*states, limit)).fetchall()
            else:
                rows = self.db.execute("""SELECT * FROM (SELECT * FROM jobs WHERE state IN ('queued','running')
                                          UNION ALL SELECT * FROM (SELECT * FROM jobs WHERE state NOT IN ('queued','running')
                                          ORDER BY finished DESC LIMIT ?))""", (limit,)).fetchall()
            return [self.row(r) for r in rows]

    def update(self, job_id, **fields):
        if not fields:
            return
        for key in ("result", "body"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        with self.lock:
            self.db.execute(f"UPDATE jobs SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), job_id))

    def next_queued(self, gpu: bool):
        with self.lock:
            return self.row(self.db.execute("SELECT * FROM jobs WHERE state='queued' AND gpu=? ORDER BY position LIMIT 1",
                                            (int(gpu),)).fetchone())

    def claim(self, job_id) -> bool:
        with self.lock:
            cur = self.db.execute("UPDATE jobs SET state='running', started=? WHERE id=? AND state='queued'", (time.time(), job_id))
            return cur.rowcount == 1

    def resume_interrupted(self):
        """After a crash or restart, jobs that were running go back to the head of the queue."""
        with self.lock:
            self.db.execute("UPDATE jobs SET state='queued', position=position-1000000 WHERE state='running'")

    def reorder(self, ids):
        with self.lock:
            # Keep the reordered block ahead of anything else queued, in the given order.
            low = self.db.execute("SELECT MIN(position) FROM jobs WHERE state='queued'").fetchone()[0] or 0
            for index, job_id in enumerate(ids):
                self.db.execute("UPDATE jobs SET position=? WHERE id=? AND state='queued'", (low - len(ids) + index, job_id))

    def add_track(self, job, cue, kind, seconds, audio_seconds):
        with self.lock:
            self.db.execute("INSERT INTO tracks VALUES (?,?,?,?,?,?)", (job, cue, kind, seconds, audio_seconds, time.time()))

    def tracks(self, limit=500):
        with self.lock:
            rows = self.db.execute("SELECT * FROM tracks ORDER BY finished DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in reversed(rows)]

    def close(self):
        with self.lock:
            self.db.close()


class Queue:
    def __init__(self, services, path: Path, handlers: dict):
        self.services = services
        self.store = JobStore(path)
        self.log_dir = Path(path).parent / "logs" / "jobs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.handlers = handlers  # type -> (function, uses_gpu)
        self.running: dict[int, JobContext] = {}
        self.paused = False
        self.stopping = False
        self.cond = threading.Condition()
        self.threads = []
        self.store.resume_interrupted()

    # --- control -------------------------------------------------------------------------------------------------
    def start(self):
        self.threads = [threading.Thread(target=self._worker, args=(True,), name="gpu-worker", daemon=True)]
        self.threads += [threading.Thread(target=self._worker, args=(False,), name=f"cpu-worker-{n}", daemon=True)
                         for n in range(CPU_WORKERS)]
        for thread in self.threads:
            thread.start()

    def stop(self, wait=True, timeout=None):
        with self.cond:
            self.stopping = True
            self.cond.notify_all()
        if wait:
            for thread in self.threads:
                thread.join(timeout)

    def submit(self, type_, body: dict) -> int:
        if type_ not in self.handlers:
            raise ValueError(f"unknown job type {type_!r}")
        job_id = self.store.add(type_, body, self.handlers[type_][1])
        self.services.emit({"type": "job", "id": job_id, "state": "queued", "progress": 0, "job_type": type_, "body": body})
        with self.cond:
            self.cond.notify_all()
        return job_id

    def cancel(self, job_id) -> bool:
        job = self.store.get(job_id)
        if job is None:
            return False
        if job["state"] == "queued":
            self.store.update(job_id, state="cancelled", finished=time.time())
            self.services.emit({"type": "job", "id": job_id, "state": "cancelled", "progress": job["progress"]})
            return True
        if job["state"] == "running" and job_id in self.running:
            self.running[job_id].cancel()
            return True
        return False

    def reorder(self, ids):
        self.store.reorder(ids)
        self.services.emit({"type": "queue", "reordered": ids})

    def pause(self):
        self.paused = True
        self.services.emit({"type": "queue", "paused": True})

    def resume(self):
        self.paused = False
        with self.cond:
            self.cond.notify_all()
        self.services.emit({"type": "queue", "paused": False})

    def busy(self) -> bool:
        return bool(self.running)

    def idle(self) -> bool:
        return not self.running and not self.store.list("queued,running", 1)

    def wait_idle(self, timeout=None) -> bool:
        deadline = None if timeout is None else time.time() + timeout
        while self.running:
            if deadline and time.time() > deadline:
                return False
            time.sleep(0.1)
        return True

    # --- workers -------------------------------------------------------------------------------------------------
    def _worker(self, gpu: bool):
        while True:
            with self.cond:
                job = None
                while not self.stopping:
                    if not self.paused:
                        job = self.store.next_queued(gpu)
                        if job and self.store.claim(job["id"]):
                            break
                        job = None
                    self.cond.wait(1.0)
                if self.stopping:
                    return
            self._run(job)
            if gpu and self.services.settings["gpu_mode"] == "restricted":
                time.sleep(float(self.services.settings["rest_seconds"] or 0))

    def _run(self, job):
        ctx = JobContext(self, job)
        self.running[job["id"]] = ctx
        self.services.emit({"type": "job", "id": job["id"], "state": "running", "progress": 0, "job_type": job["type"]})
        self.services.health_changed()
        handler = self.handlers[job["type"]][0]
        state, result, error = "done", None, None
        try:
            result = handler(ctx, job["body"])
            if ctx.cancelled:
                state = "cancelled"
        except Cancelled:
            state = "cancelled"
        except OSError as exc:
            state, error = "failed", str(exc)
            if exc.errno == errno.ENOSPC:
                error = "the disk is full: free some space, then resume the queue"
                self.pause()
            ctx.log(f"FAILED: {error}")
        except Exception as exc:
            state, error = "failed", f"{type(exc).__name__}: {exc}"
            ctx.log("FAILED: " + error)
            for line in traceback.format_exc().splitlines():
                ctx.log(line)
        finally:
            self.running.pop(job["id"], None)
        progress = 1.0 if state == "done" else self.store.get(job["id"])["progress"]
        self.store.update(job["id"], state=state, finished=time.time(), result=result, error=error, progress=progress)
        self.services.emit({"type": "job", "id": job["id"], "state": state, "progress": progress, "message": error,
                            "job_type": job["type"], "result": result})
        self.services.health_changed()
        if self.idle():
            self.services.emit({"type": "queue", "idle": True})
