"""The GPU lock shared by SodaPixel and Sodaudio: one lock file, held for the whole of any GPU job, so a night is
art or audio, never both. Standard library only; this file is copied byte-identically between the two apps.

The lock is `gpu.lock` in `$SODA_LOCK_DIR` (else `the Soda folder in %LOCALAPPDATA%`), created atomically with O_CREAT | O_EXCL and
holding JSON: owner, pid, pid_started (when that process was created), what and since. A lock is stale, and is
cleared, when its pid is no longer alive, when that pid now names a later process (pid_started differs: the pid was
reused), when it was written before the PC last started (a lock left by a crash or power loss, whose pid a process
of this boot may have been given), or when its file is unreadable. Waiting is cancellable and raises `engine.jobs.Cancelled` (imported lazily so this module stands alone).
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


BOOT_SLACK = 120.0  # a lock is from before the last start only when it is older than the boot time by this much


def boot_time() -> float | None:
    """When this PC last started, in seconds since the epoch, or None when it cannot be told."""
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        return time.time() - kernel32.GetTickCount64() / 1000.0  # the tick count runs from boot, sleep included
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError):
        pass
    return None


def process_started(pid) -> float | None:
    """When a running process was created, in seconds since the epoch, or None when it cannot be told. Together with
    the pid it names one process: a later process given the same pid has a later creation time."""
    if sys.platform != "win32":
        return None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime  # 100 ns since 1601
        return (ticks - 116444736000000000) / 10000000.0
    finally:
        kernel32.CloseHandle(handle)


def lock_alive(data: dict) -> bool:
    """Whether the process that wrote this lock content is still running."""
    pid = data.get("pid")
    if not pid_alive(pid):
        return False
    started = data.get("pid_started")
    if isinstance(started, (int, float)):
        now = process_started(pid)
        if now is not None:
            return abs(now - started) < 1.0  # otherwise the pid was reused by a later process
    since, boot = data.get("since"), boot_time()
    if isinstance(since, (int, float)) and boot is not None and since < boot - BOOT_SLACK:
        return False  # written before the PC last started: whatever has that pid now is not the writer
    return True


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
        """The live lock's content ({"owner", "pid", "pid_started", "what", "since"}), or None when free, unreadable, or
        left by a process that is gone (that lock is stale and is cleared by the next acquire)."""
        data = self._read()
        return data if data is not None and lock_alive(data) else None

    def held_by_me(self) -> bool:
        """Whether this process holds the lock under this owner name."""
        data = self.holder()
        return bool(data) and data.get("pid") == os.getpid() and data.get("owner") == self.owner

    def _stale(self) -> bool:
        """The lock file is there but its holder is gone (see lock_alive), or it is garbage that has outlived the grace
        period."""
        data = self._read()
        if data is not None:
            return not lock_alive(data)
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
        payload = json.dumps({"owner": self.owner, "pid": os.getpid(), "pid_started": process_started(os.getpid()),
                              "what": what, "since": time.time()})
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
