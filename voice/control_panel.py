#!/usr/bin/env python3
"""Small Windows control panel for Jarvis."""

from __future__ import annotations

import asyncio
import re
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import edge_tts

from preview_voices import DEFAULT_TEXT, list_voices, play_mp3, save_sample

ROOT = Path(__file__).resolve().parents[1]
VOICE_DIR = ROOT / "voice"
CONFIG_PATH = VOICE_DIR / "config.yaml"
LOGS_DIR = ROOT / "logs"
START_ALL = ROOT / "start-all.ps1"
STOP_ALL = ROOT / "stop-all.ps1"
INSTALL_STARTUP = ROOT / "install-startup.ps1"
VOICE_PYTHON = VOICE_DIR / ".venv" / "Scripts" / "python.exe"
ASSISTANT = VOICE_DIR / "assistant.py"


def run_hidden(command: list[str], cwd: Path = ROOT) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def run_powershell(script: Path) -> subprocess.Popen:
    return run_hidden(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        ROOT,
    )


def read_config_text() -> str:
    return CONFIG_PATH.read_text(encoding="utf-8")


def update_config_value(key: str, value: str) -> None:
    lines = read_config_text().splitlines()
    in_tts = False
    changed = False

    for idx, line in enumerate(lines):
        if line.startswith("tts:"):
            in_tts = True
            continue
        if in_tts and line and not line.startswith(" "):
            in_tts = False
        if in_tts and re.match(rf"^\s+{re.escape(key)}\s*:", line):
            escaped = value.replace("\\", "\\\\").replace('"', '\\"')
            lines[idx] = f'  {key}: "{escaped}"'
            changed = True
            break

    if not changed:
        raise ValueError(f"Could not find tts.{key} in config.yaml")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_config_value(key: str, default: str = "") -> str:
    text = read_config_text()
    in_tts = False
    for line in text.splitlines():
        if line.startswith("tts:"):
            in_tts = True
            continue
        if in_tts and line and not line.startswith(" "):
            in_tts = False
        if in_tts:
            match = re.match(rf"^\s+{re.escape(key)}\s*:\s*[\"']?(.*?)[\"']?\s*$", line)
            if match:
                return match.group(1).replace('\\"', '"')
    return default


def assistant_running() -> bool:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { "
                "($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
                "$_.CommandLine -like '*ai-assistant*voice*assistant.py*' "
                "} | "
                "Select-Object -First 1 -ExpandProperty Id"
            ),
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return bool(proc.stdout.strip())


def startup_task_exists() -> bool:
    proc = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-ScheduledTask -TaskName JarvisLocalAI -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return "JarvisLocalAI" in proc.stdout


def stop_voice_assistant() -> None:
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | "
                "Where-Object { "
                "($_.Name -eq 'python.exe' -or $_.Name -eq 'pythonw.exe') -and "
                "$_.CommandLine -like '*voice*assistant.py*' "
                "} | Invoke-CimMethod -MethodName Terminate | Out-Null"
            ),
        ],
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


class JarvisControlPanel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Jarvis Control Panel")
        self.geometry("900x620")
        self.minsize(780, 520)

        self.voices: list[dict] = []
        self.voice_by_label: dict[str, str] = {}
        self.assistant_process: subprocess.Popen | None = None

        self.status_var = tk.StringVar(value="Ready")
        self.current_voice_var = tk.StringVar(value=read_config_value("edge_voice", "en-GB-RyanNeural"))
        self.filter_var = tk.StringVar(value="en")
        self.sample_text = tk.StringVar(value=DEFAULT_TEXT)
        self.rate_var = tk.StringVar(value=read_config_value("edge_rate", "+8%"))
        self.pitch_var = tk.StringVar(value=read_config_value("edge_pitch", "+0Hz"))
        self.running_var = tk.StringVar(value="Assistant: checking...")
        self.startup_var = tk.StringVar(value="Startup: checking...")

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_status()
        self.load_voices()

    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        status_frame = ttk.LabelFrame(main, text="Jarvis")
        status_frame.pack(fill=tk.X)

        ttk.Label(status_frame, textvariable=self.running_var).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Label(status_frame, textvariable=self.startup_var).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(status_frame, text="Start Jarvis in UI", command=self.start_jarvis_in_ui).pack(side=tk.LEFT, padx=4)
        ttk.Button(status_frame, text="Start Full Stack", command=self.start_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(status_frame, text="Stop Jarvis", command=self.stop_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(status_frame, text="Install Startup", command=self.install_startup).pack(side=tk.LEFT, padx=4)
        ttk.Button(status_frame, text="Refresh", command=self.refresh_status).pack(side=tk.LEFT, padx=4)

        voice_frame = ttk.LabelFrame(main, text="Voice")
        voice_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        controls = ttk.Frame(voice_frame)
        controls.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(controls, text="Filter").pack(side=tk.LEFT)
        ttk.Entry(controls, textvariable=self.filter_var, width=18).pack(side=tk.LEFT, padx=4)
        ttk.Button(controls, text="Load Voices", command=self.load_voices).pack(side=tk.LEFT, padx=4)
        ttk.Label(controls, text="Current").pack(side=tk.LEFT, padx=(20, 4))
        ttk.Label(controls, textvariable=self.current_voice_var).pack(side=tk.LEFT)

        list_frame = ttk.Frame(voice_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=8)
        self.voice_list = tk.Listbox(list_frame, height=14)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self.voice_list.yview)
        self.voice_list.configure(yscrollcommand=scrollbar.set)
        self.voice_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        sample_frame = ttk.Frame(voice_frame)
        sample_frame.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(sample_frame, text="Sample").pack(side=tk.LEFT)
        ttk.Entry(sample_frame, textvariable=self.sample_text).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        tune_frame = ttk.Frame(voice_frame)
        tune_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(tune_frame, text="Rate").pack(side=tk.LEFT)
        ttk.Entry(tune_frame, textvariable=self.rate_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Label(tune_frame, text="Pitch").pack(side=tk.LEFT)
        ttk.Entry(tune_frame, textvariable=self.pitch_var, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(tune_frame, text="Preview Selected", command=self.preview_selected).pack(side=tk.LEFT, padx=8)
        ttk.Button(tune_frame, text="Preview Current", command=self.preview_current).pack(side=tk.LEFT, padx=4)
        ttk.Button(tune_frame, text="Apply Selected", command=self.apply_selected).pack(side=tk.LEFT, padx=4)

        logs_frame = ttk.LabelFrame(main, text="Live Jarvis Output")
        logs_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        log_body = ttk.Frame(logs_frame)
        log_body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 0))
        self.log_text = tk.Text(log_body, height=10, wrap=tk.WORD, state=tk.DISABLED)
        log_scroll = ttk.Scrollbar(log_body, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        log_controls = ttk.Frame(logs_frame)
        log_controls.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(log_controls, text="Clear Output", command=self.clear_output).pack(side=tk.LEFT)
        ttk.Button(log_controls, text="Open Logs Folder", command=lambda: self.open_path(LOGS_DIR)).pack(side=tk.LEFT, padx=4)

        utilities = ttk.LabelFrame(main, text="Debug")
        utilities.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(utilities, text="Open Logs Folder", command=lambda: self.open_path(LOGS_DIR)).pack(side=tk.LEFT, padx=8, pady=8)
        ttk.Button(utilities, text="Open Config", command=lambda: self.open_path(CONFIG_PATH)).pack(side=tk.LEFT, padx=4)
        ttk.Button(utilities, text="Open Web UI", command=lambda: self.open_url("http://localhost:8080")).pack(side=tk.LEFT, padx=4)
        ttk.Label(utilities, textvariable=self.status_var).pack(side=tk.RIGHT, padx=8)

    def set_status(self, message: str) -> None:
        self.status_var.set(message)
        self.update_idletasks()

    def refresh_status(self) -> None:
        if self.assistant_process and self.assistant_process.poll() is not None:
            self.append_output(f"\n[Jarvis exited with code {self.assistant_process.returncode}]\n")
            self.assistant_process = None

        running_label = "Assistant: running" if assistant_running() else "Assistant: stopped"
        if self.assistant_process and self.assistant_process.poll() is None:
            running_label = "Assistant: running in UI"
        self.running_var.set(running_label)
        self.startup_var.set("Startup: installed" if startup_task_exists() else "Startup: not installed")
        self.after(5000, self.refresh_status)

    def load_voices(self) -> None:
        self.set_status("Loading voices...")
        self.voice_list.delete(0, tk.END)

        def worker() -> None:
            try:
                voices = asyncio.run(list_voices(self.filter_var.get()))
                self.after(0, lambda: self.show_voices(voices))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: messagebox.showerror("Voice Load Failed", message))

        threading.Thread(target=worker, daemon=True).start()

    def show_voices(self, voices: list[dict]) -> None:
        self.voices = voices
        self.voice_by_label.clear()
        self.voice_list.delete(0, tk.END)
        for voice in voices:
            short = voice.get("ShortName", "")
            label = f"{short} | {voice.get('Locale', '')} | {voice.get('Gender', '')}"
            self.voice_by_label[label] = short
            self.voice_list.insert(tk.END, label)
            if short == self.current_voice_var.get():
                self.voice_list.selection_clear(0, tk.END)
                self.voice_list.selection_set(tk.END)
                self.voice_list.see(tk.END)
        self.set_status(f"Loaded {len(voices)} voices")

    def selected_voice(self, *, show_message: bool = True) -> str | None:
        selection = self.voice_list.curselection()
        if not selection:
            if show_message:
                messagebox.showinfo("No Voice Selected", "Select a voice first.")
            return None
        label = self.voice_list.get(selection[0])
        return self.voice_by_label.get(label)

    def preview_selected(self) -> None:
        voice = self.selected_voice(show_message=False) or self.current_voice_var.get()
        if not voice:
            messagebox.showinfo("No Voice", "No voice is selected or configured.")
            return
        self.preview_voice(voice)

    def preview_current(self) -> None:
        voice = self.current_voice_var.get()
        if not voice:
            messagebox.showinfo("No Current Voice", "No current voice is configured.")
            return
        self.preview_voice(voice)

    def preview_voice(self, voice: str) -> None:
        self.set_status(f"Previewing {voice}...")
        self.append_output(f"\n[Previewing voice: {voice}]\n")

        def worker() -> None:
            try:
                tmp = LOGS_DIR / "voice-samples" / "_preview.mp3"
                text = self.sample_text.get().strip() or DEFAULT_TEXT
                asyncio.run(save_sample(voice, text, tmp, self.rate_var.get(), self.pitch_var.get()))
                play_mp3(tmp)
                self.after(0, lambda: self.set_status("Preview done"))
                self.after(0, lambda: self.append_output("[Preview finished]\n"))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self.set_status("Preview failed"))
                self.after(0, lambda: self.append_output(f"[Preview failed: {message}]\n"))
                self.after(0, lambda: messagebox.showerror("Preview Failed", message))

        threading.Thread(target=worker, daemon=True).start()

    def apply_selected(self) -> None:
        voice = self.selected_voice()
        if not voice:
            return
        try:
            update_config_value("edge_voice", voice)
            update_config_value("edge_rate", self.rate_var.get())
            update_config_value("edge_pitch", self.pitch_var.get())
            self.current_voice_var.set(voice)
            self.set_status("Voice saved. Restart Jarvis to use it.")
        except Exception as exc:
            messagebox.showerror("Save Failed", str(exc))

    def start_all(self) -> None:
        run_powershell(START_ALL)
        self.set_status("Starting full stack in background...")
        self.after(3000, self.refresh_status)

    def start_jarvis_in_ui(self) -> None:
        if self.assistant_process and self.assistant_process.poll() is None:
            self.set_status("Jarvis is already running in the UI.")
            return

        if assistant_running():
            if not messagebox.askyesno(
                "Jarvis Already Running",
                "A Jarvis voice process is already running. Stop it and restart inside the UI?",
            ):
                return
            self.stop_jarvis()
            time.sleep(1)

        if not VOICE_PYTHON.exists():
            messagebox.showerror("Missing Python", "Voice environment is missing. Run setup.ps1 first.")
            return

        self.clear_output()
        self.append_output("[Starting Jarvis in UI...]\n")
        try:
            self.assistant_process = subprocess.Popen(
                [str(VOICE_PYTHON), str(ASSISTANT)],
                cwd=str(VOICE_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            messagebox.showerror("Start Failed", str(exc))
            self.assistant_process = None
            return

        threading.Thread(target=self.read_assistant_output, daemon=True).start()
        self.set_status("Jarvis is running in the UI.")
        self.after(1000, self.refresh_status)

    def read_assistant_output(self) -> None:
        proc = self.assistant_process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self.after(0, lambda value=line: self.append_output(value))
        code = proc.poll()
        self.after(0, lambda: self.append_output(f"\n[Jarvis output stream closed, code={code}]\n"))

    def stop_all(self) -> None:
        self.stop_jarvis()

    def stop_jarvis(self) -> None:
        if self.assistant_process and self.assistant_process.poll() is None:
            self.append_output("\n[Stopping Jarvis...]\n")
        stop_voice_assistant()
        self.set_status("Stopping Jarvis...")
        self.after(3000, self.refresh_status)

    def install_startup(self) -> None:
        run_powershell(INSTALL_STARTUP)
        self.set_status("Installing startup task...")
        self.after(3000, self.refresh_status)

    def open_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer.exe", str(path)], creationflags=subprocess.CREATE_NO_WINDOW)

    def open_url(self, url: str) -> None:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-Command", f"Start-Process '{url}'"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def append_output(self, text: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_output(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def on_close(self) -> None:
        if self.assistant_process and self.assistant_process.poll() is None:
            keep_running = messagebox.askyesno(
                "Jarvis is running",
                "Jarvis is running inside this UI. Keep Jarvis running after closing the UI?",
            )
            if not keep_running:
                self.stop_all()
        self.destroy()


if __name__ == "__main__":
    JarvisControlPanel().mainloop()
