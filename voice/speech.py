"""Speech capture, transcription, and text-to-speech."""

from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

ROOT = Path(__file__).resolve().parent

_whisper_model = None
_tts_engine = None
_tts_stop = threading.Event()


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    return path


def interrupt_speech() -> None:
    _tts_stop.set()


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
        else:
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
    logging.info("Heard: %s", text or "(empty)")
    return text


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


def _speak_edge_tts(text: str, cfg: dict) -> None:
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
