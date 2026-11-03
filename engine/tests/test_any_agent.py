"""Any agent, not only Claude: the OpenAI-compatible tool loop (with a scripted model) and the MCP server for outside
agents (over real stdio, against a live engine). No model API is called."""
import asyncio
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from engine.agent import AgentHost

from .conftest import wait_for, wait_idle


def scripted(replies):
    """A stand-in chat API: returns the given replies in turn and keeps what it was sent."""
    sent = []

    async def chat(settings, messages):
        sent.append(json.loads(json.dumps(messages)))
        return replies[len(sent) - 1]
    chat.sent = sent
    return chat


def call(id_, name, args):
    return {"id": id_, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_openai_compatible_loop_edits_queues_and_asks(services):
    from engine import agent_openai
    services.settings.data.update(agent_provider="openai", agent_base_url="http://127.0.0.1:1/v1", agent_model="local-model")
    assert services.settings.agent_ready()
    host = AgentHost(services)
    services.agent = host
    events = []
    services.emit = lambda e: events.append(e)
    chat = scripted([
        {"choices": [{"message": {"content": "Making it sparse.", "tool_calls": [
            call("a", "edit_cue", {"id": "mus_frozen_night", "fields": {"voice": "hushed breathy monks", "notes": "user: too busy"}}),
            call("b", "queue_generate", {"cues": ["mus_frozen_night"], "takes": 2, "seconds": 6})]}}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
        {"choices": [{"message": {"content": "", "tool_calls": [call("c", "promote", {"candidates": ["mus_frozen_night__c1.wav"]})]}}]},
        {"choices": [{"message": {"content": "Queued two takes; listen in Audition under music."}}], "usage": {"prompt_tokens": 50, "completion_tokens": 10}},
    ])

    async def scenario():
        session = host.get(host.create())
        task = asyncio.create_task(agent_openai.run_turn(session, "frozen night is too busy, two takes", chat))
        for _ in range(500):  # the install asks; say no
            await asyncio.sleep(0.01)
            asks = [e for e in events if e.get("kind") == "permission"]
            if asks:
                session.answer(asks[0]["request_id"], False)
                break
        await task
        return session

    session = asyncio.run(scenario())
    cue = services.project.cue("mus_frozen_night")
    assert cue["voice"] == "hushed breathy monks" and cue["notes"] == "user: too busy"
    wait_idle(services)
    assert len(list(services.project.candidate_dir("music").glob("mus_frozen_night__c*.wav"))) == 2
    assert not list((services.project.root / "Assets" / "Music").glob("mus_frozen_night*"))  # the install was refused
    kinds = [e["kind"] for e in events if e.get("type") == "agent"]
    assert kinds.count("tool_use") == 3 and "permission" in kinds and kinds[-1] == "done"
    assert chat.sent[0][0]["role"] == "system" and "Measure, don't trust" in chat.sent[0][0]["content"]
    tool_reply = [m for m in chat.sent[2] if m["role"] == "tool" and m["tool_call_id"] == "c"][0]
    assert "did not allow" in tool_reply["content"]
    assert session.meta["usage"]["input_tokens"] == 150
    assert services.project.ws("agent", f"{session.id}.messages.json").exists()


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def live_engine(services):
    import uvicorn
    from engine.app import create_app
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(services, port, open_last=False), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started, 30)
    yield port
    server.should_exit = True
    thread.join(10)


def test_mcp_server_for_outside_agents(services, live_engine):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    script = Path(__file__).resolve().parents[1] / "mcp_server.py"
    params = StdioServerParameters(command=sys.executable, args=[str(script)],
                                   env={**os.environ, "SODAUDIO_PORT": str(live_engine)})

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                assert {"list_cues", "queue_generate", "promote", "map_track"} <= {t.name for t in tools}
                listing = await client.call_tool("list_cues", {"category": "music", "query": "frozen"})
                cues = json.loads(listing.content[0].text)["cues"]
                assert {c["id"] for c in cues} == {"mus_frozen_day", "mus_frozen_night", "mus_frozen_underground"}
                queued = json.loads((await client.call_tool("queue_generate", {"cues": ["use_spear"], "takes": 1})).content[0].text)
                assert queued["takes"] == ["use_spear__c1.wav"]
                # An install waits for the user's answer in the app; answer it from "the UI" while the call waits.
                events = []
                services.emit = lambda e: events.append(e)

                def answer():
                    for _ in range(300):
                        asks = [e for e in events if e.get("kind") == "permission"]
                        if asks:
                            services.agent.get("external").answer(asks[0]["request_id"], True)
                            return
                        time.sleep(0.05)
                wait_idle(services)
                threading.Thread(target=answer, daemon=True).start()
                installed = json.loads((await client.call_tool("promote", {"candidates": ["use_spear__c1.wav"]})).content[0].text)
                assert installed["installed"][0]["path"] == "Assets/Sounds/use_spear.wav"

    asyncio.run(scenario())
    history = [e["kind"] for e in services.agent.history("external")]
    assert history.count("tool_use") == 3 and "permission" in history


def test_mcp_setup_snippets(services, live_engine):
    import httpx
    info = httpx.get(f"http://127.0.0.1:{live_engine}/api/agent/mcp").json()
    assert info["args"][0].endswith("mcp_server.py") and "claude mcp add sodaudio" in info["claude_code"]
    assert info["json"]["mcpServers"]["sodaudio"]["env"]["SODAUDIO_PORT"] == str(live_engine)
