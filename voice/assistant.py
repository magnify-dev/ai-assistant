#!/usr/bin/env python3
"""Local voice assistant: wake word -> STT -> Ollama (+ tools) -> TTS."""

from __future__ import annotations

import json
import logging
import msvcrt
import os
import subprocess
import sys
import tempfile
import time
import wave
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

STOP_PHRASES = {
    "stop listening",
    "stop listening to my commands",
    "stop assistant",
    "go to sleep",
    "goodbye",
}


class ConversationEnded(Exception):
    """Return to wake-word listening without shutting down the assistant."""


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
    obj = _parse_json_object(content)
    if not obj:
        return []

    calls: list[dict] = []
    if "name" in obj and "arguments" in obj:
        calls.append({"function": {"name": obj["name"], "arguments": obj["arguments"]}})
        return calls

    fn = obj.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        calls.append({"function": fn})
        return calls

    if isinstance(obj.get("tool_calls"), list):
        return obj["tool_calls"]

    return []


def _normalize_tool_calls(message: dict) -> list[dict]:
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return tool_calls
    content = (message.get("content") or "").strip()
    if content:
        return _tool_calls_from_content(content)
    return []


def _run_tool_calls(tool_calls: list[dict], messages: list[dict], assistant_message: dict) -> None:
    messages.append(assistant_message)
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
        result = execute_tool(name, args)
        logging.info("Tool result: %s", result[:300])
        messages.append({"role": "tool", "content": result})


def _sanitize_for_speech(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "Done."

    if stripped.startswith("{") or stripped.startswith("```"):
        return "I tried to run that, but need another moment. Please ask again."

    if len(stripped) > 500:
        return stripped[:500].rsplit(" ", 1)[0] + "."
    return stripped


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


def chat_ollama(text: str, cfg: dict, history: list[dict]) -> str:
    ollama = cfg["ollama"]
    use_tools = tools_enabled(cfg)
    max_rounds = int(cfg.get("tools", {}).get("max_rounds", 5))

    messages: list[dict] = [{"role": "system", "content": ollama["system_prompt"].strip()}]
    messages.extend(history[-8:])
    messages.append({"role": "user", "content": text})

    logging.info("Thinking%s...", " (tools enabled)" if use_tools else "")
    final_text = ""

    for round_idx in range(max_rounds):
        message = _ollama_chat(ollama["url"], ollama["model"], messages, use_tools)
        tool_calls = _normalize_tool_calls(message) if use_tools else []

        if tool_calls:
            _run_tool_calls(tool_calls, messages, message)
            continue

        final_text = (message.get("content") or "").strip()
        if final_text:
            break

        logging.warning("Empty model response on round %s", round_idx + 1)

    if not final_text:
        final_text = "Done."

    final_text = _sanitize_for_speech(final_text)
    history.append({"role": "user", "content": text})
    history.append({"role": "assistant", "content": final_text})
    logging.info("Reply: %s", final_text)
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
            time.sleep(0.05)
        pygame.mixer.music.unload()
    finally:
        mp3_path.unlink(missing_ok=True)


def speak(text: str, cfg: dict) -> None:
    global _tts_engine
    tts = cfg["tts"]
    engine_name = tts.get("engine", "pyttsx3")
    logging.info("Speaking...")

    if engine_name == "edge-tts":
        _speak_edge_tts(text, cfg)
        return

    if engine_name == "pyttsx3":
        if _tts_engine is None:
            preload_tts(cfg)
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
    normalized = user_text.lower().strip(" .!?")
    if normalized in STOP_PHRASES:
        speak("Okay, going to sleep. Say hey jarvis when you need me.", cfg)
        raise ConversationEnded()

    reply = chat_ollama(user_text, cfg, history)
    speak(reply, cfg)
    cooldown = float(cfg.get("wake_word", {}).get("cooldown_after_turn_sec", 4.0))
    time.sleep(cooldown)


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
    sample_rate = cfg["speech"]["sample_rate"]
    input_device = cfg["audio"].get("input_device")
    history: list[dict] = []
    busy_until = 0.0

    logging.info("Loading wake word model: %s", wake["model"])
    oww = Model(
        wakeword_models=[wake["model"]],
        inference_framework=wake.get("inference_framework", "onnx"),
    )

    tools_on = tools_enabled(cfg)
    logging.info(
        "Say '%s' to activate Jarvis%s",
        wake["model"].replace("_", " "),
        " — PC tools enabled" if tools_on else "",
    )

    frame_size = 1280
    with sd.InputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        blocksize=frame_size,
        device=input_device,
    ) as stream:
        while True:
            if time.time() < busy_until:
                stream.read(frame_size)
                continue

            audio, _ = stream.read(frame_size)
            pcm = (audio.reshape(-1) * 32767).astype(np.int16)
            score = float(oww.predict(pcm).get(wake["model"], 0.0))
            if score < wake["threshold"]:
                continue

            busy_until = time.time() + 120
            logging.info("Wake word detected (score=%.2f)", score)

            try:
                run_conversation_session(cfg, history, sample_rate)
            except SystemExit:
                break
            finally:
                busy_until = time.time() + float(wake.get("cooldown_after_turn_sec", 2.5))
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
    setup_logging(cfg)
    wait_for_ollama(cfg["ollama"]["url"])
    preload_model(cfg["ollama"]["url"], cfg["ollama"]["model"])
    preload_whisper(cfg)
    preload_tts(cfg)
    run_wake_word_loop(cfg)


if __name__ == "__main__":
    main()
