from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Config:
    root: Path
    host: str = "127.0.0.1"
    port: int = 8420
    cloud_enabled: bool = False
    cloud_free_only: bool = False
    model: str = ""
    local_enabled: bool = False
    local_model: str = "qwen3.5:4b"
    max_agent_steps: int = 6
    max_parallel_inference: int = 2
    max_child_agents: int = 4
    max_agent_depth: int = 2
    max_agent_births: int = 12
    local_memory_gb: int = 12
    ollama_url: str = "http://127.0.0.1:11434"
    max_output_tokens: int = 1024
    session_budget_usd: float = 0.25
    stt_model_path: str | None = None

    def __post_init__(self):
        self.root = self.root.expanduser().resolve()
        for name, maximum in (("max_parallel_inference", 4), ("max_child_agents", 16), ("max_agent_depth", 4), ("max_agent_births", 64)):
            if type(getattr(self, name)) is not int or not 1 <= getattr(self, name) <= maximum:
                raise ValueError(f"{name} must be 1..{maximum}")
        if self.stt_model_path is not None and (not isinstance(self.stt_model_path, str) or not self.stt_model_path.strip()):
            raise ValueError("stt_model_path must be a local directory path or null")
        if type(self.local_enabled) is not bool or type(self.max_agent_steps) is not int or not 1 <= self.max_agent_steps <= 12:
            raise ValueError("local_enabled must be boolean and max_agent_steps must be 1..12")
        if not isinstance(self.local_model, str) or not self.local_model.startswith("qwen") or ":cloud" in self.local_model:
            raise ValueError("Local inference requires a local Qwen model")
        if type(self.cloud_free_only) is not bool:
            raise ValueError("cloud_free_only must be a JSON boolean")
        if type(self.cloud_enabled) is not bool:
            raise ValueError("cloud_enabled must be a JSON boolean")
        if self.host != "127.0.0.1":
            raise ValueError("Only loopback binding is supported")
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("port must be an integer between 1 and 65535")
        if type(self.local_memory_gb) is not int or not 8 <= self.local_memory_gb <= 16:
            raise ValueError("Local inference budget must be between 8 and 16 GB")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 8192:
            raise ValueError("max_output_tokens must be 1..8192")
        if type(self.session_budget_usd) not in (float, int) or not 0 <= self.session_budget_usd <= 100 or (self.session_budget_usd == 0 and not self.cloud_free_only):
            raise ValueError("session_budget_usd must be positive and at most 100, or zero in free-only mode")
        local = urlsplit(self.ollama_url)
        if local.scheme != "http" or local.hostname not in ("127.0.0.1", "localhost", "::1") or local.username or local.password or local.path or local.query or local.fragment:
            raise ValueError("ollama_url must be an HTTP loopback origin without a path")

    @classmethod
    def load(cls, root: Path | None = None):
        root = (root or Path(os.environ.get("AGENT_ZERO_HOME", "~/.agent-zero"))).expanduser().resolve()
        path = root / "config.json"
        data = json.loads(path.read_text()) if path.exists() else {}
        allowed = {k for k in cls.__dataclass_fields__ if k != "root"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown configuration fields: {', '.join(sorted(unknown))}")
        config = cls(root=root, **data)
        return config

    def prepare(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root / "sandbox").mkdir(exist_ok=True, mode=0o700)
