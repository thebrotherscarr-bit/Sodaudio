"""Sodaudio's actions as an MCP server, so any agent that speaks MCP (Codex, Gemini CLI, Claude Code,
opencode, Cursor …) can direct the audio. It runs over stdio and forwards every call to the running engine, which
applies the same approval rules as the agent panel: installs and big runs wait for "Allow" in the app.

    <engine python> <this folder>/mcp_server.py        (the engine must be running; SODAUDIO_PORT if not 8770)

The Setup screen shows the exact command and config snippets for common agents.
"""
import json
import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.agent_tools import SCHEMAS  # noqa: E402

BASE = f"http://127.0.0.1:{os.environ.get('SODAUDIO_PORT', '8770')}/api"
INSTRUCTIONS = ("You are directing a game's audio through Sodaudio engine. The user listens; you propose. "
                "You cannot hear audio: judge it only from map_track data and say so. Use the measured tempo from "
                "map_track, never the cue's bpm. Map before cutting; cut on bar lines; never leave a near-silent stretch "
                "in a loop. Sung parts use invented syllables only. Installs and large runs wait for the user's approval "
                "in Sodaudio window. queue_generate tops a cue up to N takes in total.")


def call_engine(name, args) -> str:
    try:
        # Long timeout: a call that needs approval waits until the user answers in the app.
        response = httpx.post(f"{BASE}/agent/external/call", json={"tool": name, "args": args}, timeout=3600)
    except httpx.HTTPError as error:
        return json.dumps({"error": f"Sodaudio engine is not answering at {BASE} ({error}); start it first"})
    if response.status_code >= 400:
        return json.dumps({"error": response.json().get("detail", response.text)})
    return response.json()["text"]


def main():
    import anyio
    from mcp import types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(name=name, description=description, inputSchema=schema)
                                            for name, (description, schema) in SCHEMAS.items()])

    async def call_tool(ctx, params):
        text = await anyio.to_thread.run_sync(call_engine, params.name, params.arguments or {})
        is_error = text.startswith('{"error"')
        return types.CallToolResult(content=[types.TextContent(text=text)], isError=is_error)

    server = Server("sodaudio", version="1.0.0", instructions=INSTRUCTIONS, on_list_tools=list_tools, on_call_tool=call_tool)

    async def run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(run)


if __name__ == "__main__":
    main()
