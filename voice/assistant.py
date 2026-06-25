#!/usr/bin/env python3
"""Local voice assistant: wake word -> STT -> Ollama (+ tools) -> TTS."""

from __future__ import annotations

import json
import logging
import msvcrt
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
import yaml

from tools import TOOL_DEFINITIONS, capture_active_window_context, execute_tool, tools_enabled

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"

_whisper_model = None
_tts_engine = None
_lock_handle = None
_tts_stop = threading.Event()
_audit_lock = threading.Lock()
_last_git_project: str | None = None
_last_action: dict[str, object] = {
    "text": "",
    "reply": "",
    "tools": [],
    "source": "",
    "actually_did_work": False,
}

STOP_PHRASES = {
    "stop listening",
    "stop listening to my commands",
    "stop assistant",
    "go to sleep",
    "goodbye",
    "sleep",
    "jarvis go to sleep",
    "hey jarvis go to sleep",
}

SLEEP_INTENT_PHRASES = {
    "go to sleep",
    "stop listening",
    "stop assistant",
    "sleep",
    "goodbye",
}

_WAKE_PREFIXES = (
    "hey jarvis",
    "okay jarvis",
    "ok jarvis",
    "jarvis",
)


def _is_sleep_command(text: str) -> bool:
    normalized = text.lower().strip(" .!?,-")
    if normalized in STOP_PHRASES:
        return True

    stripped = normalized
    for prefix in _WAKE_PREFIXES:
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip(" .!?,")
            break

    stripped = stripped.strip(" .!?,-")
    polite_prefixes = (
        "can you ",
        "could you ",
        "would you ",
        "will you ",
        "please ",
        "jarvis please ",
    )
    polite_suffixes = (
        " please",
        " for me",
        " now",
    )

    changed = True
    while changed:
        changed = False
        for prefix in polite_prefixes:
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix) :].strip(" .!?,-")
                changed = True
        for suffix in polite_suffixes:
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)].strip(" .!?,-")
                changed = True

    if stripped in STOP_PHRASES or stripped in {"sleep", "go to sleep", "stop", "goodbye"}:
        return True

    for phrase in STOP_PHRASES:
        if stripped == phrase or stripped.startswith(phrase):
            return True

    for phrase in SLEEP_INTENT_PHRASES:
        if phrase in stripped and "don't" not in stripped and "do not" not in stripped:
            return True

    return False


class ConversationEnded(Exception):
    """Return to wake-word listening without shutting down the assistant."""


def _audit_path(cfg: dict) -> Path:
    raw_path = cfg.get("logging", {}).get("command_audit_file", "../logs/voice-commands.jsonl")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def _audit_value(value: object, max_chars: int = 4000) -> object:
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[:max_chars] + "...[truncated]"
    if isinstance(value, dict):
        return {str(k): _audit_value(v, max_chars=max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [_audit_value(v, max_chars=max_chars) for v in value]
    return value


def audit_event(cfg: dict, event_type: str, **payload: object) -> None:
    """Append structured voice/debug events for later inspection."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "event": event_type,
        **{key: _audit_value(value) for key, value in payload.items()},
    }
    try:
        path = _audit_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False)
        with _audit_lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception as exc:
        logging.debug("Could not write command audit event: %s", exc)


@dataclass
class SessionState:
    mode: str = "sleeping"
    command_buffer: str = ""
    wake_mute_until: float = 0.0
    last_speech_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SpeechSegmenter:
    """Detect speech segments from a continuous mic stream (VAD on RMS)."""

    def __init__(self, cfg: dict) -> None:
        speech = cfg["speech"]
        self.sample_rate = speech["sample_rate"]
        self.max_frames = int(speech["max_record_sec"] * self.sample_rate)
        self.min_frames = int(speech["min_record_sec"] * self.sample_rate)
        self.silence_frames_needed = int(speech["silence_duration_sec"] * self.sample_rate)
        self.silence_threshold = speech["silence_threshold"]
        self.frames: list[np.ndarray] = []
        self.silent_run = 0
        self.started = False

    def reset(self) -> None:
        self.frames.clear()
        self.silent_run = 0
        self.started = False

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))

        if rms >= self.silence_threshold:
            self.started = True
            self.silent_run = 0
        elif not self.started:
            return None
        elif self.started:
            self.silent_run += len(chunk)
            total = sum(len(f) for f in self.frames)
            if self.silent_run >= self.silence_frames_needed and total >= self.min_frames:
                audio = np.concatenate(self.frames).astype(np.float32)
                self.reset()
                logging.info("Recorded %.1f seconds of audio", len(audio) / self.sample_rate)
                return audio

        self.frames.append(chunk.copy())
        total = sum(len(f) for f in self.frames)
        if total >= self.max_frames:
            audio = np.concatenate(self.frames).astype(np.float32)
            self.reset()
            logging.info("Recorded %.1f seconds of audio (max length)", len(audio) / self.sample_rate)
            return audio

        return None


def _normalize_phrase(text: str) -> str:
    return text.lower().strip(" .!?,")


def _extract_command(text: str, phrases: list[str], buffer: str, accumulate: bool) -> tuple[str | None, str]:
    """Return (command to send, updated buffer). Command is set when a phrase is found."""
    text = text.strip()
    if not text:
        return None, buffer

    combined = f"{buffer} {text}".strip() if buffer else text
    lowered = combined.lower()

    for phrase in sorted(phrases, key=len, reverse=True):
        pl = phrase.lower().strip()
        if not pl:
            continue

        if _normalize_phrase(text) == pl and buffer:
            return buffer.strip(), ""

        idx = lowered.rfind(pl)
        if idx == -1:
            continue

        before = combined[:idx].strip(" .,!?-")
        after = combined[idx + len(pl) :].strip(" .,!?-")
        if after:
            continue

        if before:
            return before, ""
        if buffer:
            return buffer.strip(), ""
        return None, ""

    if accumulate:
        return None, combined
    return None, ""


def interrupt_speech() -> None:
    _tts_stop.set()
    try:
        import pygame

        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
    except Exception:
        pass


def acquire_single_instance_lock() -> None:
    global _lock_handle
    lock_path = ROOT / ".assistant.lock"
    _lock_handle = open(lock_path, "w", encoding="utf-8")
    try:
        msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("Another Jarvis voice assistant is already running. Exiting.")
        sys.exit(0)
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()


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


def _remember_git_project(tool_name: str, args: dict) -> None:
    global _last_git_project
    if tool_name in {"git_status", "git_command", "github_publish_project"}:
        project = args.get("project_path")
        if isinstance(project, str) and project.strip():
            _last_git_project = project.strip()


def _project_for_voice_request(text: str, cfg: dict) -> str | None:
    lowered = text.lower()
    active_window = execute_tool("get_active_window", {}).lower()
    known_paths = cfg.get("tools", {}).get("known_paths") or {}

    for name, value in known_paths.items():
        path_name = Path(value).name.lower()
        if name.lower() in lowered or name.lower() in active_window or path_name in active_window:
            return value

    for root in cfg.get("tools", {}).get("git_roots") or []:
        path = Path(root)
        if path.name.lower() in lowered or path.name.lower() in active_window:
            return str(path)

    if "cursor" in lowered or "current" in lowered or "this" in lowered:
        for root in cfg.get("tools", {}).get("git_roots") or []:
            path = Path(root)
            if (path / ".git").exists():
                return str(path)

    if _last_git_project:
        return _last_git_project

    roots = [Path(root) for root in cfg.get("tools", {}).get("git_roots") or []]
    git_roots = [path for path in roots if (path / ".git").exists()]
    if len(git_roots) == 1:
        return str(git_roots[0])

    return None


def _changed_file_count(git_status_text: str) -> int:
    if "(clean working tree)" in git_status_text:
        return 0
    lines = git_status_text.splitlines()
    try:
        start = lines.index("Changes:") + 1
    except ValueError:
        return 0
    return sum(1 for line in lines[start:] if line.strip())


def _explicit_git_commit_message(text: str) -> str | None:
    lowered = text.lower()
    for marker in ("message is", "message:", "commit message is", "commit message"):
        idx = lowered.find(marker)
        if idx != -1:
            candidate = text[idx + len(marker) :].strip(" .:\"'")
            if candidate:
                return candidate[:120]
    return None


def _auto_git_commit_message(staged_files_text: str) -> str:
    files = [
        line.strip().replace("\\", "/")
        for line in staged_files_text.splitlines()
        if line.strip() and "(no output)" not in line
    ]
    file_set = set(files)

    if not files:
        return "Update local assistant changes"

    docs = [path for path in files if path.lower().endswith((".md", ".txt"))]
    voice = [path for path in files if path.startswith("voice/")]
    ui = [
        path
        for path in files
        if path.endswith("control_panel.py")
        or path.endswith("preview_voices.py")
        or path.endswith("jarvis-ui.bat")
    ]
    config = [path for path in files if path.endswith((".yaml", ".yml", ".json"))]
    tools = [path for path in files if path.endswith("tools.py")]
    assistant = [path for path in files if path.endswith("assistant.py")]

    if ui:
        return "Add Jarvis control panel and voice preview"
    if assistant and tools:
        return "Improve Jarvis voice assistant tooling"
    if assistant:
        return "Improve Jarvis voice command handling"
    if tools:
        return "Improve Jarvis tool execution"
    if voice and config:
        return "Update Jarvis voice configuration"
    if len(docs) == len(file_set):
        return "Update documentation"
    if voice:
        return "Update Jarvis voice assistant"
    return "Update local assistant changes"


def _wants_git_commit(text: str) -> bool:
    lowered = text.lower()
    return bool(re.search(r"\b(commit|committing)\b", lowered))


def _git_stageable_paths(status_text: str) -> list[str]:
    ignored_prefixes = (
        "logs/",
        "voice/.assistant.lock",
    )
    ignored_suffixes = (
        ".pyc",
    )
    ignored_parts = (
        "/__pycache__/",
        "__pycache__/",
    )
    paths: list[str] = []

    for line in status_text.splitlines():
        if not line.strip() or line.strip() == "(no output)":
            continue
        if len(line) < 4:
            continue

        status_code = line[:2]
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        path = raw_path.strip('"').replace("\\", "/")

        if status_code == "!!":
            continue
        if path.startswith(ignored_prefixes):
            continue
        if path.endswith(ignored_suffixes):
            continue
        if any(part in path for part in ignored_parts):
            continue
        paths.append(path)

    return paths


def _run_git_tool(
    cfg: dict,
    command_id: str | None,
    project: str,
    args: list[str],
) -> str:
    result = execute_tool("git_command", {"project_path": project, "args": args})
    audit_event(
        cfg,
        "tool_result",
        command_id=command_id,
        name="git_command",
        arguments={"project_path": project, "args": args},
        result=result,
    )
    return result


def _git_commit_with_retries(
    text: str,
    cfg: dict,
    command_id: str | None,
    project: str,
) -> str:
    attempts = max(1, int(cfg.get("tools", {}).get("git_commit_attempts", 3)))
    explicit_message = _explicit_git_commit_message(text)
    last_error = ""

    for attempt in range(1, attempts + 1):
        audit_event(
            cfg,
            "git_commit_attempt",
            command_id=command_id,
            project=project,
            attempt=attempt,
            max_attempts=attempts,
        )

        status = _run_git_tool(cfg, command_id, project, ["status", "--short"])
        if status.startswith("Exit "):
            last_error = f"status failed: {status[:300]}"
            continue
        if "(no output)" in status:
            return "There are no uncommitted Git changes to commit."

        stageable_paths = _git_stageable_paths(status)
        audit_event(
            cfg,
            "git_stageable_paths",
            command_id=command_id,
            project=project,
            paths=stageable_paths,
        )
        if not stageable_paths:
            return "I only found ignored/generated files, so I did not commit."

        add_result = _run_git_tool(cfg, command_id, project, ["add", "-A", "--", *stageable_paths])
        if add_result.startswith("Exit "):
            last_error = f"stage failed: {add_result[:300]}"
            continue

        staged = _run_git_tool(cfg, command_id, project, ["diff", "--cached", "--name-only"])
        if staged.startswith("Exit "):
            last_error = f"staged check failed: {staged[:300]}"
            continue
        if "(no output)" in staged:
            last_error = "only ignored/generated files were found"
            break

        commit_message = explicit_message or _auto_git_commit_message(staged)
        audit_event(
            cfg,
            "git_commit_message_generated",
            command_id=command_id,
            explicit=bool(explicit_message),
            staged_files=staged,
            commit_message=commit_message,
        )
        commit = _run_git_tool(cfg, command_id, project, ["commit", "-m", commit_message])
        if commit.startswith("Exit "):
            last_error = f"commit failed: {commit[:300]}"
            if "nothing to commit" in commit.lower():
                break
            continue

        verify = _run_git_tool(cfg, command_id, project, ["status", "--short"])
        audit_event(
            cfg,
            "git_commit_success",
            command_id=command_id,
            project=project,
            attempt=attempt,
            commit_message=commit_message,
            verify_status=verify,
        )
        return "Committed the changes."

    audit_event(
        cfg,
        "git_commit_failed",
        command_id=command_id,
        project=project,
        attempts=attempts,
        error=last_error,
    )
    return f"I couldn't commit after {attempts} attempts. {last_error}"


def _handle_local_command(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    lowered = text.lower()
    git_words = ("git", "uncommitted", "committed", "commit", "status", "changes")
    if not any(word in lowered for word in git_words):
        return None
    if "commitment" in lowered:
        return None

    project = _project_for_voice_request(text, cfg)
    if not project:
        return "I need the project name or folder path before I can check Git."

    _remember_git_project("git_status", {"project_path": project})
    audit_event(cfg, "local_command", command_id=command_id, kind="git", project=project, text=text)
    status = execute_tool("git_status", {"project_path": project})
    audit_event(cfg, "tool_result", command_id=command_id, name="git_status", result=status)
    wants_commit = _wants_git_commit(text)
    if not wants_commit:
        if "Status check failed:" in status:
            return "I couldn't read Git status."
        count = _changed_file_count(status)
        if count == 0:
            return "Git is clean."
        return f"I found {count} changed file{'s' if count != 1 else ''}. Say 'commit please' to commit."

    return _git_commit_with_retries(text, cfg, command_id, project)


def _is_followup_check(text: str) -> bool:
    lowered = text.lower().strip(" .!?")
    return lowered in {
        "did you check",
        "did you do it",
        "did it work",
        "what happened",
        "did anything happen",
    } or lowered.startswith(("did you check ", "did you do "))


def _handle_followup_check(text: str, cfg: dict, command_id: str | None = None) -> str | None:
    if not _is_followup_check(text):
        return None

    tools = [str(tool) for tool in _last_action.get("tools", [])]
    actually_did_work = bool(_last_action.get("actually_did_work"))
    previous = str(_last_action.get("text", ""))

    audit_event(
        cfg,
        "followup_check",
        command_id=command_id,
        text=text,
        previous_command=previous,
        previous_tools=tools,
        previous_did_work=actually_did_work,
    )

    if tools or actually_did_work:
        return "Yes, I ran the check."
    if previous:
        return "No, I didn't run a tool for that yet."
    return "No, I don't have a previous action to check."


def _remember_action(text: str, reply: str, source: str, tools: list[str] | None = None) -> None:
    _last_action.update(
        {
            "text": text,
            "reply": reply,
            "source": source,
            "tools": tools or [],
            "actually_did_work": bool(tools) or source == "local",
        }
    )


def _claims_action_without_tool(text: str) -> bool:
    lowered = text.lower()
    claim_phrases = (
        "i checked",
        "i tried",
        "using cursor",
        "using cursor's",
        "i opened",
        "i searched",
        "i found",
        "i clicked",
        "i ran",
        "i used",
        "done",
        "got it",
    )
    return any(phrase in lowered for phrase in claim_phrases)


def _sanitize_for_speech(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "Done."

    if stripped.startswith("{") or stripped.startswith("```"):
        return "I tried to run that, but need another moment. Please ask again."

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if any(line.startswith(("-", "*", "•")) for line in lines) or len(lines) > 3:
        return lines[0][:180].rstrip(".") + "."

    sentence_parts = stripped.replace("\n", " ").split(". ")
    brief = ". ".join(sentence_parts[:2]).strip()
    if len(brief) > 220:
        brief = brief[:220].rsplit(" ", 1)[0]
    return brief.rstrip() + ("." if brief and brief[-1].isalnum() else "")


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(cfg: dict) -> None:
    log_file = (ROOT / cfg["logging"]["file"]).resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg["logging"].get("level", "INFO")),
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def wait_for_ollama(url: str, timeout_sec: int = 120) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = requests.get(f"{url}/api/tags", timeout=3)
            if resp.ok:
                logging.info("Ollama is ready at %s", url)
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError(f"Ollama not reachable at {url} after {timeout_sec}s")


def preload_model(url: str, model: str) -> None:
    logging.info("Preloading model %s", model)
    try:
        requests.post(
            f"{url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ready"}],
                "stream": False,
                "keep_alive": -1,
            },
            timeout=300,
        ).raise_for_status()
        logging.info("Model %s loaded", model)
    except requests.RequestException as exc:
        logging.warning("Model preload failed (will load on first chat): %s", exc)


def preload_whisper(cfg: dict) -> None:
    global _whisper_model
    from faster_whisper import WhisperModel

    whisper_cfg = cfg["whisper"]
    logging.info(
        "Loading Whisper model '%s' (downloads once, then cached locally)...",
        whisper_cfg["model"],
    )
    _whisper_model = WhisperModel(
        whisper_cfg["model"],
        device=whisper_cfg["device"],
        compute_type=whisper_cfg["compute_type"],
    )
    logging.info("Whisper ready")


def preload_tts(cfg: dict) -> None:
    global _tts_engine
    tts = cfg["tts"]
    engine_name = tts.get("engine", "pyttsx3")

    if engine_name == "edge-tts":
        import edge_tts  # noqa: F401

        logging.info("Text-to-speech ready (edge-tts voice: %s)", tts.get("edge_voice", "en-GB-RyanNeural"))
        return

    if engine_name != "pyttsx3":
        return

    import pyttsx3

    logging.info("Loading text-to-speech engine...")
    _tts_engine = pyttsx3.init()
    _tts_engine.setProperty("rate", tts.get("rate", 185))
    _tts_engine.setProperty("volume", tts.get("volume", 1.0))

    voice_filter = tts.get("voice_name_contains") or ""
    if voice_filter:
        for voice in _tts_engine.getProperty("voices"):
            if voice_filter.lower() in voice.name.lower():
                _tts_engine.setProperty("voice", voice.id)
                break

    logging.info("Text-to-speech ready")


def play_beep(sample_rate: int = 16000) -> None:
    duration = 0.12
    freq = 880.0
    t = np.linspace(0, duration, int(sample_rate * duration), False)
    tone = (0.15 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    sd.play(tone, sample_rate)
    sd.wait()


def record_utterance(cfg: dict, idle_timeout_sec: float | None = None) -> np.ndarray | None:
    speech = cfg["speech"]
    sample_rate = speech["sample_rate"]
    channels = speech["channels"]
    max_frames = int(speech["max_record_sec"] * sample_rate)
    min_frames = int(speech["min_record_sec"] * sample_rate)
    silence_frames_needed = int(speech["silence_duration_sec"] * sample_rate)
    silence_threshold = speech["silence_threshold"]
    input_device = cfg["audio"].get("input_device")

    if idle_timeout_sec is not None:
        logging.info("Waiting for you to speak (%.0fs idle timeout)...", idle_timeout_sec)
    else:
        logging.info("Listening for your command...")

    frames: list[np.ndarray] = []
    silent_run = 0
    started = False
    idle_deadline = time.time() + idle_timeout_sec if idle_timeout_sec is not None else None

    with sd.InputStream(
        samplerate=sample_rate,
        channels=channels,
        dtype="float32",
        device=input_device,
    ) as stream:
        while len(frames) * stream.blocksize < max_frames:
            chunk, _ = stream.read(512)
            chunk = chunk.reshape(-1)
            frames.append(chunk.copy())

            rms = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))
            if rms >= silence_threshold:
                started = True
                silent_run = 0
            elif started:
                silent_run += len(chunk)
                if silent_run >= silence_frames_needed and len(frames) * 512 >= min_frames:
                    break
            elif idle_deadline is not None and time.time() >= idle_deadline:
                logging.info("No speech within %.0fs — going back to wake word", idle_timeout_sec)
                return None

    if not started:
        if idle_timeout_sec is not None:
            return None
        logging.info("No speech detected")
        return np.array([], dtype=np.float32)

    audio = np.concatenate(frames)
    logging.info("Recorded %.1f seconds of audio", len(audio) / sample_rate)
    return audio.astype(np.float32)


def transcribe(audio: np.ndarray, cfg: dict) -> str:
    global _whisper_model
    if _whisper_model is None:
        preload_whisper(cfg)

    whisper_cfg = cfg["whisper"]
    logging.info("Transcribing...")
    segments, _ = _whisper_model.transcribe(
        audio,
        language=whisper_cfg.get("language") or None,
        vad_filter=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    logging.info("Heard: %s", text or "<empty>")
    return text


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
        logging.info("Reply: %s", followup_reply)
        audit_event(cfg, "assistant_reply", command_id=command_id, source="local", text=followup_reply)
        _remember_action(text, followup_reply, "local_followup", [])
        return followup_reply

    local_reply = _handle_local_command(text, cfg, command_id=command_id) if use_tools else None
    if local_reply:
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": local_reply})
        logging.info("Reply: %s", local_reply)
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
    logging.info("Reply: %s", final_text)
    audit_event(cfg, "assistant_reply", command_id=command_id, source="llm", text=final_text)
    _remember_action(text, final_text, "llm", executed_tools)
    return final_text


def _speak_edge_tts(text: str, cfg: dict) -> None:
    import asyncio

    import edge_tts

    tts = cfg["tts"]
    voice = tts.get("edge_voice", "en-GB-RyanNeural")
    rate = tts.get("edge_rate", "+8%")
    pitch = tts.get("edge_pitch", "+0Hz")

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        mp3_path = Path(tmp.name)

    async def _generate() -> None:
        communicator = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicator.save(str(mp3_path))

    try:
        asyncio.run(_generate())
        import pygame

        pygame.mixer.init()
        pygame.mixer.music.load(str(mp3_path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            if _tts_stop.is_set():
                pygame.mixer.music.stop()
                return
            time.sleep(0.05)
        pygame.mixer.music.unload()
    finally:
        mp3_path.unlink(missing_ok=True)


def speak(text: str, cfg: dict, *, interruptible: bool = True) -> None:
    global _tts_engine
    tts = cfg["tts"]
    engine_name = tts.get("engine", "pyttsx3")
    logging.info("Speaking...")
    _tts_stop.clear()

    if engine_name == "edge-tts":
        _speak_edge_tts(text, cfg)
        return

    if engine_name == "pyttsx3":
        if _tts_engine is None:
            preload_tts(cfg)
        if _tts_stop.is_set():
            return
        _tts_engine.say(text)
        _tts_engine.runAndWait()
        return

    piper_exe = resolve_path(tts["piper_exe"])
    voice_model = resolve_path(tts["voice_model"])
    if not piper_exe.exists() or not voice_model.exists():
        raise FileNotFoundError("Piper TTS not configured")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)
    try:
        proc = subprocess.run(
            [str(piper_exe), "--model", str(voice_model), "--output_file", str(wav_path)],
            input=text,
            text=True,
            capture_output=True,
            check=False,
            cwd=str(piper_exe.parent),
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "Piper TTS failed")
        with wave.open(str(wav_path), "rb") as wf:
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            sd.play(audio, wf.getframerate(), device=cfg["audio"].get("output_device"))
            sd.wait()
    finally:
        wav_path.unlink(missing_ok=True)


def handle_turn(user_text: str, cfg: dict, history: list[dict]) -> None:
    if _is_sleep_command(user_text):
        audit_event(cfg, "sleep_command", text=user_text, mode="turn_based")
        speak("Going to sleep.", cfg)
        raise ConversationEnded()

    command_id = uuid.uuid4().hex[:12]
    audit_event(cfg, "command_ready", command_id=command_id, text=user_text, mode="turn_based")
    reply = chat_ollama(user_text, cfg, history, command_id=command_id)
    speak(reply, cfg)
    cooldown = float(cfg.get("wake_word", {}).get("cooldown_after_turn_sec", 4.0))
    time.sleep(cooldown)


def _listening_cfg(cfg: dict) -> dict:
    return cfg.get("listening") or {}


def _go_to_sleep(
    cfg: dict,
    state: SessionState,
    history: list[dict],
    *,
    announce: bool = True,
) -> None:
    wake = cfg["wake_word"]
    mute_sec = float(_listening_cfg(cfg).get("post_sleep_mute_sec", wake.get("post_sleep_mute_sec", 4.0)))
    interrupt_speech()
    with state.lock:
        state.mode = "sleeping"
        state.command_buffer = ""
        state.wake_mute_until = float("inf")
    history.clear()
    logging.info("Going to sleep")
    audit_event(cfg, "sleep", announce=announce, mute_seconds=mute_sec)
    if announce:
        speak("Going to sleep.", cfg, interruptible=False)
    with state.lock:
        state.wake_mute_until = time.time() + mute_sec
    logging.info("Wake word muted for %.0fs", mute_sec)


def _activate_session(cfg: dict, state: SessionState, sample_rate: int) -> None:
    phrases = _listening_cfg(cfg).get("command_phrases") or ["please"]
    with state.lock:
        state.mode = "active"
        state.command_buffer = ""
        state.last_speech_at = time.time()
    capture_active_window_context()
    play_beep(sample_rate)
    audit_event(cfg, "session_active", command_phrases=phrases)
    logging.info(
        "Active — mic always on. Say your command, then say '%s' to send. Say 'go to sleep' to stop.",
        phrases[0],
    )


def run_always_on_session(
    cfg: dict,
    history: list[dict],
    sample_rate: int,
    stream: sd.InputStream,
    frame_size: int,
    state: SessionState,
) -> None:
    """Always-on mic: accumulate speech until command phrase, process in background."""
    listening = _listening_cfg(cfg)
    phrases = listening.get("command_phrases") or ["please"]
    accumulate = bool(listening.get("accumulate_until_phrase", True))
    idle_sec = float(listening.get("conversation_idle_sec", cfg["wake_word"].get("conversation_idle_sec", 60.0)))

    segmenter = SpeechSegmenter(cfg)
    segment_queue: queue.Queue[np.ndarray | None] = queue.Queue()
    command_queue: queue.Queue[dict | None] = queue.Queue()

    def transcription_worker() -> None:
        while True:
            audio = segment_queue.get()
            if audio is None:
                return

            with state.lock:
                if state.mode != "active":
                    continue

            text = transcribe(audio, cfg)
            audit_event(cfg, "transcript", text=text, duration_sec=round(len(audio) / sample_rate, 3))
            with state.lock:
                state.last_speech_at = time.time()
                if state.mode != "active":
                    continue
                buffer = state.command_buffer

            if _is_sleep_command(text):
                audit_event(cfg, "sleep_command", text=text, mode="always_on_transcript")
                _go_to_sleep(cfg, state, history)
                continue

            command, new_buffer = _extract_command(text, phrases, buffer, accumulate)
            with state.lock:
                state.command_buffer = new_buffer

            if command:
                if _is_sleep_command(command):
                    audit_event(cfg, "sleep_command", text=command, mode="always_on_command")
                    _go_to_sleep(cfg, state, history)
                    continue
                command_id = uuid.uuid4().hex[:12]
                logging.info("Command ready: %s", command)
                audit_event(
                    cfg,
                    "command_ready",
                    command_id=command_id,
                    text=command,
                    buffer_before=buffer,
                    trigger_text=text,
                )
                command_queue.put({"id": command_id, "text": command})
            elif text and accumulate:
                logging.info("Buffered — say '%s' when done: %s", phrases[0], new_buffer)
                audit_event(
                    cfg,
                    "command_buffered",
                    text=text,
                    buffer_before=buffer,
                    buffer_after=new_buffer,
                )

    def command_worker() -> None:
        while True:
            command_item = command_queue.get()
            if command_item is None:
                return
            command_id = str(command_item.get("id", ""))
            command = str(command_item.get("text", ""))

            with state.lock:
                if state.mode != "active":
                    continue

            interrupt_speech()
            try:
                audit_event(cfg, "command_started", command_id=command_id, text=command)
                reply = chat_ollama(command, cfg, history, command_id=command_id)
                with state.lock:
                    if state.mode != "active":
                        continue
                speak(reply, cfg)
                audit_event(cfg, "command_finished", command_id=command_id)
            except Exception as exc:
                logging.exception("Command error: %s", exc)
                audit_event(cfg, "command_error", command_id=command_id, text=command, error=str(exc))
                with state.lock:
                    if state.mode == "active":
                        speak("Something went wrong.", cfg)

    transcribe_thread = threading.Thread(target=transcription_worker, daemon=True)
    command_thread = threading.Thread(target=command_worker, daemon=True)
    transcribe_thread.start()
    command_thread.start()

    try:
        while True:
            with state.lock:
                if state.mode != "active":
                    return
                if time.time() - state.last_speech_at >= idle_sec:
                    logging.info("Idle for %.0fs — going to sleep", idle_sec)
                    audit_event(cfg, "idle_sleep", idle_seconds=idle_sec)
                    _go_to_sleep(cfg, state, history, announce=False)
                    return

            audio, _ = stream.read(frame_size)
            chunk = audio.reshape(-1)

            segment = segmenter.feed(chunk)
            if segment is not None and segment.size:
                segment_queue.put(segment)

            with state.lock:
                if state.mode != "active":
                    return
    finally:
        segment_queue.put(None)
        command_queue.put(None)
        transcribe_thread.join(timeout=5)
        command_thread.join(timeout=5)
        segmenter.reset()


def run_conversation_session(cfg: dict, history: list[dict], sample_rate: int) -> None:
    """Multi-turn chat after wake word; ends after idle timeout or stop phrase."""
    wake = cfg["wake_word"]
    idle_sec = float(wake.get("conversation_idle_sec", 20.0))
    capture_active_window_context()
    play_beep(sample_rate)

    first_turn = True
    while True:
        try:
            utterance = record_utterance(
                cfg,
                idle_timeout_sec=None if first_turn else idle_sec,
            )
            if utterance is None:
                return

            first_turn = False
            user_text = transcribe(utterance, cfg) if utterance.size else ""
            if not user_text:
                speak("I didn't catch that.", cfg)
                continue

            handle_turn(user_text, cfg, history)
        except ConversationEnded:
            return
        except Exception as exc:
            logging.exception("Conversation error: %s", exc)
            try:
                speak("Something went wrong.", cfg)
            except Exception:
                pass
            return


def run_wake_word_loop(cfg: dict) -> None:
    import openwakeword
    from openwakeword.model import Model

    openwakeword.utils.download_models()
    wake = cfg["wake_word"]
    listening = _listening_cfg(cfg)
    always_on = listening.get("mode", "always_on") != "turn_based"
    sample_rate = cfg["speech"]["sample_rate"]
    input_device = cfg["audio"].get("input_device")
    history: list[dict] = []
    state = SessionState()

    logging.info("Loading wake word model: %s", wake["model"])
    oww = Model(
        wakeword_models=[wake["model"]],
        inference_framework=wake.get("inference_framework", "onnx"),
    )

    tools_on = tools_enabled(cfg)
    wake_phrase = wake["model"].replace("_", " ")
    if always_on:
        cmd_phrase = (listening.get("command_phrases") or ["please"])[0]
        logging.info(
            "Say '%s' to activate Jarvis%s — then speak freely and end with '%s'",
            wake_phrase,
            " — PC tools enabled" if tools_on else "",
            cmd_phrase,
        )
    else:
        logging.info(
            "Say '%s' to activate Jarvis%s",
            wake_phrase,
            " — PC tools enabled" if tools_on else "",
        )

    frame_size = 1280
    wake_model = wake["model"]
    wake_threshold = wake["threshold"]

    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=frame_size,
        device=input_device,
    ) as stream:
        while True:
            with state.lock:
                mode = state.mode
                wake_mute_until = state.wake_mute_until

            if always_on and mode == "active":
                _activate_session(cfg, state, sample_rate)
                try:
                    run_always_on_session(cfg, history, sample_rate, stream, frame_size, state)
                except SystemExit:
                    break
                finally:
                    oww.reset()
                    with state.lock:
                        state.mode = "sleeping"
                    logging.info("Listening for wake word again")
                continue

            if time.time() < wake_mute_until:
                stream.read(frame_size)
                continue

            audio, _ = stream.read(frame_size)
            pcm = (audio.reshape(-1) * 32767).astype(np.int16)
            score = float(oww.predict(pcm).get(wake_model, 0.0))
            if score < wake_threshold:
                continue

            logging.info("Wake word detected (score=%.2f)", score)
            audit_event(cfg, "wake_word", score=round(score, 4), model=wake_model)

            if always_on:
                with state.lock:
                    state.mode = "active"
                    state.wake_mute_until = 0.0
                    state.last_speech_at = time.time()
                continue

            try:
                run_conversation_session(cfg, history, sample_rate)
            except SystemExit:
                break
            finally:
                oww.reset()
                with state.lock:
                    state.wake_mute_until = time.time() + float(
                        wake.get("post_sleep_mute_sec", 4.0)
                    )
                logging.info("Listening for wake word again")


def main() -> None:
    acquire_single_instance_lock()
    cfg = load_config()
    workspace = cfg.get("tools", {}).get("workspace")
    if workspace:
        os.environ["JARVIS_WORKSPACE"] = workspace
    github_org = cfg.get("tools", {}).get("github_org")
    if github_org:
        os.environ["JARVIS_GITHUB_ORG"] = github_org
    git_roots = cfg.get("tools", {}).get("git_roots")
    if git_roots:
        os.environ["JARVIS_GIT_ROOTS"] = ";".join(git_roots)
    known_paths = cfg.get("tools", {}).get("known_paths") or {}
    if known_paths:
        import tools as tools_module

        tools_module.KNOWN_PATHS = dict(known_paths)
    setup_logging(cfg)
    wait_for_ollama(cfg["ollama"]["url"])
    preload_model(cfg["ollama"]["url"], cfg["ollama"]["model"])
    preload_whisper(cfg)
    preload_tts(cfg)
    run_wake_word_loop(cfg)


if __name__ == "__main__":
    main()
