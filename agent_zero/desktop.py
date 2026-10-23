"""Double-clickable macOS launcher using Chrome's microphone-capable app window."""
from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import time
import webbrowser

from .config import Config
from .server import call


def launch(config):
    async def running():
        try:
            return await call(config.root, "status")
        except (OSError, RuntimeError, ValueError):
            return None
    if not asyncio.run(running()):
        log = config.root / "daemon.log"
        with log.open("ab") as handle:
            process = subprocess.Popen([sys.executable, "-m", "agent_zero", "--home", str(config.root), "serve", "--graph"],
                                       stdout=handle, stderr=handle, start_new_session=True)
        for _ in range(100):
            if asyncio.run(running()):
                break
            if process.poll() is not None:
                raise RuntimeError(f"Daemon failed to start; inspect {log}")
            time.sleep(0.1)
        else:
            raise RuntimeError(f"Daemon startup timed out; inspect {log}")
    url = f"http://{config.host}:{config.port}"
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if sys.platform == "darwin" and chrome.exists():
        # A separate profile keeps permissions/state independent of personal browsing.
        subprocess.Popen([str(chrome), f"--app={url}", f"--user-data-dir={config.root / 'browser'}",
                          "--no-first-run", "--no-default-browser-check", "--window-size=1080,850"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    else:
        webbrowser.open(url)


def build_app(config, destination):
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError("App destination already exists; choose a new path")
    macos = destination / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    executable = macos / "Agent Zero"
    project = Path(__file__).resolve().parent.parent
    command = shlex.join([sys.executable, "-m", "agent_zero", "--home", str(config.root), "desktop"])
    executable.write_text(f"#!/bin/sh\ncd {shlex.quote(str(project))} || exit 1\nexec {command}\n")
    executable.chmod(0o755)
    with (destination / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump({"CFBundleName": "Agent Zero", "CFBundleDisplayName": "Agent Zero",
                      "CFBundleIdentifier": "local.agentzero.desktop", "CFBundleVersion": "1",
                      "CFBundleShortVersionString": "0.2.0", "CFBundlePackageType": "APPL",
                      "CFBundleExecutable": "Agent Zero", "LSMinimumSystemVersion": "13.0",
                      "NSMicrophoneUsageDescription": "Speak locally to JARVIS using offline transcription."}, handle)
    return destination
