"""The GPU lock shared with SodaPixel: one holder at a time, stale locks cleared, waiting is cancellable."""
import json
import os
import subprocess
import sys
import threading
import time

import pytest

from engine import jobs
from engine.gpu_lock import GpuLock

from .conftest import wait_for, wait_idle


def test_second_owner_waits_until_release(tmp_path):
    a, b = GpuLock("A", tmp_path), GpuLock("B", tmp_path)
    a.acquire("art", lambda: False, lambda m: None)
    lines, done = [], threading.Event()

    def run():
        b.acquire("music", lambda: False, lines.append, poll=0.05)
        done.set()

    threading.Thread(target=run, daemon=True).start()
    time.sleep(0.5)
    assert not done.is_set()
    a.release()
    assert done.wait(2)
    assert b.holder()["owner"] == "B"
    waiting = [l for l in lines if "waiting for the GPU" in l]
    assert len(waiting) == 1 and "A" in waiting[0]
    b.release()


def test_stale_lock_is_cleared(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "SodaPixel", "pid": dead.pid}))
    lines = []
    lock = GpuLock("Sodaudio", tmp_path)
    lock.acquire("art", lambda: False, lines.append)
    assert any("stale" in l for l in lines)
    assert lock.holder()["owner"] == "Sodaudio"
    lock.release()


def test_garbled_lock_is_stale(tmp_path):
    (tmp_path / "gpu.lock").write_text("{not json")
    old = time.time() - 60
    os.utime(tmp_path / "gpu.lock", (old, old))
    lock = GpuLock("Sodaudio", tmp_path)
    lock.acquire("art", lambda: False, lambda m: None)
    lock.release()
    assert not (tmp_path / "gpu.lock").exists()


def test_cancel_while_waiting(tmp_path):
    a, b = GpuLock("A", tmp_path), GpuLock("B", tmp_path)
    a.acquire("art", lambda: False, lambda m: None)
    flag = {"stop": False}
    outcome = []

    def run():
        try:
            b.acquire("music", lambda: flag["stop"], lambda m: None, poll=0.05)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.2)
    flag["stop"] = True
    thread.join(2)
    assert len(outcome) == 1 and isinstance(outcome[0], jobs.Cancelled)
    a.release()


def test_reentrant_same_owner_and_pid_is_refused(tmp_path):
    lock = GpuLock("A", tmp_path)
    lock.acquire("art", lambda: False, lambda m: None)
    with pytest.raises(RuntimeError):
        lock.acquire("art", lambda: False, lambda m: None)
    lock.release()


def test_gpu_jobs_hold_the_lock(services):
    seen = []

    def gpu_job(ctx, body):
        seen.append(GpuLock("probe").holder())
        return {}

    services.handlers["gpu"] = (gpu_job, True)
    services.queue.submit("gpu", {})
    wait_idle(services)
    assert seen[0]["owner"] == "Sodaudio"
    assert not (GpuLock("probe").path).exists()


def test_young_empty_lock_is_not_cleared(tmp_path):
    (tmp_path / "gpu.lock").write_text("")
    lock = GpuLock("Sodaudio", tmp_path)
    flag = {"stop": False}
    outcome = []

    def run():
        try:
            lock.acquire("art", lambda: flag["stop"], lambda m: None, poll=0.05)
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    time.sleep(0.4)
    flag["stop"] = True
    thread.join(2)
    assert outcome and isinstance(outcome[0], jobs.Cancelled)
    assert (tmp_path / "gpu.lock").exists()


def test_two_clearers_on_one_dead_lock_yield_one_holder(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    for _ in range(15):
        (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "SodaPixel", "pid": dead.pid}))
        locks = [GpuLock("A", tmp_path), GpuLock("B", tmp_path)]
        flag = {"stop": False}
        got = []

        def run(lock):
            try:
                lock.acquire("x", lambda: flag["stop"], lambda m: None, poll=0.02)
                got.append(lock)
            except jobs.Cancelled:
                pass

        threads = [threading.Thread(target=run, args=(l,), daemon=True) for l in locks]
        for th in threads:
            th.start()
        time.sleep(0.4)
        flag["stop"] = True
        for th in threads:
            th.join(2)
        assert len(got) == 1
        assert got[0].holder()["owner"] == got[0].owner
        got[0].release()
        assert not (tmp_path / "gpu.lock").exists()


def test_lock_released_when_the_job_raises_or_is_cancelled(services):
    def boom(ctx, body):
        raise ValueError("no")

    def spin(ctx, body):
        while True:
            ctx.check()
            time.sleep(0.01)

    services.handlers["boom"] = (boom, True)
    services.handlers["spin"] = (spin, True)
    lock_file = services.queue.gpu_lock.path
    failed = services.queue.submit("boom", {})
    wait_idle(services)
    assert services.queue.store.get(failed)["state"] == "failed" and not lock_file.exists()
    job = services.queue.submit("spin", {})
    wait_for(lambda: lock_file.exists())
    services.queue.cancel(job)
    wait_for(lambda: services.queue.store.get(job)["state"] == "cancelled", 20)
    wait_for(lambda: not lock_file.exists(), 20)  # the run ends once nothing is left to start


def test_busy_clear_mutex_backs_off_and_is_cancellable(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "SodaPixel", "pid": dead.pid}))
    (tmp_path / "gpu.lock.clear").write_text("")  # a fresh mutex held by someone else
    start = time.time()
    with pytest.raises(jobs.Cancelled):
        GpuLock("A", tmp_path).acquire("x", lambda: time.time() - start > 0.3, lambda m: None, poll=5.0)
    assert time.time() - start < 1.5
    assert sorted(f.name for f in tmp_path.iterdir()) == ["gpu.lock", "gpu.lock.clear"]


def test_holder_ignores_a_lock_left_by_a_dead_pid(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "Sodaudio", "pid": dead.pid, "what": "x", "since": 0}))
    lock = GpuLock("SodaPixel", tmp_path)
    assert lock.holder() is None  # not reported as running
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "Sodaudio", "pid": os.getpid(), "what": "x", "since": time.time()}))
    assert lock.holder()["owner"] == "Sodaudio"  # a live pid still is


# --- staleness across reboots and pid reuse ----------------------------------------------------------------------------
def test_a_lock_from_a_reused_pid_is_stale(tmp_path):
    """The pid is alive, but it now names a later process than the one that wrote the lock."""
    from engine import gpu_lock
    started = gpu_lock.process_started(os.getpid())
    assert started is not None and started <= time.time()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "Other", "pid": os.getpid(), "pid_started": started - 3600,
                                                   "what": "x", "since": time.time()}))
    lock = GpuLock("Mine", tmp_path)
    assert lock.holder() is None
    lock.acquire("x", lambda: False, lambda m: None, poll=0.05)
    assert lock.holder()["owner"] == "Mine"
    lock.release()


def test_a_lock_written_before_the_last_boot_is_stale(tmp_path):
    """A lock left by a crash or power loss, in the format without pid_started: its pid may be any process now."""
    from engine import gpu_lock
    boot = gpu_lock.boot_time()
    assert boot is not None and boot < time.time()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "Other", "pid": os.getpid(), "what": "x", "since": boot - 3600}))
    lock = GpuLock("Mine", tmp_path)
    assert lock.holder() is None
    lock.acquire("x", lambda: False, lambda m: None, poll=0.05)
    assert lock.holder()["owner"] == "Mine"
    lock.release()


def test_a_lock_from_this_boot_and_process_is_live(tmp_path):
    from engine import gpu_lock
    a = GpuLock("A", tmp_path)
    a.acquire("x", lambda: False, lambda m: None)
    written = json.loads((tmp_path / "gpu.lock").read_text())
    assert written["pid_started"] == pytest.approx(gpu_lock.process_started(os.getpid()))
    assert GpuLock("B", tmp_path).holder()["owner"] == "A" and a.held_by_me() and not GpuLock("B", tmp_path).held_by_me()
    a.release()
    (tmp_path / "gpu.lock").write_text(json.dumps({"owner": "Other", "pid": os.getpid(), "what": "x", "since": time.time()}))
    assert GpuLock("B", tmp_path).holder()["owner"] == "Other"  # the older format, written this boot: still live


# --- ComfyUI across the lock hand-off ----------------------------------------------------------------------------------
def dummy_comfy():
    """Stands in for a ComfyUI this engine started: a process that only waits."""
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


def test_a_gpu_run_holds_the_lock_and_stops_our_comfy_before_releasing(services):
    """Back-to-back GPU jobs share one hold of the lock; when none is left, the ComfyUI this engine started is stopped
    while the lock is still held, and only then is the lock released."""
    own = services.queue.gpu_lock.owner
    seen, at_stop = [], []
    services.supervisor.process = dummy_comfy()
    real_stop = services.supervisor.stop

    def stop():
        at_stop.append(GpuLock("probe").holder())
        real_stop()
    services.supervisor.stop = stop

    def gpu_job(ctx, body):
        seen.append(GpuLock("probe").holder())
        return {}

    services.queue.pause()
    services.handlers["gpu"] = (gpu_job, True)
    services.queue.submit("gpu", {})
    services.queue.submit("gpu", {})
    services.queue.resume()
    wait_idle(services)
    assert len(seen) == 2 and seen[0]["owner"] == own and seen[0]["since"] == seen[1]["since"]  # one hold for the run
    assert len(at_stop) == 1 and at_stop[0]["owner"] == own  # stopped before the lock went
    assert services.supervisor.process is None and not services.supervisor.ours
    assert not GpuLock("probe").path.exists()


def test_an_attached_comfy_is_never_stopped(services):
    stops = []
    services.supervisor.stop = lambda: stops.append(1)
    services.handlers["gpu"] = (lambda ctx, body: {}, True)
    services.queue.submit("gpu", {})
    wait_idle(services)
    assert stops == [] and not GpuLock("probe").path.exists()


def test_quitting_leaves_our_comfy_to_the_app_holding_the_gpu(services):
    other = GpuLock("Other")  # the other app holds the lock (same pid here, another owner)
    other.acquire("its run", lambda: False, lambda m: None)
    process = dummy_comfy()
    services.supervisor.process = process
    try:
        services.supervisor.shutdown()
        assert process.poll() is None  # still running for the holder
        assert services.supervisor.process is None  # handed over: no longer ours to restart or stop
    finally:
        other.release()
        process.kill()
        process.wait(10)


def test_quitting_stops_our_comfy_when_no_one_else_holds_the_gpu(services):
    process = dummy_comfy()
    services.supervisor.process = process
    services.supervisor.shutdown()
    process.wait(20)
    assert process.poll() is not None
