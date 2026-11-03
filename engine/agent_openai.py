"""The agent panel with any OpenAI-compatible chat API: OpenAI, Gemini's OpenAI endpoint, Mistral, Groq, OpenRouter,
or a local server (Ollama, LM Studio, llama.cpp, vLLM). A plain tool-calling loop over the same engine actions and
the same approval rules as the Claude agent. The conversation is kept in the workspace (agent/<id>.messages.json).

A local model runs on the same GPU as ACE-Step and MOSS: the one-focus rule applies to it too."""
import asyncio
import json

import httpx

from . import agent_tools
from .agent import system_prompt
from .util import read_json, write_json

MAX_STEPS = 40


def tool_specs():
    return [{"type": "function", "function": {"name": name, "description": description, "parameters": schema}}
            for name, (description, schema) in agent_tools.SCHEMAS.items()]


async def chat(settings, messages):
    base = settings["agent_base_url"].rstrip("/")
    key = settings.agent_key()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": settings["agent_model"], "messages": messages, "tools": tool_specs(), "tool_choice": "auto"}
    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(base + "/chat/completions", json=body, headers=headers)
    if response.status_code >= 400:
        raise RuntimeError(f"{response.status_code} from {base}: {response.text[:800]}")
    return response.json()


async def run_turn(session, text, chat=chat):
    """One user message: ask the model, run the tool calls it makes (asking the user where the rules say so), repeat
    until it answers in words."""
    services = session.services
    path = services.require_project().ws("agent", f"{session.id}.messages.json")
    messages = read_json(path) or [{"role": "system", "content": system_prompt(services.project)}]
    messages.append({"role": "user", "content": text})
    usage = session.meta["usage"]
    try:
        for _ in range(MAX_STEPS):
            reply = await chat(services.settings, messages)
            for key, total in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
                usage[total] += int((reply.get("usage") or {}).get(key) or 0)
            message = reply["choices"][0]["message"]
            calls = message.get("tool_calls") or []
            messages.append({"role": "assistant", "content": message.get("content") or "",
                             **({"tool_calls": calls} if calls else {})})
            if (message.get("content") or "").strip():
                session.record({"kind": "text", "text": message["content"]})
            if not calls:
                break
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                    if not isinstance(args, dict):
                        raise ValueError("arguments are not an object")
                except ValueError as error:
                    result = json.dumps({"error": f"could not read the arguments: {error}"})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                    continue
                session.record({"kind": "tool_use", "id": call["id"], "tool": name, "input": args})
                if await session.permitted(name, args):
                    result = await asyncio.to_thread(agent_tools.call, services, name, args)
                    error = False
                else:
                    result, error = json.dumps({"error": "the user did not allow this (or there is no such tool)"}), True
                session.record({"kind": "tool_result", "id": call["id"], "text": result[:4000], "error": error})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        session.record({"kind": "done", "usage": usage})
    finally:
        write_json(path, messages)
