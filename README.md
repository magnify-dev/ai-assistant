# Local AI Assistant (Jarvis)

Fully local, open-source stack for Windows:

- **Ollama** — LLM brain (runs on your GPU)
- **Open WebUI** — browser chat at http://localhost:8080
- **Voice assistant** — wake word **"hey jarvis"**, multi-turn conversation, PC tools
- **Helix-Pilot** — desktop control MCP for Cursor (optional phase 6)

## First-time setup

1. Install **Ollama** from https://ollama.com and pull models:

```powershell
ollama pull qwen2.5-coder:14b
ollama pull qwen2.5:14b
```

2. Run setup:

```powershell
cd C:\Users\marce\ai-assistant
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup.ps1
.\setup-phase6.ps1
```

3. Start the stack:

```powershell
.\start-all.bat
```

4. Optional — auto-start at login: `.\install-startup.bat`

## Voice usage

1. Say **"hey jarvis"** and wait for the beep.
2. Ask your question — no wake word needed for follow-ups.
3. After **20 seconds** of silence, Jarvis goes back to sleep (wake word required again).
4. Say **"go to sleep"** or **"stop listening"** to end the conversation early.

## Voice (TTS)

Default engine is **edge-tts** (Microsoft neural voices — needs internet for speech synthesis only).

Edit `voice/config.yaml`:

```yaml
tts:
  engine: "edge-tts"
  edge_voice: "en-GB-RyanNeural"   # British, calm — good Jarvis feel
  edge_rate: "+8%"
```

Other good voices to try:

| Voice | Style |
|---|---|
| `en-GB-RyanNeural` | Calm British (default) |
| `en-GB-ThomasNeural` | Warm British |
| `en-US-AndrewMultilingualNeural` | Natural American |
| `en-US-GuyNeural` | Deep American |

List all voices: `voice\.venv\Scripts\edge-tts.exe --list-voices`

For fully offline TTS, set `engine: "pyttsx3"` (robotic but local).

## Config

Edit `voice/config.yaml`:

| Setting | Purpose |
|---|---|
| `wake_word.conversation_idle_sec` | Seconds of silence before sleep (default 20) |
| `ollama.model` | LLM model name |
| `whisper.model` | STT size (`base`, `small`, `medium`) |
| `tools.workspace` | Folder for file tools |

## Logs

`logs/voice-assistant.log`

## GitHub

Repository: https://github.com/magnify-dev/ai-assistant
