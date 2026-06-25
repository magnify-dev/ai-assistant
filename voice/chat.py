from __future__ import annotations

import json
import logging
import re

import requests

from audit import audit_event
from handlers.browser import _handle_browser_command
from handlers.commands import (
    _claims_action_without_tool,
    _handle_context_action,
    _handle_followup_check,
    _handle_local_command,
    _sanitize_for_speech,
)
from log_util import ui
from speech import interrupt_speech
from tools import TOOL_DEFINITIONS, execute_tool, tools_enabled
from voice_context import _remember_action, _remember_git_project
def _parse_json_object(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _tool_calls_from_content(content: str) -> list[dict]:
    objects: list[dict] = []
    obj = _parse_json_object(content)
    if obj:
        objects.append(obj)
    else:
        decoder = json.JSONDecoder()
        text = content.strip()
        for idx, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                objects.append(parsed)

    if not objects:
        return []

    calls: list[dict] = []
    for obj in objects:
        raw_calls = obj.get("tool_calls")
        if isinstance(raw_calls, list):
            for call in raw_calls:
                if isinstance(call, dict):
                    fn = call.get("function", call)
                    if isinstance(fn, dict) and fn.get("name"):
                        calls.append({"function": fn})
            if calls:
                return calls

        fn = obj.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            calls.append({"function": fn})
            return calls
        if isinstance(fn, str):
            args = obj.get("arguments") or obj.get("parameters") or {}
            calls.append({"function": {"name": fn, "arguments": args}})
            return calls

        name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
        if isinstance(name, str):
            args = obj.get("arguments") or obj.get("parameters") or {}
            calls.append({"function": {"name": name, "arguments": args}})
            return calls

    return []


def _normalize_tool_calls(message: dict) -> list[dict]:
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return tool_calls
    content = (message.get("content") or "").strip()
    if content:
        return _tool_calls_from_content(content)
    return []


def _run_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    assistant_message: dict,
    cfg: dict,
    command_id: str | None = None,
) -> list[str]:
    messages.append(assistant_message)
    executed: list[str] = []
    for call in tool_calls:
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}

        logging.info("Tool call: %s(%s)", name, args)
        audit_event(cfg, "tool_call", command_id=command_id, name=name, arguments=args)
        result = execute_tool(name, args)
        executed.append(name)
        _remember_git_project(name, args)
        logging.info("Tool result: %s", result[:300])
        audit_event(cfg, "tool_result", command_id=command_id, name=name, result=result)
        messages.append({"role": "tool", "content": result})
    return executed


def _tool_calls_from_content(content: str) -> list[dict]:
    objects: list[dict] = []
    obj = _parse_json_object(content)
    if obj:
        objects.append(obj)
    else:
        decoder = json.JSONDecoder()
        text = content.strip()
        for idx, char in enumerate(text):
            if char != "{":
                continue
            try:
                parsed, _end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                objects.append(parsed)

    if not objects:
        return []

    calls: list[dict] = []
    for obj in objects:
        raw_calls = obj.get("tool_calls")
        if isinstance(raw_calls, list):
            for call in raw_calls:
                if isinstance(call, dict):
                    fn = call.get("function", call)
                    if isinstance(fn, dict) and fn.get("name"):
                        calls.append({"function": fn})
            if calls:
                return calls

        fn = obj.get("function")
        if isinstance(fn, dict) and fn.get("name"):
            calls.append({"function": fn})
            return calls
        if isinstance(fn, str):
            args = obj.get("arguments") or obj.get("parameters") or {}
            calls.append({"function": {"name": fn, "arguments": args}})
            return calls

        name = obj.get("name") or obj.get("tool") or obj.get("tool_name")
        if isinstance(name, str):
            args = obj.get("arguments") or obj.get("parameters") or {}
            calls.append({"function": {"name": name, "arguments": args}})
            return calls

    return []


def _normalize_tool_calls(message: dict) -> list[dict]:
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return tool_calls
    content = (message.get("content") or "").strip()
    if content:
        return _tool_calls_from_content(content)
    return []


def _run_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    assistant_message: dict,
    cfg: dict,
    command_id: str | None = None,
) -> list[str]:
    messages.append(assistant_message)
    executed: list[str] = []
    for call in tool_calls:
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}

        logging.info("Tool call: %s(%s)", name, args)
        audit_event(cfg, "tool_call", command_id=command_id, name=name, arguments=args)
        result = execute_tool(name, args)
        executed.append(name)
        _remember_git_project(name, args)
        logging.info("Tool result: %s", result[:300])
        audit_event(cfg, "tool_result", command_id=command_id, name=name, result=result)
        messages.append({"role": "tool", "content": result})
    return executed


def _normalize_tool_calls(message: dict) -> list[dict]:
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return tool_calls
    content = (message.get("content") or "").strip()
    if content:
        return _tool_calls_from_content(content)
    return []


def _run_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    assistant_message: dict,
    cfg: dict,
    command_id: str | None = None,
) -> list[str]:
    messages.append(assistant_message)
    executed: list[str] = []
    for call in tool_calls:
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}

        logging.info("Tool call: %s(%s)", name, args)
        audit_event(cfg, "tool_call", command_id=command_id, name=name, arguments=args)
        result = execute_tool(name, args)
        executed.append(name)
        _remember_git_project(name, args)
        logging.info("Tool result: %s", result[:300])
        audit_event(cfg, "tool_result", command_id=command_id, name=name, result=result)
        messages.append({"role": "tool", "content": result})
    return executed


def _run_tool_calls(
    tool_calls: list[dict],
    messages: list[dict],
    assistant_message: dict,
    cfg: dict,
    command_id: str | None = None,
) -> list[str]:
    messages.append(assistant_message)
    executed: list[str] = []
    for call in tool_calls:
        fn = call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
        else:
            args = raw_args or {}

        logging.info("Tool call: %s(%s)", name, args)
        audit_event(cfg, "tool_call", command_id=command_id, name=name, arguments=args)
        result = execute_tool(name, args)
        executed.append(name)
        _remember_git_project(name, args)
        logging.info("Tool result: %s", result[:300])
        audit_event(cfg, "tool_result", command_id=command_id, name=name, result=result)
        messages.append({"role": "tool", "content": result})
    return executed


def _ollama_chat(url: str, model: str, messages: list[dict], use_tools: bool) -> dict:
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": -1,
    }
    if use_tools:
        payload["tools"] = TOOL_DEFINITIONS
    resp = requests.post(f"{url}/api/chat", json=payload, timeout=300)
    resp.raise_for_status()
    return resp.json()["message"]


def chat_ollama(text: str, cfg: dict, history: list[dict], command_id: str | None = None) -> str:
    ollama = cfg["ollama"]
    use_tools = tools_enabled(cfg)
    max_rounds = int(cfg.get("tools", {}).get("max_rounds", 5))

    audit_event(cfg, "llm_request", command_id=command_id, text=text, tools_enabled=use_tools)
    followup_reply = _handle_followup_check(text, cfg, command_id=command_id) if use_tools else None
    if followup_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": followup_reply})
        ui(f"Jarvis: {followup_reply}")
        audit_event(cfg, "assistant_reply", command_id=command_id, source="local", text=followup_reply)
        _remember_action(text, followup_reply, "local_followup", [])
        return followup_reply

    browser_reply = _handle_browser_command(text, cfg, command_id=command_id) if use_tools else None
    if browser_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": browser_reply})
        ui(f"Jarvis: {browser_reply}")
        audit_event(cfg, "assistant_reply", command_id=command_id, source="local", text=browser_reply)
        _remember_action(text, browser_reply, "local", ["browser_context"])
        return browser_reply

    context_reply = _handle_context_action(text, cfg, command_id=command_id) if use_tools else None
    if context_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": context_reply})
        ui(f"Jarvis: {context_reply}")
        audit_event(cfg, "assistant_reply", command_id=command_id, source="local", text=context_reply)
        _remember_action(text, context_reply, "local", ["act_on_context"])
        return context_reply

    local_reply = _handle_local_command(text, cfg, command_id=command_id) if use_tools else None
    if local_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": local_reply})
        ui(f"Jarvis: {local_reply}")
        audit_event(cfg, "assistant_reply", command_id=command_id, source="local", text=local_reply)
        _remember_action(text, local_reply, "local", ["local_command"])
        return local_reply

    messages: list[dict] = [{"role": "system", "content": ollama["system_prompt"].strip()}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": text})

    logging.info("Thinking%s...", " (tools enabled)" if use_tools else "")
    final_text = ""
    executed_tools: list[str] = []

    for round_idx in range(max_rounds):
        message = _ollama_chat(ollama["url"], ollama["model"], messages, use_tools)
        audit_event(cfg, "llm_response", command_id=command_id, round=round_idx + 1, message=message)
        tool_calls = _normalize_tool_calls(message) if use_tools else []

        if tool_calls:
            executed_tools.extend(_run_tool_calls(tool_calls, messages, message, cfg, command_id=command_id))
            continue

        final_text = (message.get("content") or "").strip()
        if final_text:
            rescue_calls = _tool_calls_from_content(final_text) if use_tools else []
            if rescue_calls:
                logging.info("Recovered tool call from model text content")
                executed_tools.extend(_run_tool_calls(rescue_calls, messages, message, cfg, command_id=command_id))
                continue
            if final_text.startswith("{") or final_text.startswith("```"):
                logging.warning("Unrecognized tool-shaped model response: %s", final_text[:500])
                audit_event(
                    cfg,
                    "unrecognized_tool_response",
                    command_id=command_id,
                    text=final_text,
                )
            break

        logging.warning("Empty model response on round %s", round_idx + 1)

    if not final_text:
        final_text = "Done."

    claim_text = final_text.lower()
    claimed_commit = "committed" in claim_text or "pushed" in claim_text
    ran_write_git_tool = any(
        name in {"git_command", "github_publish_project"} for name in executed_tools
    )
    if claimed_commit and not ran_write_git_tool:
        audit_event(
            cfg,
            "blocked_false_claim",
            command_id=command_id,
            text=final_text,
            executed_tools=executed_tools,
        )
        final_text = "I did not commit anything yet. Say 'commit please' to commit."
    elif not executed_tools and _claims_action_without_tool(final_text):
        audit_event(
            cfg,
            "blocked_false_claim",
            command_id=command_id,
            text=final_text,
            executed_tools=executed_tools,
        )
        final_text = "I didn't run a tool for that yet."

    final_text = _sanitize_for_speech(final_text)
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": final_text})
    ui(f"Jarvis: {final_text}")
    audit_event(cfg, "assistant_reply", command_id=command_id, source="llm", text=final_text)
    _remember_action(text, final_text, "llm", executed_tools)
    return final_text


