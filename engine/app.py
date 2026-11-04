"""The engine's HTTP and WebSocket API (ENGINE-API.md) under /api, and the built UI at /. Listens on 127.0.0.1 only
and refuses requests from any page but its own.

    python -m engine.app [--port 8770]
"""
import argparse
import asyncio
import json
import os
import string
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Body, FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import VERSION, agent_tools, catalog, pipeline, promote, setup
from .agent import AgentHost
from .analysis import map as map_mod
from .jobs import Queue
from .project import Project, kind_of, rel
from .services import Services
from .settings import Settings, home
from .util import AUDIO_EXTS, read_json, write_json

HERE = Path(__file__).resolve().parent
UI_DIST = HERE.parent / "ui" / "dist"
DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def create_app(services: Services | None = None, port=8770, open_last=True) -> FastAPI:
    services = services or Services(Settings(), pipeline.handlers())
    if not services.handlers:
        services.handlers = pipeline.handlers()
    services.agent = AgentHost(services)
    state = {"server": None}

    @asynccontextmanager
    async def lifespan(app):
        services.events.attach(asyncio.get_running_loop())
        if services.queue is None:
            services.queue = Queue(services, home() / "jobs.sqlite", services.handlers)
            services.queue.start()
        services.supervisor.watch()
        if open_last and services.project is None:
            for path in services.settings["recent_projects"]:
                if Path(path).exists():
                    try:
                        services.open_project(path)
                    except Exception as error:  # a broken recent project must not stop the engine starting
                        services.log(f"could not reopen {path}: {error}")
                    break
        services.log(f"engine {VERSION} on http://127.0.0.1:{port}")
        yield
        services.events.close_all()
        await services.agent.shutdown()
        services.shutdown()

    app = FastAPI(title="Sodaudio engine", version=VERSION, lifespan=lifespan)
    app.state.services = services
    app.state.holder = state
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}", *DEV_ORIGINS}
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}", "127.0.0.1", "localhost", "testserver"}

    def origin_ok(origin, host):
        if host and host not in hosts:
            return False  # DNS rebinding: a page on another name pointed at 127.0.0.1
        return not origin or origin in allowed or origin.startswith("app://")

    @app.middleware("http")
    async def check_origin(request: Request, call_next):
        if not origin_ok(request.headers.get("origin"), request.headers.get("host")):
            return JSONResponse({"detail": "this engine only answers its own pages"}, status_code=403)
        return await call_next(request)

    api = APIRouter(prefix="/api")

    def project() -> Project:
        if not services.project:
            raise HTTPException(409, "no project is open")
        return services.project

    def queue() -> Queue:
        return services.queue

    # --- health and settings ---------------------------------------------------------------------------------------
    @app.get("/health")
    @api.get("/health")
    def health():
        return services.health()

    @api.get("/settings")
    def get_settings():
        return services.settings.public()

    @api.put("/settings")
    def put_settings(changes: dict = Body(...)):
        if "api_key" in changes:
            services.settings.set_api_key(changes.pop("api_key") or None)
        if "agent_key" in changes:
            services.settings.set_agent_key(changes.pop("agent_key") or None)
        if "gpu_mode" in changes and changes["gpu_mode"] not in ("focused", "restricted"):
            raise HTTPException(400, "gpu_mode is focused or restricted")
        result = services.settings.update(changes)
        services.health_changed()
        return result

    @api.get("/setup/check")
    def setup_check():
        return setup.check(services)

    @api.post("/setup/run")
    def setup_run(body: dict = Body(default={})):
        return {"ids": [queue().submit("setup", {"steps": body.get("steps")})]}

    @api.post("/setup/adopt")
    def setup_adopt(body: dict = Body(default={})):
        """Use a game's own ComfyUI, start script and MOSS setup (Rimtera's tools/), found from its folder or the open
        project. Returns what was found; with apply, saves it."""
        roots = [Path(body["root"])] if body.get("root") else setup.game_roots(services)
        found = {}
        for root in roots:
            found = setup.adopt_game_tools(root)
            if found:
                break
        if body.get("apply") and found:
            services.settings.update(found)
        return {"found": found, "root": str(root) if found else None}

    @api.post("/setup/point")
    def setup_point(body: dict = Body(...)):
        """Point at existing pieces (SETUP.md, "Adopting an existing install")."""
        keys = ("comfy_path", "comfy_python", "comfy_extra_model_paths", "comfy_extra_args", "comfy_start_script", "moss_python",
                "moss_model_dir", "moss_code_dir", "comfy_url", "comfy_models_dirs")
        return services.settings.update({k: v for k, v in body.items() if k in keys})

    # --- projects ------------------------------------------------------------------------------------------------
    @api.get("/projects")
    def projects():
        result = []
        for path in services.settings["recent_projects"]:
            item = {"path": path, "exists": Path(path).exists()}
            if item["exists"]:
                try:
                    proj = services.project if services.project and str(services.project.path) == path else Project.open(path)
                    rows = proj.status(proj.cues(include_missing=False))
                    item.update(name=proj.data.get("name"), cues=len(rows), no_sound=sum(1 for r in rows if not r["installed"]),
                                open=proj is services.project)
                except Exception as error:
                    item["error"] = str(error)
            result.append(item)
        return result

    @api.post("/projects/open")
    def open_project(body: dict = Body(...)):
        try:
            proj = services.open_project(body["path"], create=bool(body.get("create")), name=body.get("name", ""))
        except FileNotFoundError as error:
            raise HTTPException(404, str(error))
        except (RuntimeError, ValueError) as error:
            raise HTTPException(409, str(error))
        if body.get("create") and body.get("importer"):
            proj.edit_project({"importer": body["importer"]})
        services.emit({"type": "project", "opened": str(proj.path)})
        return proj.summary()

    @api.get("/project")
    def get_project():
        return project().summary()

    @api.patch("/project")
    def patch_project(changes: dict = Body(...)):
        result = project().edit_project(changes)
        services.emit({"type": "project", "changed": sorted(changes)})
        return result

    @api.post("/project/import")
    async def project_import(body: dict = Body(default={})):
        """Runs the importer now and returns added, changed and missing cue ids."""
        proj = project()
        from .importers import merge, run_importer
        incoming = await run_in_threadpool(run_importer, proj, body.get("run_command", True), services.log)
        result = merge(proj, incoming)
        services.emit({"type": "cues", "changed": True})
        return result

    @api.post("/project/decompose")
    def project_decompose():
        changed = project().decompose()
        services.emit({"type": "cues", "changed": changed})
        return {"changed": changed}

    @api.get("/project/history")
    def project_history():
        return project().history()

    @api.post("/project/undo")
    def project_undo():
        entry = project().undo()
        services.emit({"type": "cues", "changed": True})
        return {"undone": entry}

    # --- cues ----------------------------------------------------------------------------------------------------
    @api.get("/cues")
    def list_cues(category: str = "", q: str = "", status: str = "", missing: bool = False):
        proj = project()
        rows = proj.status(proj.cues(include_missing=missing))
        if category:
            rows = [r for r in rows if r["category"] == category or r["kind"] == category]
        if q:
            ql = q.lower()
            rows = [r for r in rows if ql in r["id"].lower() or ql in (r.get("trigger") or "").lower()
                    or ql in (r.get("card") or "").lower()]
        if status:
            rows = [r for r in rows if agent_tools._status_filter(r, status)]
        return rows

    def cue_detail(proj, cue):
        return {**proj.status([cue])[0], "prompt_final": proj.compose_prompt(cue), "plan_final": proj.compose_plan(cue),
                "shape": proj.shape_for(cue) if kind_of(cue["category"]) == "music" and proj.data.get("chant") else None}

    @api.get("/cues/{cue_id}")
    def get_cue(cue_id: str):
        proj = project()
        try:
            return cue_detail(proj, proj.cue(cue_id))
        except KeyError:
            raise HTTPException(404, f"no cue {cue_id}")

    @api.patch("/cues/{cue_id}")
    def patch_cue(cue_id: str, changes: dict = Body(...)):
        proj = project()
        try:
            cue = proj.edit_cue(cue_id, changes)
        except KeyError:
            raise HTTPException(404, f"no cue {cue_id}")
        services.emit({"type": "cues", "changed": [cue_id]})
        return cue_detail(proj, cue)

    @api.post("/cues")
    def add_cue(fields: dict = Body(...)):
        try:
            cue = project().add_cue({**fields})
        except ValueError as error:
            raise HTTPException(400, str(error))
        services.emit({"type": "cues", "changed": [cue["id"]]})
        return cue_detail(project(), cue)

    @api.post("/preview")
    def preview(body: dict = Body(...)):
        """The final prompt and plan for unsaved edits: a cue's fields, or the style with three sample cues."""
        proj = project()
        clone = Project(proj.path, {**proj.data, **{k: v for k, v in body.get("project", {}).items()
                                                    if k in ("style", "templates", "chant")}})
        if body.get("cue"):
            base = proj.cue(body["cue"]["id"]) if proj.has(body["cue"].get("id", "")) else {}
            cue = {**base, **body["cue"]}
            return {"prompt_final": clone.compose_prompt(cue), "plan_final": clone.compose_plan(cue),
                    "shape": clone.shape_for(cue) if kind_of(cue.get("category", "sfx")) == "music" and clone.data.get("chant") else None}
        samples, seen = [], set()
        music = [c for c in proj.cues(include_missing=False) if kind_of(c["category"]) == "music"]
        for cue in music:
            shape = clone.shape_for(cue) if clone.data.get("chant") else "plain"
            if shape in seen:
                continue
            seen.add(shape)
            # Show the composed text: a cue with its own prompt would hide the style being previewed.
            shown = {**cue, "prompt": None} if cue.get("card") else cue
            samples.append({"id": cue["id"], "shape": shape, "prompt_final": clone.compose_prompt(shown), "plan_final": clone.compose_plan(cue)})
        return {"samples": samples[:3]}

    # --- takes, files, installs ----------------------------------------------------------------------------------
    @api.get("/cues/{cue_id}/takes")
    def cue_takes(cue_id: str):
        try:
            return catalog.takes_for(project(), cue_id)
        except KeyError:
            raise HTTPException(404, f"no cue {cue_id}")

    @api.get("/audition")
    def audition(category: str = "", music_only: bool = False, since: float = 0):
        proj = project()
        rows = proj.status(proj.cues(include_missing=False))
        groups = []
        for row in rows:
            if row["candidates"] == 0 or (category and row["category"] != category) or (music_only and row["kind"] != "music"):
                continue
            takes = catalog.takes_for(proj, row["id"], loudness=False)
            cands = [c for c in takes["candidates"] if not c["rejected"] and c["modified"] >= since]
            if cands:
                groups.append({"cue": {k: row.get(k) for k in ("id", "category", "kind", "trigger", "loop", "locked", "variants", "notes")},
                               "installed": takes["installed"], "candidates": cands})
        seen = read_json(proj.ws("state.json"), {}) or {}
        return {"groups": groups, "last_visit": seen.get("audition_seen", 0)}

    @api.post("/audition/seen")
    def audition_seen():
        proj = project()
        state_ = read_json(proj.ws("state.json"), {}) or {}
        state_["audition_seen"] = time.time()
        write_json(proj.ws("state.json"), state_)
        return state_

    @api.post("/candidates/reject")
    def reject(body: dict = Body(...)):
        proj = project()
        rejected = set(read_json(proj.ws("rejected.json"), []) or [])
        (rejected.add if body.get("rejected", True) else rejected.discard)(body["name"])
        write_json(proj.ws("rejected.json"), sorted(rejected))
        return {"rejected": sorted(rejected)}

    @api.get("/loudness")
    def loudness(name: str):
        proj = project()
        path = services.find_take(name)
        if path is None:
            raise HTTPException(404, name)
        return {"name": name, "lufs": catalog._loudness(proj, path)}

    def serve(path: Path, base: Path):
        path = path.resolve()
        if base not in path.parents or not path.is_file() or path.suffix.lower() not in AUDIO_EXTS + (".png", ".json"):
            raise HTTPException(404, "no such file")
        return FileResponse(path)

    @api.get("/files/{path:path}")
    def files(path: str):
        return serve(project().workspace / path, project().workspace)

    @api.get("/game/{path:path}")
    def game_files(path: str):
        return serve(project().root / path, project().root)

    @api.get("/abs")
    def abs_file(path: str):
        """A file anywhere on disk (Electron's dropped files), audio only."""
        p = Path(path)
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS:
            raise HTTPException(404, "no such audio file")
        return FileResponse(p)

    @api.get("/installed/{cue_id}")
    def installed(cue_id: str):
        proj = project()
        cue = proj.cue(cue_id)
        return [rel(p, proj.root) for p in proj.installed(cue_id, cue["category"])]

    @api.post("/promote")
    async def do_promote(body: dict = Body(...)):
        proj = project()
        result = await run_in_threadpool(promote.promote, proj, body["candidates"], body.get("mode", "add"), services.log, services.emit)
        return {"installed": result}

    @api.post("/promote/fill-gaps")
    async def do_fill_gaps():
        return {"installed": await run_in_threadpool(promote.fill_gaps, project(), services.log, services.emit)}

    # --- jobs ----------------------------------------------------------------------------------------------------
    @api.post("/jobs")
    def add_jobs(body: dict = Body(...)):
        type_ = body.pop("type", None)
        if type_ not in services.handlers:
            raise HTTPException(400, f"unknown job type {type_!r}")
        if type_ != "setup":
            project()
        if type_ == "generate":
            items, locked = pipeline.plan_takes(project(), body.get("cues", []), body.get("takes"))
            job = queue().submit(type_, body)
            return {"ids": [job], "takes": [p.name for _, _, p in items], "locked": locked}
        return {"ids": [queue().submit(type_, body)]}

    @api.get("/jobs")
    def list_jobs(state: str = ""):
        return {"jobs": queue().store.list(state or None), "paused": queue().paused, "mode": services.settings["gpu_mode"]}

    @api.get("/jobs/{job_id}")
    def get_job(job_id: int):
        job = queue().store.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        log_path = queue().log_dir / f"{job_id}.log"
        job["log"] = log_path.read_text(encoding="utf-8", errors="replace")[-200000:] if log_path.exists() else ""
        return job

    @api.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: int):
        return {"ok": queue().cancel(job_id)}

    @api.post("/jobs/reorder")
    def reorder(body: dict = Body(...)):
        queue().reorder(body["ids"])
        return {"ok": True}

    @api.post("/queue/pause")
    def pause():
        queue().pause()
        services.health_changed()
        return {"paused": True}

    @api.post("/queue/resume")
    def resume():
        queue().resume()
        services.health_changed()
        return {"paused": False}

    @api.post("/queue/mode")
    def mode(body: dict = Body(...)):
        if body.get("mode") not in ("focused", "restricted"):
            raise HTTPException(400, "mode is focused or restricted")
        services.settings.update({"gpu_mode": body["mode"]})
        services.health_changed()
        return {"mode": body["mode"]}

    @api.get("/stats/tracks")
    def track_stats():
        return queue().store.tracks()

    @api.get("/logs")
    def logs(source: str = "engine", lines: int = 500):
        return {"lines": list(services.events.recent.get(source, []))[-lines:]}

    # --- maps ----------------------------------------------------------------------------------------------------
    @api.get("/maps")
    def get_map(file: str):
        try:
            path, data = pipeline.cached_map(services, file)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(404, str(error))
        if data is not None:
            return data
        pending = [j for j in queue().store.list("queued,running") if j["type"] == "map" and j["body"].get("file") == file]
        job = pending[0]["id"] if pending else queue().submit("map", {"file": file})
        return JSONResponse({"job": job, "file": file}, status_code=202)

    @api.get("/maps/png")
    def map_png(file: str):
        path = services.resolve_audio(file)
        png = project().ws("maps", pipeline.map_key(path) + ".png")
        if png.exists():
            return FileResponse(png, filename=path.stem + ".map.png")
        job = queue().submit("map", {"file": file, "png": True})
        return JSONResponse({"job": job}, status_code=202)

    @api.get("/maps/summary")
    def map_summary(file: str):
        _, data = pipeline.cached_map(services, file)
        if data is None:
            raise HTTPException(404, "not mapped yet")
        return map_mod.summary(data)

    # --- agent ---------------------------------------------------------------------------------------------------
    @api.get("/agent/sessions")
    def agent_sessions():
        return services.agent.list()

    @api.post("/agent/sessions")
    def agent_new():
        project()
        return {"id": services.agent.create()}

    @api.get("/agent/sessions/{session_id}")
    def agent_history(session_id: str):
        session = services.agent.sessions.get(session_id)
        return {"events": services.agent.history(session_id), "busy": bool(session and session.task and not session.task.done()),
                "meta": read_json(project().ws("agent", f"{session_id}.json"))}

    @api.post("/agent/sessions/{session_id}/messages")
    async def agent_message(session_id: str, body: dict = Body(...)):
        if not services.settings.agent_ready():
            raise HTTPException(409, "set up an agent on the Setup screen (a Claude API key, or an OpenAI-compatible address and model)")
        try:
            services.agent.send(session_id, body["text"])
        except KeyError:
            raise HTTPException(404, "no such conversation")
        except RuntimeError as error:
            raise HTTPException(409, str(error))
        return {"ok": True}

    @api.post("/agent/sessions/{session_id}/approve")
    def agent_approve(session_id: str, body: dict = Body(...)):
        ok = services.agent.get(session_id).answer(body["request_id"], body.get("allow", False), body.get("always", False))
        return {"ok": ok}

    @api.post("/agent/sessions/{session_id}/stop")
    async def agent_stop(session_id: str):
        await services.agent.get(session_id).stop()
        return {"ok": True}

    @api.post("/agent/external/call")
    async def agent_external(body: dict = Body(...)):
        """Tool calls from outside agents (mcp_server.py). Approval-needing calls wait for the user's answer in the UI."""
        project()
        if body.get("tool") not in agent_tools.FUNCTIONS:
            raise HTTPException(404, f"no tool {body.get('tool')!r}")
        return {"text": await services.agent.external_call(body["tool"], body.get("args") or {})}

    @api.get("/agent/tools")
    def agent_tool_list():
        return [{"name": name, "description": d, "input_schema": schema} for name, (d, schema) in agent_tools.SCHEMAS.items()]

    @api.get("/agent/mcp")
    def agent_mcp():
        """How to connect an outside agent: the command line and config snippets for common agent CLIs."""
        python, script = Path(sys.executable), HERE / "mcp_server.py"
        env = {"SODAUDIO_PORT": str(port)}
        return {"command": str(python), "args": [str(script)], "env": env,
                "claude_code": f'claude mcp add sodaudio --env SODAUDIO_PORT={port} -- "{python}" "{script}"',
                "codex_toml": "[mcp_servers.sodaudio]\n" f"command = {json.dumps(str(python))}\n"
                              f"args = [{json.dumps(str(script))}]\n" f'env = {{ SODAUDIO_PORT = "{port}" }}',
                "json": {"mcpServers": {"sodaudio": {"command": str(python), "args": [str(script)], "env": env}}}}

    # --- files without native dialogs (PWA) ----------------------------------------------------------------------
    def fs_roots():
        roots = [Path(r) for r in services.settings["fs_roots"] or []]
        if not roots:
            roots = [Path.home()]
            if services.project:
                roots += [services.project.root, services.project.path.parent]
        return [r.resolve() for r in roots]

    @api.get("/fs")
    def fs(path: str = ""):
        roots = fs_roots()
        if not path:
            return {"path": "", "parent": None, "folders": [{"name": str(r), "path": str(r)} for r in roots], "files": []}
        target = Path(path).resolve()
        if not any(target == r or r in target.parents for r in roots):
            raise HTTPException(403, "that folder is not in the allowed list (settings: fs_roots)")
        if not target.is_dir():
            raise HTTPException(404, "no such folder")
        folders, files_ = [], []
        try:
            for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith(".") or child.name in ("node_modules", "__pycache__"):
                    continue
                if child.is_dir():
                    folders.append({"name": child.name, "path": str(child)})
                elif child.suffix.lower() in AUDIO_EXTS or child.suffix.lower() == ".json":
                    files_.append({"name": child.name, "path": str(child), "size": child.stat().st_size})
        except PermissionError:
            pass
        parent = target.parent if any(target != r and r in target.parents or target.parent == r for r in roots) else None
        return {"path": str(target), "parent": str(parent) if parent and parent != target else None, "folders": folders, "files": files_}

    @api.post("/upload")
    async def upload(file: UploadFile = File(...)):
        proj = project()
        name = Path(file.filename or "upload.wav").name
        if Path(name).suffix.lower() not in AUDIO_EXTS:
            raise HTTPException(400, "audio files only")
        target = proj.ws("inbox", name)
        target.parent.mkdir(parents=True, exist_ok=True)
        stem, n = target.stem, 2
        while target.exists():
            target = target.with_name(f"{stem}_{n}{target.suffix}")
            n += 1
        with target.open("wb") as handle:
            while chunk := await file.read(1 << 20):
                handle.write(chunk)
        return {"file": rel(target, proj.workspace)}

    @api.post("/reveal")
    def reveal(body: dict = Body(...)):
        raw = body["path"]
        proj = services.project
        candidates = [Path(raw)]
        if proj:
            candidates += [proj.root / raw, proj.workspace / raw]
        path = next((p for p in candidates if p.exists()), None)
        if path is None:
            raise HTTPException(404, "no such file")
        if body.get("open", True) and sys.platform == "win32":
            subprocess.Popen(["explorer", f"/select,{path.resolve()}"])
        return {"path": str(path.resolve())}

    @api.post("/autostart")
    def autostart(body: dict = Body(...)):
        """Start with Windows: a shortcut to start.cmd --no-open in the Startup folder (PWA build)."""
        startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        link = startup / "Sodaudio engine.cmd"
        if body.get("enabled"):
            start = HERE.parent / "start.cmd"
            link.write_text(f'@echo off\r\ncall "{start}" --no-open\r\n', encoding="utf-8")
        elif link.exists():
            link.unlink()
        services.settings.update({"start_with_windows": bool(body.get("enabled"))})
        return {"enabled": link.exists()}

    @api.post("/shutdown")
    def shutdown(body: dict = Body(default={})):
        """Stop the running job and cancel the queued ones (mode stop, the default: quitting), or let the running job
        finish and keep the queue (mode finish: an engine restart), then exit; the ComfyUI this engine started is stopped on the way out (services.shutdown). Nothing
        is left to start again by itself on the next start."""
        mode_ = body.get("mode", "stop")
        q = queue()
        q.pause()
        if mode_ == "stop":
            for job in q.store.list("queued"):
                q.cancel(job["id"])
            for job_id in list(q.running):
                q.cancel(job_id)

        def finish():
            if not q.wait_idle(timeout=None if mode_ == "finish" else 30):
                services.log("a job did not stop in 30 s; it is ended with the engine")
            for job in q.store.list("running"):  # ended with the engine: cancelled, not resumed on the next start
                q.store.update(job["id"], state="cancelled", finished=time.time())
            services.log("engine shutting down")
            server = state.get("server")
            if server:
                services.events.emit({"type": "engine", "stopping": True})
                if services.events.loop:
                    services.events.loop.call_soon_threadsafe(services.events.close_all)
                server.should_exit = True
            else:
                os._exit(0)
        threading.Thread(target=finish, daemon=True).start()
        return {"ok": True, "waiting_for": list(q.running)}

    # --- events --------------------------------------------------------------------------------------------------
    async def event_socket(socket: WebSocket):
        if not origin_ok(socket.headers.get("origin"), socket.headers.get("host")):
            await socket.close(code=4403)
            return
        await socket.accept()
        stream = services.events.subscribe()
        await socket.send_json({"type": "health", **services.health()})
        try:
            while True:
                event = await stream.get()
                if event is None:
                    await socket.close()
                    break
                await socket.send_json(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            services.events.unsubscribe(stream)

    app.websocket("/api/events")(event_socket)
    app.websocket("/events")(event_socket)
    app.include_router(api)

    if UI_DIST.exists():
        app.mount("/", StaticFiles(directory=UI_DIST, html=True), name="ui")
    else:
        @app.get("/")
        def no_ui():
            return HTMLResponse("<h1>Sodaudio</h1><p>The UI is not built yet: run <code>npm run build</code> in ui/.</p>")
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("SODAUDIO_PORT", 8770)))
    parser.add_argument("--fake", action="store_true", help="use the fake generator (no GPU)")
    args = parser.parse_args()
    import uvicorn
    settings = Settings()
    if args.fake:
        settings.data["generator"] = "fake"  # this run only; not saved
    services = Services(settings, pipeline.handlers())
    app = create_app(services, args.port)
    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning", ws_max_size=16 * 2**20, timeout_graceful_shutdown=10)
    server = uvicorn.Server(config)
    app.state.holder["server"] = server
    print(f"Sodaudio engine {VERSION}: http://127.0.0.1:{args.port}", flush=True)
    server.run()


if __name__ == "__main__":
    main()
