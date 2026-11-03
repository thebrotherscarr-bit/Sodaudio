"""The agent's tools and permission bridge, without calling the Claude API."""
import asyncio
import json

from engine import agent_tools
from engine.agent import AgentHost, system_prompt

from .conftest import wait_idle


def test_approval_rules(services):
    assert agent_tools.needs_approval(services, "list_cues", {}) is None
    assert agent_tools.needs_approval(services, "edit_cue", {"id": "x", "fields": {}}) is None
    assert agent_tools.needs_approval(services, "promote", {"candidates": []})
    assert agent_tools.needs_approval(services, "edit_style", {"fields": {}})
    music = [c["id"] for c in services.project.cues() if c["category"] == "music" and not c.get("locked")]
    assert agent_tools.needs_approval(services, "queue_generate", {"cues": music[:5], "takes": 1}) is None
    assert agent_tools.needs_approval(services, "queue_generate", {"cues": music[:6], "takes": 1})
    assert agent_tools.needs_approval(services, "queue_generate", {"cues": music[:3], "takes": 2})  # 6 tracks
    sfx = [c["id"] for c in services.project.cues() if c["category"] not in ("music",)][:31]
    assert agent_tools.needs_approval(services, "queue_generate", {"cues": sfx, "takes": 1})


def test_tools_edit_queue_and_map(services):
    result = json.loads(agent_tools.call(services, "edit_cue", {"id": "mus_frozen_night", "fields": {"notes": "user: too busy"}}))
    assert result["ok"]
    assert json.loads(agent_tools.call(services, "edit_cue", {"id": "mus_frozen_night", "fields": {"locked": True}}))["error"]
    queued = json.loads(agent_tools.call(services, "queue_generate", {"cues": ["mus_frozen_night"], "takes": 1, "seconds": 16}))
    assert queued["takes"] == ["mus_frozen_night__c1.flac"] and queued["estimated_minutes"] > 0
    wait_idle(services)
    takes = json.loads(agent_tools.call(services, "get_takes", {"cue": "mus_frozen_night"}))
    assert takes["candidates"][0]["name"] == "mus_frozen_night__c1.wav"
    summary = json.loads(agent_tools.call(services, "map_track", {"file": "mus_frozen_night__c1"}))
    assert abs(summary["tempo"] - 112) < 3 and summary["bars"]
    listing = json.loads(agent_tools.call(services, "list_cues", {"status": "has-candidates"}))
    assert [c["id"] for c in listing["cues"]] == ["mus_frozen_night"]
    assert "error" in json.loads(agent_tools.call(services, "get_cue", {"id": "nope"}))


def test_permission_bridge(services):
    host = AgentHost(services)
    services.agent = host
    events = []
    services.emit = lambda e: events.append(e)

    async def scenario():
        session = host.get(host.create())
        allow = await session.can_use_tool("mcp__director__list_cues", {}, None)
        assert allow.behavior == "allow"
        denied = await session.can_use_tool("Bash", {"command": "dir"}, None)
        assert denied.behavior == "deny"
        task = asyncio.create_task(session.can_use_tool("mcp__director__promote", {"candidates": ["x"]}, None))
        for _ in range(100):
            await asyncio.sleep(0.01)
            asks = [e for e in events if e.get("kind") == "permission"]
            if asks:
                break
        assert asks[0]["tool"] == "promote" and asks[0]["can_always"] is False
        assert session.answer(asks[0]["request_id"], True, always=True)
        assert (await task).behavior == "allow"
        assert "promote" not in session.always  # installs always ask
        task = asyncio.create_task(session.can_use_tool("mcp__director__edit_style", {"fields": {}}, None))
        await asyncio.sleep(0.05)
        ask = [e for e in events if e.get("kind") == "permission"][-1]
        session.answer(ask["request_id"], True, always=True)
        assert (await task).behavior == "allow"
        assert (await session.can_use_tool("mcp__director__edit_style", {"fields": {}}, None)).behavior == "allow"
        task = asyncio.create_task(session.can_use_tool("mcp__director__fill_gaps", {}, None))
        await asyncio.sleep(0.05)
        ask = [e for e in events if e.get("kind") == "permission"][-1]
        session.answer(ask["request_id"], False)
        assert (await task).behavior == "deny"

    asyncio.run(scenario())


def test_sdk_server_builds_every_tool(services):
    host = AgentHost(services)
    session = host.get(host.create())
    server = session.build_server()
    assert server["name"] == "director"
    assert "Measure, don't trust" in system_prompt(services.project)
