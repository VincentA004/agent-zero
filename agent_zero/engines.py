from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request

from .local_http import request_local_json
from .openrouter_setup import load_key


class ProviderError(RuntimeError):
    def __init__(self, status, message="Inference provider rejected the request"):
        self.status = status
        super().__init__(f"{message} (HTTP {status})")


def request_json(url, payload, headers=None, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        message = "Inference provider rejected the request"
        try:
            error = json.loads(exc.read(16000)).get("error", {})
            if isinstance(error.get("message"), str):
                message = error["message"]
        except (ValueError, AttributeError):
            pass
        finally:
            exc.close()
        authorization = (headers or {}).get("Authorization", "")
        if authorization.startswith("Bearer "):
            message = message.replace(authorization[7:], "[REDACTED]")
        raise ProviderError(exc.code, message[:300]) from None
    except urllib.error.URLError:
        raise RuntimeError("Inference provider unavailable; check local configuration") from None


class OpenRouter:
    def __init__(self, config):
        self.config = config
        self.spent = 0.0
        self.tokens = 0
        self.billing_fault = False

    async def chat(self, model, messages, record, tools=None):
        if not self.config.cloud_enabled:
            raise PermissionError("Cloud inference is disabled")
        if self.billing_fault:
            raise PermissionError("OpenRouter is blocked after unexpected billing on a free-only request")
        if self.config.cloud_free_only and (not record or not record.get("free") or not model.endswith(":free")
                or record.get("input_per_million") != 0 or record.get("output_per_million") != 0):
            raise PermissionError("Free-only mode rejects paid models before networking")
        key = load_key(self.config)
        if not key:
            raise ValueError("OPENROUTER_API_KEY is not configured")
        if not record or not record.get("verified") or record.get("id") != model:
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
        if self.config.cloud_free_only:
            payload["provider"]["max_price"].update(request=0, image=0)
        if record.get("disable_reasoning"):
            payload["reasoning"] = {"enabled": False}
        if tools:
            payload["tools"] = tools
        attempts = 3 if self.config.cloud_free_only else 1
        for attempt in range(attempts):
            try:
                result = await asyncio.to_thread(request_json, "https://openrouter.ai/api/v1/chat/completions", payload,
                                                 {"Authorization": f"Bearer {key}"})
                # OpenRouter may return an upstream failure in an HTTP 200 body.
                if isinstance(result, dict) and isinstance(result.get("error"), dict):
                    error = result["error"]
                    cost = result.get("usage", {}).get("cost", 0)
                    if self.config.cloud_free_only and isinstance(cost, (int, float)) and cost > 0:
                        self.spent += cost
                        self.billing_fault = True
                        raise RuntimeError("Unexpected free-route billing on an error response; requests blocked")
                    code = error.get("code", 0)
                    code = code if type(code) is int else 0
                    message = str(error.get("message", "Inference provider returned an error")).replace(key, "[REDACTED]")[:300]
                    raise ProviderError(code, message)
                break
            except ProviderError as exc:
                # Only explicit overload failures are retried, only in free mode.
                # Never retry authentication, quota/rate-limit or ambiguous errors.
                if exc.status not in (502, 503) or attempt + 1 >= attempts:
                    raise
                await asyncio.sleep((2, 6)[attempt])
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list) or not result["choices"]:
            raise RuntimeError("Inference provider returned no completion")
        usage = result.get("usage", {})
        self.tokens += usage.get("total_tokens", 0)
        actual = usage.get("cost")
        if isinstance(actual, (int, float)) and actual >= 0:
            self.spent += actual - reserve
            if self.config.cloud_free_only and actual > 0:
                self.billing_fault = True
                raise RuntimeError("OpenRouter reported unexpected nonzero cost on a free-only request; further requests blocked")
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
