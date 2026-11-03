"""The agent panel's back end (AGENT.md). Any agent can direct the audio:
- Claude: a Claude Agent SDK client per conversation, the engine's actions as an in-process MCP server, the SDK's
  built-in tools turned off;
- any OpenAI-compatible chat API (OpenAI, Gemini, Mistral, OpenRouter, or a local Ollama / LM Studio / llama.cpp
  server): a tool-calling loop over the same actions (agent_openai.py);
- outside agents (Codex, Gemini CLI, Claude Code, opencode …): the same actions through the MCP server
  (mcp_server.py), which calls back into the engine.
Every permission request goes to the UI to answer, whichever agent asks. Conversations are kept in the workspace
(agent/<id>.jsonl) and resume where they stopped."""
import asyncio
import json
import time
import uuid

from . import agent_tools
from .util import read_json, write_json

SERVER = "director"


def system_prompt(project) -> str:
    data = project.data
    style = data.get("style") or {}
    chant = data.get("chant") or {}
    notes = [f"- {c['id']}: {c['notes']}" for c in data["cues"] if c.get("notes") and not c.get("missing")]
    return "\n".join([
        f"You direct the audio of the game project \"{data.get('name')}\" with the tools given. The user's taste decides: "
        "you propose, they listen. You cannot hear audio; you judge it only from map data (tempo, bars, loudness, choir and "
        "drum bands, chroma). Say so when you choose by numbers, and leave \"which take sounds best\" to the user.",
        "",
        "The project:",
        f"- House sound: {style.get('house_sound', '')}",
        f"- Choir: {style.get('choir', '')}",
        f"- Chant lines: {', '.join(chant.get('lines', []))}" if chant else "- No chant.",
        f"- {len([c for c in data['cues'] if not c.get('missing')])} cues. Categories: "
        f"{', '.join(sorted({c['category'] for c in data['cues']}))}.",
        *(["", "What the user said about cues:", *notes[:80]] if notes else []),
        "",
        "Rules:",
        "- Never install over a file the tool did not install. Installs always ask the user first.",
        "- Sung parts use invented syllables only, never real words.",
        "- Measure, don't trust: use the measured tempo from map_track, never the cue's bpm, for bar maths.",
        "- Map before cutting; cut on bar lines; never leave a near-silent stretch in a loop; never splice automatically.",
        "- Keep vocals untouched; raise a quiet piece with gain rather than processing it.",
        "- Before queuing a large run, say how long it will take and how much GPU time it uses.",
        "- One GPU focus: do not queue art and audio together.",
        "- queue_generate tops a cue up to N takes in total (c1..cN): to add two new takes to a cue with one, ask for 3.",
        "",
        "Replies: short. After queuing, say what was queued and where to listen (Audition, under the cue's category).",
    ])


class AgentSession:
    def __init__(self, host, session_id, meta=None):
        self.host, self.id = host, session_id
        self.meta = meta or {"id": session_id, "created": time.time(), "title": "", "sdk_session": None,
                             "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}}
        self.client = None
        self.pending: dict[str, asyncio.Future] = {}
        self.always: set[str] = set()
        self.asked: dict[str, str] = {}
        self.task: asyncio.Task | None = None

    @property
    def services(self):
        return self.host.services

    def record(self, event: dict):
        event = {"session": self.id, **event}
        self.services.emit({"type": "agent", **event})
        project = self.services.project
        if project:
            path = project.ws("agent", f"{self.id}.jsonl")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"t": time.time(), **event}, default=str) + "\n")

    def save_meta(self):
        project = self.services.project
        if project:
            write_json(project.ws("agent", f"{self.id}.json"), self.meta)

    # --- permissions ---------------------------------------------------------------------------------------------
    async def can_use_tool(self, tool_name, tool_input, context):
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
        prefix = f"mcp__{SERVER}__"
        if not tool_name.startswith(prefix):
            return PermissionResultDeny(message="only Sodaudio tools are available")
        allowed = await self.permitted(tool_name[len(prefix):], tool_input)
        return PermissionResultAllow() if allowed else PermissionResultDeny(message="the user said no")

    async def permitted(self, name, tool_input) -> bool:
        """The approval rules (AGENT.md) for any agent: ask the user when a call needs it."""
        if name not in agent_tools.FUNCTIONS:
            return False
        reason = await asyncio.to_thread(agent_tools.needs_approval, self.services, name, tool_input)
        if reason is None or (name in self.always and name not in agent_tools.ALWAYS_ASK):
            return True
        return await self.ask(name, tool_input, reason)

    async def ask(self, name, tool_input, reason) -> bool:
        request_id = uuid.uuid4().hex[:12]
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.asked[request_id] = name
        self.record({"kind": "permission", "request_id": request_id, "tool": name, "input": tool_input, "reason": reason,
                     "can_always": name not in agent_tools.ALWAYS_ASK})
        try:
            return await future
        finally:
            self.pending.pop(request_id, None)
            self.asked.pop(request_id, None)

    def answer(self, request_id, allow, always=False):
        future = self.pending.get(request_id)
        if future is None or future.done():
            return False
        if always and allow and self.asked.get(request_id) not in agent_tools.ALWAYS_ASK:
            self.always.add(self.asked.get(request_id))
        future.set_result(bool(allow))
        self.record({"kind": "permission_answer", "request_id": request_id, "allow": bool(allow), "always": bool(always)})
        return True

    # --- the SDK client ------------------------------------------------------------------------------------------
    def build_server(self):
        from claude_agent_sdk import create_sdk_mcp_server, tool
        tools = []
        for name, (description, schema) in agent_tools.SCHEMAS.items():
            def make(name):
                async def run(args):
                    text = await asyncio.to_thread(agent_tools.call, self.services, name, args)
                    return {"content": [{"type": "text", "text": text}]}
                return run
            tools.append(tool(name, description, schema)(make(name)))
        return create_sdk_mcp_server(SERVER, "1.0.0", tools)

    async def connect(self):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
        settings, project = self.services.settings, self.services.require_project()
        key = settings.api_key()
        env = {"ANTHROPIC_API_KEY": key} if key else {}
        options = ClaudeAgentOptions(
            tools=[], mcp_servers={SERVER: self.build_server()}, system_prompt=system_prompt(project),
            model=settings["agent_model"], can_use_tool=self.can_use_tool, permission_mode="default",
            setting_sources=[], cwd=str(project.workspace), env=env, max_turns=40, resume=self.meta.get("sdk_session"),
            stderr=lambda line: self.services.log(line, "agent"))
        self.client = ClaudeSDKClient(options)
        await self.client.connect()

    async def send(self, text):
        if not self.meta.get("title"):
            self.meta["title"] = text[:60]
        self.record({"kind": "user", "text": text})
        try:
            if self.services.settings["agent_provider"] == "openai":
                from .agent_openai import run_turn
                await run_turn(self, text)
            else:
                if self.client is None:
                    await self.connect()
                await self.client.query(text)
                await self._pump()
        except asyncio.CancelledError:
            self.record({"kind": "done", "stopped": True})
            raise
        except Exception as error:  # surface SDK and API errors in the panel instead of dropping the turn
            self.record({"kind": "error", "text": f"{type(error).__name__}: {error}"})
            self.record({"kind": "done"})
            await self.close()
        finally:
            self.save_meta()

    async def _pump(self):
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock, UserMessage
        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text.strip():
                        self.record({"kind": "text", "text": block.text})
                    elif isinstance(block, ToolUseBlock):
                        self.record({"kind": "tool_use", "id": block.id, "tool": block.name.split("__")[-1], "input": block.input})
            elif isinstance(message, UserMessage) and isinstance(message.content, list):
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        content = block.content
                        if isinstance(content, list):
                            content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
                        self.record({"kind": "tool_result", "id": block.tool_use_id, "text": str(content)[:4000],
                                     "error": bool(block.is_error)})
            elif isinstance(message, ResultMessage):
                self.meta["sdk_session"] = message.session_id
                usage = message.usage or {}
                totals = self.meta["usage"]
                totals["input_tokens"] += int(usage.get("input_tokens", 0)) + int(usage.get("cache_read_input_tokens", 0) or 0) \
                    + int(usage.get("cache_creation_input_tokens", 0) or 0)
                totals["output_tokens"] += int(usage.get("output_tokens", 0))
                totals["cost_usd"] = round(totals["cost_usd"] + float(message.total_cost_usd or 0), 4)
                self.record({"kind": "done", "usage": totals, **({"error": message.errors} if message.is_error else {})})

    async def stop(self):
        for future in self.pending.values():
            if not future.done():
                future.set_result(False)
        if self.client:
            try:
                await self.client.interrupt()
            except Exception:
                pass
        if self.task and not self.task.done():
            self.task.cancel()

    async def close(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None


class AgentHost:
    def __init__(self, services):
        self.services = services
        self.sessions: dict[str, AgentSession] = {}

    def project_changed(self):
        for session in list(self.sessions.values()):
            if session.task and not session.task.done():
                session.task.cancel()
        self.sessions.clear()

    def list(self):
        project = self.services.project
        if not project or not project.ws("agent").exists():
            return []
        metas = [read_json(p) for p in project.ws("agent").glob("*.json")]
        return sorted([m for m in metas if m], key=lambda m: m.get("created", 0), reverse=True)

    def history(self, session_id):
        project = self.services.require_project()
        path = project.ws("agent", f"{session_id}.jsonl")
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def get(self, session_id) -> AgentSession:
        if session_id not in self.sessions:
            meta = read_json(self.services.require_project().ws("agent", f"{session_id}.json"))
            if meta is None:
                raise KeyError(session_id)
            self.sessions[session_id] = AgentSession(self, session_id, meta)
        return self.sessions[session_id]

    async def external_call(self, name, args) -> str:
        """A tool call from an outside agent through the MCP server: shown in the panel, approved like any other."""
        if "external" not in self.sessions:
            meta = read_json(self.services.require_project().ws("agent", "external.json"))
            session = AgentSession(self, "external", meta)
            session.meta["title"] = "Outside agents (MCP)"
            session.save_meta()
            self.sessions["external"] = session
        session = self.sessions["external"]
        call_id = uuid.uuid4().hex[:12]
        session.record({"kind": "tool_use", "id": call_id, "tool": name, "input": args})
        if not await session.permitted(name, args):
            text = json.dumps({"error": "the user did not allow this"})
            session.record({"kind": "tool_result", "id": call_id, "text": text, "error": True})
            return text
        text = await asyncio.to_thread(agent_tools.call, self.services, name, args)
        session.record({"kind": "tool_result", "id": call_id, "text": text[:4000], "error": False})
        return text

    def create(self) -> str:
        self.services.require_project()
        session_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        session = AgentSession(self, session_id)
        session.save_meta()
        self.sessions[session_id] = session
        return session_id

    def send(self, session_id, text):
        session = self.get(session_id)
        if session.task and not session.task.done():
            raise RuntimeError("the agent is still answering; stop it or wait")
        session.task = asyncio.get_running_loop().create_task(session.send(text))
        return session

    async def shutdown(self):
        for session in self.sessions.values():
            await session.stop()
            await session.close()
