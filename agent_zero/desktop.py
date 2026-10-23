"""Native Electron shell around the Python-owned Agent Zero runtime."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time

from .server import call


PROJECT = Path(__file__).resolve().parent.parent


def electron_binary():
    dist = PROJECT / 'node_modules' / 'electron' / 'dist'
    if sys.platform == 'darwin':
        binary = dist / 'Electron.app' / 'Contents' / 'MacOS' / 'Electron'
    elif sys.platform == 'win32':
        binary = dist / 'electron.exe'
    else:
        binary = dist / 'electron'
    if not binary.is_file():
        raise RuntimeError(f'Install the free desktop runtime first: cd {PROJECT} && npm ci')
    return binary


def launch(config):
    binary = electron_binary()  # Fail before starting a daemon if not installed.
    async def running():
        try:
            return await call(config.root, 'status')
        except (OSError, RuntimeError, ValueError):
            return None
    if not asyncio.run(running()):
        config.prepare()
        log = config.root / 'daemon.log'
        with log.open('ab') as handle:
            process = subprocess.Popen([sys.executable, '-m', 'agent_zero', '--home', str(config.root), 'serve', '--graph'],
                                       stdout=handle, stderr=handle, start_new_session=True)
        for _ in range(100):
            if asyncio.run(running()):
                break
            if process.poll() is not None:
                raise RuntimeError(f'Daemon failed to start; inspect {log}')
            time.sleep(0.1)
        else:
            raise RuntimeError(f'Daemon startup timed out; inspect {log}')
    environment = dict(os.environ, AGENT_ZERO_HOME=str(config.root))
    environment.pop('ELECTRON_RUN_AS_NODE', None)
    descriptor = os.open(config.root / 'desktop.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'ab') as log:
        subprocess.Popen([str(binary), str(PROJECT)], cwd=PROJECT, env=environment,
                         stdout=log, stderr=log, start_new_session=True)


def build_app(config, destination):
    """Bundle Electron plus shell resources; Python still uses this checkout/venv."""
    if sys.platform != 'darwin':
        raise RuntimeError('The .app builder requires macOS')
    binary = electron_binary()
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise ValueError('App destination already exists; choose a new path')
    shutil.copytree(binary.parents[2], destination, symlinks=True)
    resources = destination / 'Contents' / 'Resources'
    (resources / 'default_app.asar').unlink(missing_ok=True)
    application = resources / 'app'
    application.mkdir()
    for name in ('main.cjs', 'policy.cjs'):
        shutil.copyfile(PROJECT / 'desktop' / name, application / name)
    (application / 'package.json').write_text(json.dumps({'name': 'agent-zero', 'version': '0.3.0', 'main': 'main.cjs'}))
    (application / 'launch.json').write_text(json.dumps({'project': str(PROJECT), 'home': str(config.root), 'python': sys.executable}))
    plist = destination / 'Contents' / 'Info.plist'
    with plist.open('rb') as handle:
        info = plistlib.load(handle)
    info.update(CFBundleName='Agent Zero', CFBundleDisplayName='Agent Zero',
                CFBundleIdentifier='local.agentzero.desktop', CFBundleVersion='3',
                CFBundleShortVersionString='0.3.0',
                NSMicrophoneUsageDescription='Speak locally to JARVIS using offline transcription.')
    with plist.open('wb') as handle:
        plistlib.dump(info, handle)
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(destination)], check=True, capture_output=True)
    return destination
