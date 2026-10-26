from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit


class Server:
    def __init__(self, runtime):
        self.runtime = runtime
        self.web = Path(__file__).with_name("web")
        self.browser_key = secrets.token_urlsafe(32)
        self.origin = f"http://{runtime.config.host}:{runtime.config.port}"

    async def rpc(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Request must be an object")
        method = payload.get("method")
        if not isinstance(method, str):
            raise ValueError("method is required")
        return await self.runtime.dispatch(method, payload.get("params"), payload.get("token", ""))

    async def unix_client(self, reader, writer):
        try:
            data = await asyncio.wait_for(reader.readline(), 10)
            result = await self.rpc(json.loads(data))
            response = {"ok": True, "result": result}
        except (ValueError, PermissionError, RuntimeError, OSError, TimeoutError) as exc:
            response = {"ok": False, "error": str(exc)}
        except Exception:
            response = {"ok": False, "error": "Internal runtime error; inspect daemon logs"}
        writer.write(json.dumps(response).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def http_client(self, reader, writer):
        status, content_type, body = 200, "application/json", b""
        extra = []
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = header.decode("ascii").split("\r\n")
            verb, raw_path, version = lines[0].split()
            headers = {}
            for line in lines[1:]:
                if line:
                    key, value = line.split(":", 1)
                    key = key.lower()
                    if key in headers:
                        raise ValueError("Duplicate HTTP header")
                    headers[key] = value.strip()
            if headers.get("host") != urlsplit(self.origin).netloc:
                raise PermissionError("Invalid host")
            path = urlsplit(raw_path).path
            if verb == "GET" and path == "/api/status":
                # Refresh the same-origin browser session after a daemon restart.
                # Mutations still require exact Host/Origin and the custom header.
                extra.append(f"Set-Cookie: az_session={self.browser_key}; HttpOnly; SameSite=Strict; Path=/")
                body = json.dumps({"ok": True, "result": self.runtime.snapshot()}).encode()
            elif verb == "POST" and path == "/api/rpc":
                if headers.get("origin") != self.origin or headers.get("x-agent-zero") != "1":
                    raise PermissionError("Same-origin browser request required")
                cookies = dict(part.strip().split("=", 1) for part in headers.get("cookie", "").split(";") if "=" in part)
                if not secrets.compare_digest(cookies.get("az_session", ""), self.browser_key):
                    raise PermissionError("Open the local Agent Zero interface first")
                if "transfer-encoding" in headers:
                    raise ValueError("Transfer encoding is unsupported")
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 1_050_000:
                    raise ValueError("Request exceeds 1MB or is empty")
                data = await asyncio.wait_for(reader.readexactly(length), 10)
                body = json.dumps({"ok": True, "result": await self.rpc(json.loads(data))}).encode()
            elif verb == "GET" and path in ("/", "/style.css", "/orb.css", "/cardinal.css", "/app.js", "/voice.js", "/favicon.svg"):
                file = self.web / ("index.html" if path == "/" else path[1:])
                body = file.read_bytes()
                content_type = mimetypes.guess_type(file.name)[0] or "text/plain"
                if path == "/":
                    extra.append(f"Set-Cookie: az_session={self.browser_key}; HttpOnly; SameSite=Strict; Path=/")
            else:
                status, body = 404, b'{"ok":false,"error":"Not found"}'
        except PermissionError as exc:
            status, body = 403, json.dumps({"ok": False, "error": str(exc)}).encode()
        except (ValueError, OSError, RuntimeError) as exc:
            status, body = 400, json.dumps({"ok": False, "error": str(exc)}).encode()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, body = 400, b'{"ok":false,"error":"Incomplete or oversized request"}'
        except Exception:
            status, body = 500, b'{"ok":false,"error":"Internal runtime error"}'
        try:
            reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 500: "Internal Server Error"}[status]
            security = ["Cache-Control: no-store", "X-Content-Type-Options: nosniff", "X-Frame-Options: DENY", "Referrer-Policy: no-referrer", "Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"]
            head = "\r\n".join([f"HTTP/1.1 {status} {reason}", f"Content-Type: {content_type}; charset=utf-8", f"Content-Length: {len(body)}", "Connection: close", *security, *extra, "", ""])
            writer.write(head.encode() + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def serve(self):
        import fcntl
        # Hold an OS lock before touching the socket or starting a second daemon.
        lock_file = (self.runtime.config.root / "daemon.lock").open("a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            raise RuntimeError("Another agentd instance owns this runtime directory") from None
        path = self.runtime.config.root / "agentd.sock"
        try:
            path.unlink(missing_ok=True)
            unix = await asyncio.start_unix_server(self.unix_client, str(path), limit=65536)
            path.chmod(0o600)
            try:
                http = await asyncio.start_server(self.http_client, self.runtime.config.host, self.runtime.config.port, limit=65536)
                print(f"Agent Zero · {self.origin} · cloud {'ENABLED' if self.runtime.config.cloud_enabled else 'disabled / local Qwen' if self.runtime.config.local_enabled else 'disabled / free preview'}", flush=True)
                async with unix, http:
                    tasks = [asyncio.create_task(unix.serve_forever()), asyncio.create_task(http.serve_forever()), asyncio.create_task(self.maintenance())]
                    try:
                        await asyncio.gather(*tasks)
                    finally:
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                unix.close()
                await unix.wait_closed()
        finally:
            path.unlink(missing_ok=True)
            lock_file.close()

    async def maintenance(self):
        while True:
            await asyncio.sleep(1)
            async with self.runtime.lock:
                await self.runtime.expire_authority()
                self.runtime.scheduler.tick()


async def call(root: Path, method, params=None, token=""):
    reader, writer = await asyncio.open_unix_connection(str(root / "agentd.sock"), limit=2_000_000)
    try:
        writer.write(json.dumps({"method": method, "params": params or {}, "token": token}).encode() + b"\n")
        await writer.drain()
        result = json.loads(await asyncio.wait_for(reader.readline(), 90))
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return result["result"]
    finally:
        writer.close()
        await writer.wait_closed()
