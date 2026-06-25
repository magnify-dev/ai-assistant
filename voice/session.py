from __future__ import annotations

import ctypes
import logging
import msvcrt
import os
import queue
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd

from audit import audit_event
from chat import chat_ollama
from log_util import ui
from speech import SpeechSegmenter, interrupt_speech, play_beep, speak, transcribe
from tools import capture_active_window_context, tools_enabled
from voice_context import _remember_action

ROOT = Path(__file__).resolve().parent
_lock_handle = None
_mutex_handle = None

def acquire_single_instance_lock() -> None:
    global _lock_handle, _mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _mutex_handle = kernel32.CreateMutexW(None, False, "Local\\JarvisVoiceAssistant")
    if _mutex_handle and kernel32.GetLastError() == 183:
        print("Another Jarvis voice assistant is already running. Exiting.", flush=True)
        sys.exit(0)

    lock_path = ROOT / ".assistant.lock"
    _lock_handle = open(lock_path, "w", encoding="utf-8")
    try:
        msvcrt.locking(_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        print("Another Jarvis voice assistant is already running. Exiting.", flush=True)
        sys.exit(0)
    _lock_handle.write(str(os.getpid()))
    _lock_handle.flush()



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


@dataclass
class SessionState:
    mode: str = "sleeping"
    command_buffer: str = ""
    command_busy: bool = False
    deferred_command: str = ""
    wake_mute_until: float = 0.0
    last_speech_at: float = field(default_factory=time.time)
    lock: threading.Lock = field(default_factory=threading.Lock)


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


def _looks_like_command(text: str) -> bool:
    """Drop flushed background fragments that are not actionable Jarvis requests."""
    lowered = text.lower().strip(" .!?,-")
    if not lowered:
        return False
    if len(re.findall(r"[a-z0-9]+", lowered)) < 2:
        return False

    intent_patterns = (
        r"\b(can|could|would|will|should)\s+you\b",
        r"\b(is|are|am|do|does|did|has|have|can|could|would|will|should)\b",
        r"\b(open|go|navigate|show|take|click|press|select|choose|read|see|list|tell|search|find)\b",
        r"\b(commit|sync|push|git|status|changes)\b",
        r"\b(browser|firefox|youtube|playlist|video|playing|song|music|page|tab|cursor|folder|file|app|application)\b",
        r"\bwhat\b|\bwhich\b|\bwhere\b|\bwho\b|\bwhen\b|\bhow\b",
    )
    return any(re.search(pattern, lowered) for pattern in intent_patterns)


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
        state.deferred_command = ""
        state.wake_mute_until = float("inf")
    history.clear()
    logging.info("Going to sleep")
    ui("Sleeping.")
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
        state.deferred_command = ""
        state.last_speech_at = time.time()
    capture_active_window_context()
    play_beep(sample_rate)
    audit_event(cfg, "session_active", command_phrases=phrases)
    ui(
        f"Listening — pile up your command, then say '{phrases[0]}'. "
        "Say 'go to sleep' to stop."
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
            if not text.strip():
                continue
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
                if not _looks_like_command(command):
                    logging.info("Ignored non-command speech: %s", command)
                    audit_event(cfg, "ignored_non_command", text=command, trigger_text=text)
                    continue
                command_id = uuid.uuid4().hex[:12]
                item = {"id": command_id, "text": command}
                deferred = False
                deferred_snapshot = ""
                with state.lock:
                    state.command_buffer = ""
                    if state.command_busy:
                        prev = state.deferred_command
                        state.deferred_command = (
                            f"{prev} {command}".strip() if prev else command
                        )
                        deferred = True
                        deferred_snapshot = state.deferred_command
                        logging.info(
                            "Command deferred until idle: %s",
                            deferred_snapshot,
                        )
                    else:
                        command_queue.put(item)
                if deferred:
                    audit_event(
                        cfg,
                        "command_deferred",
                        command_id=command_id,
                        text=command,
                        buffer_before=buffer,
                        trigger_text=text,
                        deferred_command=deferred_snapshot,
                    )
                else:
                    ui(f"You: {command}")
                    audit_event(
                        cfg,
                        "command_ready",
                        command_id=command_id,
                        text=command,
                        buffer_before=buffer,
                        trigger_text=text,
                    )
            elif accumulate and new_buffer and new_buffer != buffer:
                preview = new_buffer if len(new_buffer) <= 72 else new_buffer[:69] + "..."
                ui(f"Heard: {preview}… (say '{phrases[0]}' when done)")
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
                state.command_busy = True

            ui("Working...")
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
            finally:
                deferred_text = ""
                still_active = False
                with state.lock:
                    state.command_busy = False
                    still_active = state.mode == "active"
                    if still_active:
                        deferred_text = state.deferred_command.strip()
                        state.deferred_command = ""
                if deferred_text:
                    deferred_id = uuid.uuid4().hex[:12]
                    audit_event(
                        cfg,
                        "command_ready",
                        command_id=deferred_id,
                        text=deferred_text,
                        deferred=True,
                    )
                    ui(f"You: {deferred_text}")
                    command_queue.put({"id": deferred_id, "text": deferred_text})
                elif still_active:
                    ui("Listening...")

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
        ui(
            f"Say '{wake_phrase}' to wake Jarvis"
            f"{' (tools on)' if tools_on else ''}"
            f" — end commands with '{cmd_phrase}'"
        )
    else:
        ui(f"Say '{wake_phrase}' to wake Jarvis{' (tools on)' if tools_on else ''}.")

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
            ui("Listening...")
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


