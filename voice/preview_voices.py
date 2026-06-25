#!/usr/bin/env python3
"""List and preview edge-tts voices for Jarvis."""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
import time
from pathlib import Path

import edge_tts


DEFAULT_TEXT = "Hello, I am Jarvis. This is a sample of my voice."


async def list_voices(filter_text: str = "") -> list[dict]:
    voices = await edge_tts.list_voices()
    if filter_text:
        needle = filter_text.lower()
        voices = [
            voice
            for voice in voices
            if needle in voice.get("ShortName", "").lower()
            or needle in voice.get("Locale", "").lower()
            or needle in voice.get("FriendlyName", "").lower()
            or needle in voice.get("Gender", "").lower()
        ]
    return sorted(voices, key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))


async def save_sample(voice: str, text: str, output_path: Path, rate: str, pitch: str) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    communicator = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicator.save(str(output_path))
    return output_path


def play_mp3(path: Path) -> None:
    try:
        import pygame

        pygame.mixer.init()
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        while pygame.mixer.music.get_busy():
            time.sleep(0.05)
        pygame.mixer.music.unload()
    except Exception:
        os.startfile(str(path))  # type: ignore[attr-defined]


def print_voice_table(voices: list[dict], limit: int | None = None) -> None:
    rows = voices[:limit] if limit else voices
    for idx, voice in enumerate(rows, start=1):
        short = voice.get("ShortName", "")
        locale = voice.get("Locale", "")
        gender = voice.get("Gender", "")
        friendly = voice.get("FriendlyName", "")
        print(f"{idx:3}. {short:<34} {locale:<8} {gender:<8} {friendly}")
    if limit and len(voices) > limit:
        print(f"... {len(voices) - limit} more voices hidden. Increase --limit or narrow --filter.")


async def main() -> None:
    parser = argparse.ArgumentParser(description="List and preview edge-tts voices.")
    parser.add_argument("--filter", default="", help="Filter by locale/name/gender, e.g. en-GB, en-US, male")
    parser.add_argument("--limit", type=int, default=80, help="Max voices to print for --list")
    parser.add_argument("--list", action="store_true", help="List matching voices")
    parser.add_argument("--voice", help="ShortName to preview, e.g. en-GB-RyanNeural")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Sample text")
    parser.add_argument("--rate", default="+8%", help="edge-tts rate, e.g. +8% or -5%")
    parser.add_argument("--pitch", default="+0Hz", help="edge-tts pitch, e.g. +0Hz")
    parser.add_argument("--save-dir", type=Path, help="Save MP3 samples here instead of a temp file")
    parser.add_argument("--preview-first", type=int, default=0, help="Play the first N matching voices")
    args = parser.parse_args()

    voices = await list_voices(args.filter)

    if args.list or (not args.voice and not args.preview_first):
        print_voice_table(voices, args.limit)
        print("\nPreview one:")
        print("  .\\.venv\\Scripts\\python.exe preview_voices.py --voice en-GB-RyanNeural")
        print("\nPreview a group:")
        print("  .\\.venv\\Scripts\\python.exe preview_voices.py --filter en-GB --preview-first 10")

    selected: list[str] = []
    if args.voice:
        selected.append(args.voice)
    if args.preview_first:
        selected.extend(voice["ShortName"] for voice in voices[: args.preview_first])

    seen: set[str] = set()
    for voice in selected:
        if voice in seen:
            continue
        seen.add(voice)
        print(f"Playing {voice}...")

        if args.save_dir:
            output_path = args.save_dir / f"{voice}.mp3"
            keep_file = True
        else:
            tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
            tmp.close()
            output_path = Path(tmp.name)
            keep_file = False

        try:
            await save_sample(voice, args.text, output_path, args.rate, args.pitch)
            play_mp3(output_path)
            if keep_file:
                print(f"Saved {output_path}")
        finally:
            if not keep_file:
                output_path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
