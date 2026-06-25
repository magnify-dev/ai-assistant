#!/usr/bin/env python3
"""Launch Firefox with the Jarvis bridge extension temporarily installed."""

from __future__ import annotations

import configparser
import os
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = ROOT / "firefox-extension"
DIST_DIR = ROOT / "dist"
XPI_PATH = DIST_DIR / "jarvis-page-bridge.xpi"
DEFAULT_PROFILE = ROOT / "logs" / "jarvis-firefox-profile"


def find_firefox_exe() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES", "")) / "Mozilla Firefox/firefox.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Mozilla Firefox/firefox.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Mozilla Firefox/firefox.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def firefox_profiles_ini() -> Path:
    return Path(os.environ["APPDATA"]) / "Mozilla/Firefox/profiles.ini"


def find_default_profile() -> Path:
    profiles_ini = firefox_profiles_ini()
    if not profiles_ini.exists():
        raise FileNotFoundError(f"Firefox profiles.ini not found: {profiles_ini}")

    parser = configparser.ConfigParser()
    parser.read(profiles_ini, encoding="utf-8")
    base = profiles_ini.parent

    install_default = None
    for section in parser.sections():
        if section.startswith("Install") and parser.has_option(section, "Default"):
            install_default = parser.get(section, "Default")
            break

    chosen_section = None
    for section in parser.sections():
        if not section.startswith("Profile"):
            continue
        path_value = parser.get(section, "Path", fallback="")
        if install_default and path_value.replace("\\", "/") == install_default.replace("\\", "/"):
            chosen_section = section
            break
        if parser.get(section, "Default", fallback="0") == "1":
            chosen_section = section

    if not chosen_section:
        for section in parser.sections():
            if section.startswith("Profile"):
                chosen_section = section
                break

    if not chosen_section:
        raise RuntimeError("No Firefox profile found")

    raw_path = parser.get(chosen_section, "Path")
    is_relative = parser.get(chosen_section, "IsRelative", fallback="1") == "1"
    profile = (base / raw_path) if is_relative else Path(raw_path)
    if not profile.exists():
        raise FileNotFoundError(f"Firefox profile not found: {profile}")
    return profile.resolve()


def bridge_profile_dir() -> Path:
    raw = os.environ.get("JARVIS_FIREFOX_PROFILE_DIR")
    profile = Path(raw).expanduser() if raw else DEFAULT_PROFILE
    profile.mkdir(parents=True, exist_ok=True)
    return profile.resolve()


def package_extension() -> Path:
    if not EXTENSION_DIR.exists():
        raise FileNotFoundError(f"Extension directory not found: {EXTENSION_DIR}")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if XPI_PATH.exists():
        XPI_PATH.unlink()

    with zipfile.ZipFile(XPI_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in EXTENSION_DIR.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(EXTENSION_DIR))
    return XPI_PATH


def main() -> int:
    initial_url = sys.argv[1] if len(sys.argv) > 1 else ""
    firefox = find_firefox_exe()
    if not firefox:
        print("Firefox executable not found.", flush=True)
        return 1

    profile = bridge_profile_dir()
    xpi = package_extension()

    print(f"Firefox: {firefox}", flush=True)
    print(f"Profile: {profile}", flush=True)
    print(f"Extension: {xpi}", flush=True)
    print("Starting Firefox with the Jarvis bridge extension.", flush=True)

    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options

    options = Options()
    options.binary_location = str(firefox)
    options.add_argument("-profile")
    options.add_argument(str(profile))
    options.add_argument("-no-remote")

    driver = webdriver.Firefox(options=options)
    try:
        addon_id = driver.install_addon(str(xpi), temporary=True)
        print(f"Installed temporary Jarvis Firefox extension: {addon_id}", flush=True)
        if initial_url:
            driver.get(initial_url)
            print(f"Opened initial URL: {initial_url}", flush=True)
        print("Firefox bridge is ready. Use Firefox normally and Jarvis can read/click the current page.", flush=True)

        while True:
            try:
                _ = driver.title
            except Exception:
                print("Firefox closed.", flush=True)
                break
            time.sleep(2)
    except KeyboardInterrupt:
        print("Stopping Firefox bridge.", flush=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
