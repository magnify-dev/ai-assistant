# PC Control & Coding — How Jarvis Works Now

## Three layers

| Layer | What it does | How to use |
|---|---|---|
| **Voice Jarvis** | Talk + basic PC tools | "Hey Jarvis, open Notepad" |
| **Open WebUI** | Text chat + history | http://localhost:8080 |
| **Cursor + Continue** | Local coding in editor | Continue panel in Cursor |
| **Helix-Pilot MCP** | See & click screen | Cursor Agent + MCP tools |

---

## Voice commands (works now)

After saying **"Hey Jarvis"**, try:

- "Open Cursor"
- "Open Notepad"
- "List files in my Documents folder"
- "Create a file called hello.txt with the text hello world"
- "Run PowerShell to show today's date"
- "What processes are using the most CPU?"

Jarvis uses **qwen2.5-coder:14b** with tools:
- `run_powershell` — run safe commands
- `open_application` — launch apps/URLs
- `read_file` / `write_file` — files under Documents
- `list_directory` — browse Documents

---

## Coding in Cursor (Continue)

1. Open Cursor → Extensions → search **Continue** → Install
2. Config already at `C:\Users\marce\.continue\config.yaml`
3. Open Continue panel (sidebar icon)
4. Chat with **Qwen Coder 14B** locally — edits files in your project

---

## Full desktop control (Helix-Pilot)

Helix-Pilot lets Cursor's Agent **see your screen** and click/type.

### One-time setup

```powershell
cd C:\Users\marce\ai-assistant
.\setup-phase6.ps1
```

Then **restart Cursor**.

### In Cursor Agent chat

Ask things like:
- "Take a screenshot and tell me what's on screen"
- "Open Cursor and click the search bar"
- "Navigate to my project folder"

Helix-Pilot tools appear under MCP when connected.

---

## Cloud for hard tasks (later)

When local model struggles, use Cursor's cloud models (Composer) for:
- Large refactors
- Complex debugging across many files
- Architecture decisions

Local stays default; cloud is manual upgrade in Cursor.

---

## Fixes applied

- **TTS echo trigger**: 3 sec cooldown after each reply so speakers don't re-trigger wake word
- **Empty transcripts**: disabled aggressive VAD filter on short speech
- **Tools enabled**: voice can now act on your PC, not just chat
