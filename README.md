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
cd C:\Users\marce\Documents\Programming\ai-assistant
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\setup.ps1
.\setup-phase6.ps1
```

3. Start the stack:

```powershell
.\start-all.bat
```

4. Optional — open the control panel: double-click `jarvis-ui.bat`
5. Optional — auto-start at login: click **Install Startup** in the control panel, or run `.\install-startup.bat`

## Control Panel

Double-click `jarvis-ui.bat` to open the Jarvis Control Panel.

Use it to:

- start/stop Jarvis
- start Jarvis inside the UI and see the same live voice output you used to see in the terminal
- install startup at Windows login
- list and preview voices
- apply a selected voice to `voice/config.yaml`
- open logs/config/Web UI

For startup, this project uses a Windows Scheduled Task (`JarvisLocalAI`). That is usually better than a classic Windows service for this assistant because Jarvis needs user-session access to your microphone, speakers, windows, Cursor, browser, and desktop tools.

## Firefox Page Bridge

To let Jarvis read your real Firefox tabs while staying logged in:

1. Start Jarvis from the UI so the local bridge is running.
2. Open Firefox and go to `about:debugging#/runtime/this-firefox`.
3. Click **Load Temporary Add-on...**.
4. Select `firefox-extension/manifest.json`.
5. Open YouTube playlists in Firefox.
6. Ask Jarvis: **"what playlists do you see please"**.

The extension sends the current page title, URL, visible text, and links to Jarvis at `http://127.0.0.1:8765/context`. It stays local on your machine.

For permanent installation, see `FIREFOX-EXTENSION.md`. Normal Firefox requires a signed extension; temporary add-ons do not survive Firefox restarts.

## Voice usage

1. Say **"hey jarvis"** and wait for the beep.
2. Speak freely. Jarvis buffers speech until you say **"please"**.
3. End each command with **"please"** to send it, e.g. "open Cursor please".
4. Say **"go to sleep please"** or **"stop listening please"** to return to wake-word mode.
5. After **60 seconds** of idle time, Jarvis goes back to sleep automatically.

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

Preview voices:

```powershell
cd C:\Users\marce\Documents\Programming\ai-assistant\voice

# List English voices
.\.venv\Scripts\python.exe preview_voices.py --filter en --list

# Listen to one voice
.\.venv\Scripts\python.exe preview_voices.py --voice en-GB-RyanNeural

# Listen to the first 10 matching voices
.\.venv\Scripts\python.exe preview_voices.py --filter en-GB --preview-first 10

# Save MP3 samples you can replay later
.\.venv\Scripts\python.exe preview_voices.py --filter en-US --preview-first 10 --save-dir ..\logs\voice-samples
```

To use a selected voice, set `tts.edge_voice` in `voice/config.yaml`, then restart Jarvis.

For fully offline TTS, set `engine: "pyttsx3"` (robotic but local).

## Config

Edit `voice/config.yaml`:

| Setting | Purpose |
|---|---|
| `wake_word.conversation_idle_sec` | Seconds of silence before sleep (default 20) |
| `listening.command_phrases` | Words that send the buffered command (default `please`) |
| `listening.conversation_idle_sec` | Idle seconds before always-on mode sleeps |
| `ollama.model` | LLM model name |
| `whisper.model` | STT size (`base`, `small`, `medium`) |
| `tools.workspace` | Folder for file tools |

## Logs

- `logs/voice-assistant.log` — normal runtime log
- `logs/voice-commands.jsonl` — structured debug audit of transcripts, buffered commands, sent commands, tool calls/results, replies, sleep, and errors

## GitHub

Repository: https://github.com/magnify-dev/ai-assistant
