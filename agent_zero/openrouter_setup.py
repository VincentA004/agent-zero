"""Explicit local setup; API keys stay outside prompts, browser state and Git."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request

FREE_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


def validate_key(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 1024:
        raise ValueError("Enter a nonempty OpenRouter API key")
    key = value.strip()
    if any(char.isspace() or ord(char) < 32 for char in key):
        raise ValueError("API key contains invalid whitespace")
    return key


def load_key(config):
    path = config.root / "openrouter.key"
    if path.exists():
        if path.is_symlink() or path.stat().st_size > 1024:
            raise ValueError("Invalid local OpenRouter key file")
        return validate_key(path.read_text())
    value = os.environ.get("OPENROUTER_API_KEY")
    return validate_key(value) if value else None


def verify_key(key):
    request = urllib.request.Request("https://openrouter.ai/api/v1/key", headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"OpenRouter rejected the key (HTTP {exc.code}); nothing was saved") from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Could not reach OpenRouter; nothing was saved") from None
    if not isinstance(result.get("data"), dict):
        raise ValueError("OpenRouter returned an invalid key response; nothing was saved")


def private_write(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix=".setup-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(text)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(config, supplied_key):
    key = validate_key(supplied_key)
    verify_key(key)  # read-only authentication check; no inference or credit purchase
    config.prepare()
    path = config.root / "config.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    data.update(cloud_enabled=True, cloud_free_only=True, local_enabled=False,
                model=FREE_MODEL, session_budget_usd=0)
    private_write(config.root / "openrouter.key", key + "\n")
    private_write(path, json.dumps(data, indent=2) + "\n")
