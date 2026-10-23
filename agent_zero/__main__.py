from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
from contextlib import AsyncExitStack
from pathlib import Path

from .config import Config
from .runtime import Runtime
from .security import Auth
from .server import Server, call


async def serve(config, graph=False):
    runtime = Runtime(config)
    async with AsyncExitStack() as stack:
        if graph:
            try:
                from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
                from .graph import TrapGraph
            except ImportError:
                raise RuntimeError("Install the agent extra first: pip install -e '.[agent]'") from None
            saver = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(config.root / "graph.sqlite")))
            runtime.graph = TrapGraph(runtime, saver)
        elif runtime.store.pending():
            # Do not silently resolve checkpoints belonging to an optional graph.
            if (config.root / "graph.sqlite").exists():
                raise RuntimeError("Pending graph checkpoints exist; restart with --graph")
        try:
            await Server(runtime).serve()
        finally:
            await runtime.processes.interrupt_all()
            await runtime.audio.transition("userspace")
            runtime.store.db.close()


def main():
    parser = argparse.ArgumentParser(description="Agent Zero local-first runtime")
    parser.add_argument("--home", type=Path, help="Runtime directory (default ~/.agent-zero)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Set or replace the kernel passphrase locally")
    start = sub.add_parser("serve", help="Start the local daemon and visual interface")
    start.add_argument("--port", type=int)
    start.add_argument("--graph", action="store_true", help="Enable durable LangGraph traps (agent extra required)")
    sub.add_parser("desktop", help="Launch JARVIS in a desktop app window")
    bundle = sub.add_parser("build-app", help="Build a double-clickable macOS launcher for this checkout")
    bundle.add_argument("--output", type=Path, default=Path("dist/Agent Zero.app"))
    sub.add_parser("status", help="Read daemon state over Unix IPC")
    tui = sub.add_parser("tui", help="Open Cardinal's native Textual interface")
    tui.add_argument("--voice-model", type=Path, help="Locally installed faster-whisper model directory; enables explicit F2 spoken auth")
    args = parser.parse_args()
    try:
        os.umask(0o077)
        config = Config.load(args.home)
        config.prepare()
        if args.command == "init":
            first = getpass.getpass("New kernel passphrase (12+ characters): ")
            if first != getpass.getpass("Repeat passphrase: "):
                raise ValueError("Passphrases do not match")
            Auth(config.root / "credentials.json").setup(first)
            print("Kernel passphrase stored as a salted scrypt hash. No API key or model is required.")
        elif args.command == "serve":
            if args.port is not None:
                if not 1 <= args.port <= 65535:
                    raise ValueError("port must be 1..65535")
                config.port = args.port
            asyncio.run(serve(config, args.graph))
        elif args.command == "desktop":
            from .desktop import launch
            launch(config)
        elif args.command == "build-app":
            from .desktop import build_app
            print(build_app(config, args.output))
        elif args.command == "status":
            print(json.dumps(asyncio.run(call(config.root, "status")), indent=2))
        elif args.command == "tui":
            from .tui import launch
            launch(config.root, args.voice_model or config.stt_model_path)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"agentd: {exc}\n")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
