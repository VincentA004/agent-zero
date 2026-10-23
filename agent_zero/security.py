from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from pathlib import Path


class Auth:
    """Passphrases never enter agent state. Tokens expire and are daemon-local."""

    def __init__(self, path: Path, ttl: int = 300):
        self.path = path
        self.ttl = ttl
        self.tokens: dict[str, float] = {}
        self.failures = 0
        self.retry_at = 0.0

    @property
    def configured(self):
        return self.path.exists()

    def setup(self, passphrase: str):
        if len(passphrase) < 12:
            raise ValueError("Use a passphrase of at least 12 characters")
        salt = secrets.token_hex(32)
        digest = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
        self.path.write_text(json.dumps({"salt": salt, "digest": digest}))
        self.path.chmod(0o600)
        self.tokens.clear()

    def authenticate(self, passphrase: str) -> str:
        if time.monotonic() < self.retry_at:
            raise PermissionError("Authentication temporarily locked; try again shortly")
        if not self.configured:
            raise PermissionError("Configure your passphrase in a terminal using the setup command shown in Cardinal")
        record = json.loads(self.path.read_text())
        digest = hashlib.scrypt(passphrase.encode(), salt=bytes.fromhex(record["salt"]), n=16384, r=8, p=1).hex()
        if not hmac.compare_digest(digest, record["digest"]):
            self.failures += 1
            self.retry_at = time.monotonic() + min(60, 2 ** self.failures)
            raise PermissionError("Passphrase rejected")
        self.failures = 0
        self.retry_at = 0
        now = time.monotonic()
        self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > now}
        token = secrets.token_urlsafe(32)
        # One active operator session owns the global audio/privilege mode.
        self.tokens.clear()
        self.tokens[token] = now + self.ttl
        return token

    def expire(self):
        now = time.monotonic()
        self.tokens = {key: expiry for key, expiry in self.tokens.items() if expiry > now}
        return bool(self.tokens)

    def require(self, token: str):
        if self.tokens.get(token, 0) <= time.monotonic():
            self.tokens.pop(token, None)
            raise PermissionError("Kernel authorization required or expired")

    def revoke(self, token: str):
        self.tokens.pop(token, None)
