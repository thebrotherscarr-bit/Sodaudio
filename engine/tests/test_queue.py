"""The queue with the fake generator: generation, post-processing, resume after a crash, cancel, reorder, pause."""
import time

import soundfile

from engine import post
from engine.jobs import Queue

from .conftest import wait_for, wait_idle


def candidates(svc, category, cue):
    folder = svc.project.candidate_dir(category)
    return sorted(p.name for p in folder.glob(f"{cue}__c*.wav")) if folder.exists() else []


def test_two_takes_become_levelled_candidates(services):
    events = []
    services.emit = lambda e: events.append(e)
    services.queue.submit("generate", {"cues": ["mus_frozen_day"], "takes": 2, "seconds": 12})
    wait_idle(services)
    assert candidates(services, "music", "mus_frozen_day") == ["mus_frozen_day__c1.wav", "mus_frozen_day__c2.wav"]
    for name in candidates(services, "music", "mus_frozen_day"):
        audio, rate = soundfile.read(str(services.project.candidate_dir("music") / name), always_2d=True)
        assert rate == 44100 and audio.shape[1] == 2
        assert abs(post.loudness(audio, rate) - (-16 - 4)) < 1.0  # project -16 LUFS, cue offset -4 dB
    progress = [e["progress"] for e in events if e.get("type") == "job" and e.get("state") == "running" and e.get("job_type") == "generate"]
    assert progress and max(progress) > 0.4
    assert any(e.get("type") == "take" and e["kind"] == "raw" for e in events)


def test_top_up_skips_existing_takes_and_locked_cues(services):
    services.queue.submit("generate", {"cues": ["use_broadsword"], "takes": 1})
    wait_idle(services)
    job = services.queue.submit("generate", {"cues": ["use_broadsword", "mus_title"], "takes": 2})
    wait_idle(services)
    result = services.queue.store.get(job)["result"]
    assert result["made"] == ["use_broadsword__c2.wav"]  # c1 existed; mus_title is locked


def test_a_running_job_resumes_after_a_restart(services):
    """A job left running by a crash goes back to the queue and finishes; takes made before the crash are kept."""
    project = services.project
    services.queue.pause()
    job = services.queue.submit("generate", {"cues": ["use_shortsword"], "takes": 3})
    services.queue.store.update(job, state="running", started=time.time())
    # Take 1 was finished before the "crash".
    from engine.generators import fake
    folder = project.raw_dir("weapons")
    folder.mkdir(parents=True, exist_ok=True)
    fake.effect(folder / "use_shortsword__c1.wav", 0.5, 1, 0)
    services.queue.stop(wait=True, timeout=10)
    services.queue.store.close()
    services.queue = Queue(services, project.ws("jobs.sqlite"), services.handlers)
    assert services.queue.store.get(job)["state"] == "queued"
    services.queue.start()
    wait_idle(services)
    finished = services.queue.store.get(job)
    assert finished["state"] == "done"
    assert finished["result"]["made"] == ["use_shortsword__c2.wav", "use_shortsword__c3.wav"]


def test_cancel_a_running_job(services):
    services.settings.data["fake_delay"] = 0.5
    job = services.queue.submit("generate", {"cues": ["mus_forest_day"], "takes": 3, "seconds": 5})
    wait_for(lambda: services.queue.store.get(job)["state"] == "running")
    assert services.queue.cancel(job)
    wait_for(lambda: services.queue.store.get(job)["state"] == "cancelled", 20)


def test_pause_reorder_and_cancel_queued(services):
    services.queue.pause()
    a = services.queue.submit("map", {"file": "nothing-a"})
    b = services.queue.submit("map", {"file": "nothing-b"})
    c = services.queue.submit("map", {"file": "nothing-c"})
    services.queue.reorder([c, a, b])
    order = [j["id"] for j in services.queue.store.list("queued")]
    assert order == [c, a, b]
    assert services.queue.cancel(a)
    assert services.queue.store.get(a)["state"] == "cancelled"
    services.queue.resume()
    wait_idle(services)
    assert services.queue.store.get(c)["state"] == "failed"  # no such file: failures are recorded, not raised
    assert "no audio file" in services.queue.store.get(c)["error"]


def test_effects_batch_with_progress(services):
    events = []
    services.emit = lambda e: events.append(e)
    cues = [c["id"] for c in services.project.cues() if c["category"] == "ui"][:10]
    job = services.queue.submit("generate", {"cues": cues, "takes": 1})
    wait_idle(services)
    assert len(services.queue.store.get(job)["result"]["made"]) == 10
    assert len([e for e in events if e.get("type") == "take" and e["kind"] == "candidate"]) == 10
    assert len(services.queue.store.tracks()) == 10


def test_variations_appear_as_candidates(services):
    services.queue.submit("generate", {"cues": ["use_broadsword"], "takes": 1})
    wait_idle(services)
    job = services.queue.submit("variations", {"candidate": "use_broadsword__c1.wav", "kinds": ["reverse", "pitch"], "semitones": [-5, 5]})
    wait_idle(services)
    made = services.queue.store.get(job)["result"]["candidates"]
    assert set(made) == {"use_broadsword__c1_rev.wav", "use_broadsword__c1_down5.wav", "use_broadsword__c1_down5s.wav",
                         "use_broadsword__c1_up5.wav", "use_broadsword__c1_up5s.wav"}
    folder = services.project.candidate_dir("weapons")
    base = soundfile.info(str(folder / "use_broadsword__c1.wav")).frames
    assert soundfile.info(str(folder / "use_broadsword__c1_up5.wav")).frames == base
    assert soundfile.info(str(folder / "use_broadsword__c1_up5s.wav")).frames < base
