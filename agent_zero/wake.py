"""Deterministic wake phrase matching after offline speech recognition."""
from __future__ import annotations

import re


WAKE_PHRASE = "hey jarvis"
_PHRASE = re.compile(r"^\W*hey[\s,;:…\-]+jarvis\b", re.IGNORECASE)


def detect_wake(text: str) -> dict:
    """Match a leading phrase, preserving a request spoken in the same breath.

    Ambient nonmatches are not written to conversation history or audit logs.
    """
    match = _PHRASE.match(text.strip())
    if not match:
        return {"awake": False, "command": ""}
    command = text.strip()[match.end():].lstrip(" \t\r\n,.:;!?—–-")
    return {"awake": True, "command": command}


def sleep_action(text: str) -> str | None:
    normalized = text.casefold().strip().rstrip(".!?")
    if normalized in ("go to sleep", "sleep jarvis", "go to sleep jarvis"):
        return "sleep"
    if normalized in ("stop listening", "disable wake word", "disable wake phrase"):
        return "stop"
    return None


def kernel_request(text: str) -> bool:
    """Recognize explicit mode changes locally; never infer authorization."""
    normalized = re.sub(r"\s+", " ", text.casefold().strip().rstrip(".!?")).strip()
    if normalized == "/kernel":
        return True
    return bool(re.fullmatch(
        r"(?:jarvis[, ]+)?(?:(?:can|could|would) you |please )?"
        r"(?:(?:system call:? )?enter|go (?:in|into)|switch to|drop (?:down )?(?:to|into)|run in) "
        r"(?:the )?(?:kernel(?: mode)?|cardinal(?: mode)?)(?: please)?", normalized))
