"""Cancellable loopback-only HTTP client for local inference."""
from __future__ import annotations
import asyncio
import json
from urllib.parse import urlsplit


async def request_local_json(url, payload, timeout=180):
    target = urlsplit(url)
    if target.scheme != "http" or target.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Local inference transport only accepts loopback HTTP")
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(target.hostname, target.port or 80, limit=65536)
            body = json.dumps(payload).encode()
            header = (f"POST {target.path} HTTP/1.1\r\nHost: {target.netloc}\r\n"
                      f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n")
            writer.write(header.encode() + body)
            await writer.drain()
            lines = (await reader.readuntil(b"\r\n\r\n")).decode("ascii").split("\r\n")
            if lines[0].split()[1] != "200":
                raise RuntimeError("Local inference provider rejected the request; check the installed model")
            headers = dict(line.lower().split(":", 1) for line in lines[1:] if ":" in line)
            limit = 4_000_000
            if "chunked" in headers.get("transfer-encoding", ""):
                parts, size = [], 0
                while True:
                    length = int((await reader.readline()).split(b";", 1)[0], 16)
                    if not length:
                        break
                    size += length
                    if size > limit:
                        raise ValueError("Local response exceeds the size limit")
                    parts.append(await reader.readexactly(length))
                    if await reader.readexactly(2) != b"\r\n":
                        raise ValueError("Malformed local HTTP response")
                response = b"".join(parts)
            elif "content-length" in headers:
                size = int(headers["content-length"])
                if not 0 <= size <= limit:
                    raise ValueError("Local response exceeds the size limit")
                response = await reader.readexactly(size)
            else:
                parts, size = [], 0
                while part := await reader.read(65536):
                    size += len(part)
                    if size > limit:
                        raise ValueError("Local response exceeds the size limit")
                    parts.append(part)
                response = b"".join(parts)
            return json.loads(response)
    except (OSError, asyncio.IncompleteReadError) as exc:
        raise RuntimeError("Local inference unavailable; check that Ollama is running") from None
    finally:
        if writer:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, TimeoutError):
                pass
