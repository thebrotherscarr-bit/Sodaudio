"""The GPU lock shared by SodaPixel and Sodaudio: one lock file, held for the whole of any GPU job, so a night is
art or audio, never both. Standard library only; this file is copied byte-identically between the two apps.

The lock is `gpu.lock` in `$SODA_LOCK_DIR` (else `the Soda folder in %LOCALAPPDATA%`), created atomically with O_CREAT | O_EXCL and
holding JSON: owner, pid, what and since. A lock whose pid is no longer alive, or whose file is unreadable, is stale
and is cleared. Waiting is cancellable and raises `engine.jobs.Cancelled` (imported lazily so this module stands alone).
"""
import contextlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Callable


def default_lock_dir() -> Path:
    env = os.environ.get("SODA_LOCK_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Soda"


GRACE = 3.0  # an unreadable lock file is only stale once it is this many seconds old
CLEAR_MUTEX_EXPIRY = 30.0  # a crashed clearer's mutex file is broken after this long


def pid_alive(pid) -> bool:
    """Whether a process is running. Never os.kill on Windows: it terminates the process."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: the process exists, we just may not open it
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # signal 0 only probes on POSIX
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class GpuLock:
    def __init__(self, owner: str, lock_dir: Path | None = None):
        self.owner = owner
        self.dir = Path(lock_dir) if lock_dir else default_lock_dir()
        self.path = self.dir / "gpu.lock"
        self._held = False

    def _read(self) -> dict | None:
        """The lock file's content as written, whether or not its holder is still alive; None when absent or unreadable."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def holder(self) -> dict | None:
        """The live lock's content ({"owner", "pid", "what", "since"}), or None when free, unreadable, or left by a dead pid
        (that lock is stale and is cleared by the next acquire)."""
        data = self._read()
        return data if data is not None and pid_alive(data.get("pid")) else None

    def _stale(self) -> bool:
        """The lock file is there but its holder is gone: a dead pid, or garbage that has outlived the grace period."""
        data = self._read()
        if data is not None:
            return not pid_alive(data.get("pid"))
        try:
            return time.time() - self.path.stat().st_mtime > GRACE
        except OSError:
            return False  # vanished: not stale, the next create attempt will simply win

    def _clear_stale(self, log) -> bool:
        """Remove a stale lock under a short-lived mutex file. Only clearers take the mutex and only they remove a dead
        holder's file, so the re-read below cannot be followed by anyone replacing it: a live lock is never deleted.
        Returns False when another clearer holds the mutex (the caller backs off), True when this call did the check."""
        mutex = self.dir / "gpu.lock.clear"
        try:
            fd = os.open(str(mutex), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - mutex.stat().st_mtime > CLEAR_MUTEX_EXPIRY:
                    mutex.unlink()
            return False
        try:
            os.close(fd)
            other = self._read()
            if self._stale():
                log(f"clearing a stale GPU lock left by {(other or {}).get('owner', 'an unknown owner')}")
                with contextlib.suppress(OSError):
                    self.path.unlink()
            return True
        finally:
            with contextlib.suppress(OSError):
                mutex.unlink()

    def _publish(self, payload: str) -> bool:
        """Create the lock with its content already in place: write a private temp file, then hard-link it to the lock
        name (which fails if the lock exists). A waiter can never see a half-written lock."""
        temp = self.dir / f"gpu.lock.{uuid.uuid4().hex}.tmp"
        try:
            temp.write_text(payload, encoding="utf-8")
            os.link(str(temp), str(self.path))
            return True
        except FileExistsError:
            return False
        finally:
            with contextlib.suppress(OSError):
                temp.unlink()

    def acquire(self, what: str, cancelled: Callable[[], bool], log: Callable[[str], None], poll: float = 5.0) -> None:
        from engine.jobs import Cancelled  # lazy: keeps this module standalone
        if self._held:
            raise RuntimeError("this GpuLock is already held")
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"owner": self.owner, "pid": os.getpid(), "what": what, "since": time.time()})
        announced = False
        while True:
            if not self.path.exists() and self._publish(payload):  # no temp file is made while a lock is plainly there
                self._held = True
                return
            other = self._read()
            if other and other.get("pid") == os.getpid() and other.get("owner") == self.owner:
                raise RuntimeError("the GPU lock is already held by this process")
            wait = poll
            if self._stale():
                if self._clear_stale(log):
                    continue
                wait = min(poll, 0.25)  # another clearer holds the mutex: back off, still cancellable
            elif other and not announced:
                announced = True
                log(f"waiting for the GPU: {other.get('owner')} is running {other.get('what')}")
            deadline = time.time() + wait
            while True:
                if cancelled():
                    raise Cancelled()
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                time.sleep(min(0.05, remaining))

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        mine = self._read()
        if mine is None or (mine.get("owner") == self.owner and mine.get("pid") == os.getpid()):
            with contextlib.suppress(OSError):
                self.path.unlink()

    @contextlib.contextmanager
    def held(self, what: str, cancelled: Callable[[], bool], log: Callable[[str], None]):
        self.acquire(what, cancelled, log)
        try:
            yield self
        finally:
            self.release()
