"""Job handlers: each pipeline step (PIPELINE.md) as a job type the queue runs. Handlers block and run in the queue's
worker threads; they report through the JobContext."""
import hashlib
import time
from pathlib import Path

from . import post as post_mod
from . import promote as promote_mod
from .analysis import loop as loop_mod
from .analysis import map as map_mod
from .analysis import recompose as recompose_mod
from .analysis import variations as variations_mod
from .importers import merge, run_importer
from .project import kind_of
from .util import cue_of, read_json, seed, take_number, write_json


# --- generate --------------------------------------------------------------------------------------------------------
def plan_takes(project, cue_ids, takes=None):
    """(cue, n, raw path) for every missing take c1..cN; locked cues get none."""
    items, skipped = [], []
    for cue_id in cue_ids:
        cue = project.cue(cue_id)
        if cue.get("locked"):
            skipped.append(cue_id)
            continue
        kind = kind_of(cue["category"])
        want = int(takes or cue.get("variants") or 1)
        folder = project.raw_dir(cue["category"])
        for n in range(1, want + 1):
            stem = f"{cue_id}__c{n}"
            if any((folder / (stem + ext)).exists() for ext in (".flac", ".wav")):
                continue
            items.append((cue, n, folder / (stem + (".flac" if kind == "music" else ".wav"))))
    return items, skipped


def _eta(store, kind, done_times, remaining):
    if done_times:
        per = sum(done_times) / len(done_times)
    else:
        history = [t["seconds"] for t in store.tracks(50) if t["kind"] == kind]
        per = sum(history) / len(history) if history else None
    return round(per * remaining) if per else None


def run_generate(ctx, body):
    project, services = ctx.project, ctx.services
    items, skipped = plan_takes(project, body["cues"], body.get("takes"))
    for cue_id in skipped:
        ctx.log(f"{cue_id} is locked: no new takes")
    if not items:
        ctx.log("nothing to generate: every take already exists")
        return {"made": [], "failed": []}
    seconds_override = body.get("seconds")
    fake = services.settings["generator"] == "fake"
    music = [i for i in items if kind_of(i[0]["category"]) == "music"]
    sounds = [i for i in items if kind_of(i[0]["category"]) != "music"]
    total = len(items)
    made, failed, times = [], [], []
    ctx.log(f"generating {len(music)} music takes and {len(sounds)} effect/ambience takes"
            f"{' (fake generator)' if fake else ''}")

    def finished(cue, path, started, kind):
        took = time.time() - started
        times.append(took)
        made.append(path.name)
        audio_seconds = float(seconds_override or cue.get("seconds") or 0)
        ctx.queue.store.add_track(ctx.id, cue["id"], kind, took, audio_seconds)
        services.emit({"type": "take", "cue": cue["id"], "name": path.name, "kind": "raw"})
        ctx.log(f"[{len(made) + len(failed)}/{total}] {path.name} ({took:.0f}s)")
        ctx.submit("post", {"takes": [path.name]})

    if music:
        ace = None
        if not fake:
            from .generators.acestep import AceStep
            services.supervisor.ensure_up(ctx.log)
            ace = AceStep(services.settings["comfy_url"])
            ctx.on_cancel(lambda: ace.interrupt())
        for cue, n, path in music:
            ctx.check()
            if ace is not None:
                services.supervisor.wait_for_gpu(ace.client_id, ctx.log, lambda: ctx.cancelled)
            path.parent.mkdir(parents=True, exist_ok=True)
            index = len(made) + len(failed)
            started = time.time()
            seconds = float(seconds_override or cue.get("seconds") or 120)

            def on_progress(step, of, node=None, index=index):
                remaining = total - index - step / max(of, 1)
                ctx.progress((index + step / max(of, 1)) / total, _eta(ctx.queue.store, "music", times, remaining),
                             f"{path.stem}: step {step}/{of}")
                services.emit({"type": "comfy", "state": "running", "step": step, "of": of, "job_id": ctx.id})

            for attempt in (1, 2):
                try:
                    partial = path.with_name(path.stem + ".part" + path.suffix)
                    if fake:
                        from .generators import fake as fake_gen
                        fake_gen.music(partial, seconds, cue.get("bpm") or 120, seed(cue["id"], n), on_progress,
                                       lambda: ctx.cancelled, float(services.settings.get("fake_delay", 0.05)))
                    else:
                        from .generators.acestep import music_workflow
                        graph = music_workflow(project.compose_prompt(cue), seconds=seconds, bpm=int(cue.get("bpm") or 120),
                                               key=cue.get("key") or "C minor", time_signature=str(cue.get("meter") or 4),
                                               seed=seed(cue["id"], n), lyrics=project.compose_plan(cue) or "[Instrumental]")
                        partial.write_bytes(ace.run(graph, on_progress, lambda: ctx.cancelled))
                    partial.replace(path)
                    finished(cue, path, started, "music")
                    if not fake:
                        services.supervisor.track_done(ctx.log)
                    break
                except InterruptedError:
                    ctx.check()
                    raise
                except Exception as error:  # noqa: BLE001 - a dropped connection gets one restart and retry
                    ctx.check()
                    ctx.log(f"{'retrying' if attempt == 1 else 'FAILED'} {path.name}: {str(error)[:300]}")
                    if attempt == 2:
                        failed.append(path.name)
                    elif not fake and not services.supervisor.probe():
                        if not services.supervisor.restart(ctx.log):
                            services.supervisor.ensure_up(ctx.log)
        if not fake:
            services.emit({"type": "comfy", "state": "idle", "step": 0, "of": 0})

    if sounds:
        ctx.check()
        by_out = {str(path): (cue, n, path) for cue, n, path in sounds}
        started = [time.time()]
        if fake:
            from .generators import fake as fake_gen
            for cue, n, path in sounds:
                ctx.check()
                path.parent.mkdir(parents=True, exist_ok=True)
                fake_gen.effect(path, float(seconds_override or cue.get("seconds") or 2), seed(cue["id"], n),
                                float(services.settings.get("fake_delay", 0.05)))
                finished(cue, path, started[0], kind_of(cue["category"]))
                started[0] = time.time()
                index = len(made) + len(failed)
                ctx.progress(index / total, _eta(ctx.queue.store, "sfx", times, total - index), path.stem)
        else:
            from .generators import moss
            services.supervisor.wait_for_gpu(None, ctx.log, lambda: ctx.cancelled)
            services.supervisor.free(ctx.log)
            jobs = [{"prompt": project.compose_prompt(cue), "seconds": float(seconds_override or cue.get("seconds") or 2),
                     "seed": seed(cue["id"], n), "out": str(path)} for cue, n, path in sounds]
            for job in jobs:
                Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)

            def on_done(out, took):
                cue, n, path = by_out[str(Path(out))] if str(Path(out)) in by_out else by_out[out]
                finished(cue, path, time.time() - float(took or 0), kind_of(cue["category"]))
                index = len(made) + len(failed)
                ctx.progress(index / total, _eta(ctx.queue.store, "sfx", times, total - index), path.stem)

            def on_failed(out, error):
                failed.append(Path(out).name)
                ctx.log(f"FAILED {Path(out).name}: {error}")

            code = moss.run_batch(services.settings, jobs, lambda line: ctx.log(line, "moss"), on_done, on_failed, ctx.on_cancel)
            ctx.check()
            if code != 0:
                raise RuntimeError(f"the MOSS worker exited with code {code}; see the log")
    if failed and not made:
        raise RuntimeError(f"every take failed: {', '.join(failed)}")
    return {"made": made, "failed": failed}


# --- post-process ----------------------------------------------------------------------------------------------------
def run_post(ctx, body):
    project, results = ctx.project, []
    names = body["takes"]
    for index, name in enumerate(names):
        ctx.check()
        cue = project.cue(cue_of(name))
        source = project.raw_dir(cue["category"]) / name
        if not source.exists():
            source = ctx.services.find_take(name)
        out = project.candidate_dir(cue["category"]) / (Path(name).stem + ".wav")
        target, peak = project.levels(cue)
        info = post_mod.process(source, out, kind_of(cue["category"]), bool(cue.get("loop")), target, peak)
        ctx.log(f"{name} -> candidates/{cue['category']}/{out.name} ({info['seconds']:.1f}s, {info['lufs']:.1f} LUFS)")
        ctx.services.emit({"type": "take", "cue": cue["id"], "name": out.name, "kind": "candidate"})
        results.append({"candidate": out.name, **info})
        cache = read_json(project.ws("loudness.json"), {}) or {}
        cache[f"{out.name}|{out.stat().st_mtime_ns}"] = round(info["lufs"], 1)
        write_json(project.ws("loudness.json"), cache)
        ctx.progress((index + 1) / len(names))
    return {"candidates": results}


# --- variations ------------------------------------------------------------------------------------------------------
def run_variations(ctx, body):
    project = ctx.project
    name = body["candidate"]
    cue = project.cue(cue_of(name))
    source = project.candidate_dir(cue["category"]) / (name if name.endswith(".wav") else name + ".wav")
    target, peak = project.levels(cue)
    made = variations_mod.make(source, source.parent, source.stem, body.get("kinds", ["reverse", "pitch"]),
                               body.get("semitones", [-5, 5]), body.get("stretch", True), target, peak)
    for made_name in made:
        ctx.log(f"made {made_name}")
        ctx.services.emit({"type": "take", "cue": cue["id"], "name": made_name, "kind": "candidate"})
    return {"candidates": made}


# --- map, loop, recompose --------------------------------------------------------------------------------------------
def map_key(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha1(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:10]
    return f"{path.stem}-{digest}"


def cached_map(services, ident):
    path = services.resolve_audio(ident)
    data = read_json(services.project.ws("maps", map_key(path) + ".json"))
    return path, data


def cue_tempo(services, path: Path):
    try:
        cue = services.project.cue(cue_of(path.name))
        return cue.get("bpm"), cue.get("meter") or 4
    except KeyError:
        return None, 4


def get_map(ctx_or_services, ident, bpm=None, log=None):
    services = getattr(ctx_or_services, "services", ctx_or_services)
    path, data = cached_map(services, ident)
    if data is None:
        asked, meter = cue_tempo(services, path)
        (log or services.log)(f"mapping {path.name}")
        data = map_mod.compute(path, bpm or asked, meter)
        data["name"] = path.name
        data["ident"] = ident
        write_json(services.project.ws("maps", map_key(path) + ".json"), data)
    return path, data


def run_map(ctx, body):
    path, data = get_map(ctx, body["file"], body.get("bpm"), ctx.log)
    ctx.log(f"{path.name}: {len(data['bars'])} bars, about {data['tempo']:.1f} BPM (asked {data.get('asked_bpm')}), {data['key']}")
    if body.get("png"):
        out = ctx.project.ws("maps", map_key(path) + ".png")
        map_mod.export_png(path, out, data.get("asked_bpm"), data.get("meter", 4))
        ctx.log(f"picture: {out}")
    return {"file": body["file"], "tempo": data["tempo"], "bars": len(data["bars"]), "key": data["key"]}


def _derived_name(folder: Path, stem: str, tag: str) -> Path:
    """<take>loop.wav, then <take>loop2.wav …: derived files never replace earlier ones."""
    n = 1
    while True:
        path = folder / f"{stem}{tag}{'' if n == 1 else n}.wav"
        if not path.exists():
            return path
        n += 1


def _take_source(ctx, take):
    path = ctx.services.resolve_audio(take)
    try:
        cue = ctx.project.cue(cue_of(path.name))
    except KeyError:
        cue = {"id": cue_of(path.name), "category": "music", "offset_db": 0}
    return path, cue


def run_loop(ctx, body):
    path, cue = _take_source(ctx, body["take"])
    _, data = get_map(ctx, body["take"], body.get("bpm"), ctx.log)
    tempo = float(body.get("bpm") or data["tempo"])
    out = _derived_name(ctx.project.candidate_dir(cue["category"]), path.stem, "loop")
    target, peak = ctx.project.levels(cue)
    include = body.get("include")
    info = loop_mod.find_loop(path, out, tempo, float(body.get("min", 30)), float(body.get("max", 45)), include,
                              data.get("meter", 4), data["bars"] if not body.get("bpm") else None, target, peak)
    ctx.log(f"{out.name}: {info['seconds']:.1f} s ({info['bars']} bars) from {info['start']:.2f} s, end-to-start match {info['match']:.2f}")
    ctx.services.emit({"type": "take", "cue": cue["id"], "name": out.name, "kind": "candidate"})
    return {"candidate": out.name, **info}


def run_recompose(ctx, body):
    path, cue = _take_source(ctx, body["take"])
    fade = body.get("fade")
    if fade is None:
        _, data = get_map(ctx, body["take"], None, ctx.log)
        fade = 60.0 / float(data["tempo"]) / 4  # a quarter beat at the measured tempo
    out = _derived_name(ctx.project.candidate_dir(cue["category"]), path.stem, "mix")
    target, peak = ctx.project.levels(cue)
    info = recompose_mod.recompose(path, out, body["pieces"], float(fade), body.get("ring"), body.get("overlay"), target, peak)
    ctx.log(f"{out.name}: {info['seconds']:.2f} s from {info['pieces']} pieces (joins {float(fade):.3f} s)")
    ctx.services.emit({"type": "take", "cue": cue["id"], "name": out.name, "kind": "candidate"})
    return {"candidate": out.name, **info}


# --- import ----------------------------------------------------------------------------------------------------------
def run_import(ctx, body):
    incoming = run_importer(ctx.project, body.get("run_command", True), ctx.log)
    result = merge(ctx.project, incoming)
    ctx.log(f"imported: {len(result['added'])} added, {len(result['changed'])} changed, {len(result['missing'])} missing")
    ctx.services.emit({"type": "cues", "changed": True})
    return result


def run_promote(ctx, body):
    if body.get("fill_gaps"):
        return {"installed": promote_mod.fill_gaps(ctx.project, ctx.log, ctx.services.emit)}
    return {"installed": promote_mod.promote(ctx.project, body["candidates"], body.get("mode", "add"), ctx.log, ctx.services.emit)}


def handlers():
    from .setup import run_setup
    return {
        "generate": (run_generate, True),
        "post": (run_post, False),
        "variations": (run_variations, False),
        "map": (run_map, False),
        "loop": (run_loop, False),
        "recompose": (run_recompose, False),
        "import": (run_import, False),
        "promote": (run_promote, False),
        "setup": (run_setup, True),
    }


__all__ = ["handlers", "plan_takes", "get_map", "cached_map", "take_number"]
