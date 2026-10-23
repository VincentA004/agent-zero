from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

from .local_http import request_local_json


def request_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Inference provider returned HTTP {exc.code}") from None
    except urllib.error.URLError:
        raise RuntimeError("Inference provider unavailable; check local configuration") from None


class OpenRouter:
    def __init__(self, config):
        self.config = config
        self.spent = 0.0
        self.tokens = 0

    async def chat(self, model, messages, record, tools=None):
        if not self.config.cloud_enabled:
            raise PermissionError("Cloud inference is disabled")
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        if not record or not record.get("verified"):
            raise ValueError("Select a verified registry model before enabling cloud inference")
        # Conservative UTF-8 byte upper bound for input tokens; reserve max output.
        input_bound = len(json.dumps(messages).encode()) + len(json.dumps(tools or []).encode()) + 1024
        reserve = (input_bound * record["input_per_million"] + self.config.max_output_tokens * record["output_per_million"]) / 1_000_000
        if self.spent + reserve > self.config.session_budget_usd:
            raise PermissionError("Session cost ceiling would be exceeded")
        self.spent += reserve  # retain reservation on ambiguous network failures
        clean_messages = [{k: v for k, v in m.items() if k != "tool_name"} for m in messages]
        payload = {
            "model": model, "messages": clean_messages, "max_tokens": self.config.max_output_tokens,
            "provider": {"allow_fallbacks": False, "require_parameters": True,
                         "max_price": {"prompt": record["input_per_million"], "completion": record["output_per_million"]}},
            "usage": {"include": True},
        }
        if tools:
            payload["tools"] = tools
        result = await asyncio.to_thread(request_json, "https://openrouter.ai/api/v1/chat/completions", payload,
                                         {"Authorization": f"Bearer {key}"})
        usage = result.get("usage", {})
        self.tokens += usage.get("total_tokens", 0)
        actual = usage.get("cost")
        if isinstance(actual, (int, float)) and actual >= 0:
            self.spent += actual - reserve
        message = result["choices"][0]["message"]
        if tools and (isinstance(message.get("content"), str) or message.get("tool_calls")):
            return {**message, "content": message.get("content") or ""}
        content = message.get("content")
        if not isinstance(content, str):
            raise RuntimeError("Provider returned no text response")
        return content


class Ollama:
    def __init__(self, config):
        self.config = config
        self.metrics = {}

    def _preflight(self):
        try:
            import psutil
            if psutil.virtual_memory().available < 1_500_000_000:
                raise RuntimeError("Local inference paused: less than 1.5 GB host memory available")
        except ImportError:
            pass
        # Never pull a model, contact an Ollama cloud tag, or silently change models.
        with urllib.request.urlopen(self.config.ollama_url + "/api/ps", timeout=3) as response:
            models = json.load(response).get("models", [])
        target = next((m for m in models if m.get("name") == self.config.local_model), None)
        if target:
            resident = target.get("size", 0)
            self.metrics["resident_gb"] = round(resident / 1024**3, 2)
            if resident > self.config.local_memory_gb * 1024**3:
                raise RuntimeError("Loaded local model exceeds the configured memory budget")

    async def turn(self, messages, tools=None):
        # Native tool calling; disabling thinking keeps spoken answers responsive.
        await asyncio.to_thread(self._preflight)
        payload = {
            "model": self.config.local_model, "messages": messages, "stream": False,
            "think": False, "keep_alive": "5m",
            "options": {"temperature": 0.2, "num_ctx": 4096,
                        "num_predict": self.config.max_output_tokens},
        }
        if tools:
            payload["tools"] = tools
        result = await request_local_json(self.config.ollama_url + "/api/chat", payload)
        self.metrics.update(output_tokens=result.get("eval_count", 0),
                            inference_seconds=round(result.get("total_duration", 0) / 1e9, 3))
        await asyncio.to_thread(self._preflight)
        message = result.get("message", {})
        if not isinstance(message, dict) or not isinstance(message.get("content", ""), str):
            raise RuntimeError("Local model returned an invalid message")
        return message

    async def chat(self, model, messages, record=None):
        return (await self.turn(messages)).get("content", "")

    async def parse(self, text):
        result = await asyncio.to_thread(request_json, self.config.ollama_url + "/api/chat", {
            "model": self.config.local_model, "stream": False, "keep_alive": "5m", "format": "json", "think": False,
            "options": {"temperature": 0, "num_ctx": 4096, "num_predict": 256},
            "messages": [{"role": "system", "content": 'Extract an admin request as JSON {"syscall": "sys_proc_table" | "sys_model_swap" | "sys_mem_flush", "args": {}}. Model swaps require target_model_id, flush requires scope. Never authorize or execute. For ambiguous requests return {"syscall":"unknown","args":{}}.'}, {"role": "user", "content": text}],
        })
        proposal = json.loads(result["message"]["content"])
        if proposal.get("syscall") not in {"sys_proc_table", "sys_model_swap", "sys_mem_flush"} or not isinstance(proposal.get("args"), dict):
            raise ValueError("Local model did not produce an allowed syscall proposal")
        return proposal
