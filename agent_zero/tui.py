from __future__ import annotations

import json
import asyncio
from pathlib import Path

from .config import Config
from .server import call


def create_app(root: Path, voice_model: Path | None = None):
    try:
        from textual.app import App, ComposeResult
        from textual.containers import Horizontal, Vertical
        from textual.widgets import DataTable, Footer, Header, Input, RichLog, Static
    except ImportError:
        raise RuntimeError("Install the TUI extra: pip install -e '.[tui]'") from None

    class Cardinal(App):
        TITLE = "CARDINAL // AGENT ZERO"
        SUB_TITLE = "RING 0 · LOCAL AUTHORITY"
        CSS = """
        Screen { background: #060b08; color: #86eda5; }
        Header { background: #102019; color: #bcffb7; }
        #telemetry { height: 7; border: solid #254f37; padding: 1 2; }
        #body { height: 1fr; }
        #left { width: 40%; border: solid #254f37; }
        #right { width: 60%; border: solid #254f37; }
        DataTable { height: 1fr; background: #060b08; }
        RichLog { height: 1fr; background: #060b08; }
        Input { border: solid #54754b; }
        #help { height: 3; padding: 0 1; color: #d3ad66; }
        Footer { background: #102019; }
        """
        BINDINGS = [("ctrl+q", "quit", "Close console"), ("ctrl+l", "lock", "Drop authority"), ("f2", "voice_auth", "Speak passphrase")]

        def __init__(self):
            super().__init__()
            self.token = ""
            self.busy = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            yield Static("Connecting to agentd…", id="telemetry")
            with Horizontal(id="body"):
                with Vertical(id="left"):
                    yield Static("  AGENT PROCESS TABLE")
                    yield DataTable(id="tasks")
                with Vertical(id="right"):
                    yield Static("  KERNEL EVENT STREAM")
                    yield RichLog(id="log", wrap=True, markup=False)
            yield Static("Authenticate below. Commands: ps · tree · spawn TASK · fork TASK · exec/wait/reap PID · signal PID TERM · model ID · allow file_write · deny file_write · approve ID · reject ID · flush session · exit", id="help")
            yield Input(placeholder="Kernel passphrase (never logged)", password=True, id="command")
            yield Footer()

        async def on_mount(self):
            self.query_one("#tasks", DataTable).add_columns("PID / PPID", "NAME", "STATE")
            await self.refresh_state()
            self.set_interval(2, self.refresh_state)
            self.query_one(Input).focus()

        async def refresh_state(self):
            try:
                state = await call(root, "status")
                mem = state["telemetry"]
                self.query_one("#telemetry", Static).update(f"CARDINAL SUPERVISOR   /   {state['mode'].upper()}\nMODEL  {state['model']}\nLOCAL  {state['local_model']}  |  BUDGET {state['local_memory_gb']} GB  |  RAM {mem['memory_used_gb'] or '—'} / {mem['memory_total_gb'] or '—'} GB\nTOKENS {state['tokens']}  |  RESERVED/USED ${state['spent_usd']:.5f}  |  CLOUD {'ON' if state['cloud_enabled'] else 'OFF'}")
                table = self.query_one("#tasks", DataTable)
                table.clear()
                for process in state.get("processes", []):
                    table.add_row(f"{process['pid']} / {process['ppid']}", process["name"], process["state"])
                for trap in state["pending"]:
                    table.add_row(trap["id"][:8], "TRAP", trap["status"])
            except (OSError, RuntimeError) as exc:
                self.query_one("#telemetry", Static).update(f"DAEMON UNAVAILABLE: {exc}")

        async def on_input_submitted(self, event: Input.Submitted):
            if self.busy:
                return
            self.busy = True
            text = event.value.strip()
            event.input.value = ""
            log = self.query_one("#log", RichLog)
            try:
                if not self.token:
                    result = await call(root, "sys_auth", {"passcode": text})
                    self.token = result["token"]
                    event.input.password = False
                    event.input.placeholder = "cardinal > command or natural-language intent"
                    log.write("AUTH VERIFIED · kernel authority expires in 5 minutes")
                    return
                parts = text.split(maxsplit=1)
                if not parts:
                    return
                command, arg = parts[0], parts[1] if len(parts) > 1 else ""
                method, params = "status", {}
                if command == "exit":
                    await self.action_lock()
                    return
                elif command in ("ps", "tree"):
                    method = "sys_proc_table" if command == "ps" else "sys_proc_tree"
                elif command in ("spawn", "fork"):
                    method, params = "sys_" + command, {"task": arg}
                elif command in ("exec", "wait", "reap", "info"):
                    method = {"exec":"sys_exec", "wait":"sys_wait", "reap":"sys_reap", "info":"sys_proc_info"}[command]
                    params = {"pid": int(arg)}
                elif command == "signal":
                    bits = arg.split()
                    params = {"pid": int(bits[0]), "signal": bits[1] if len(bits) > 1 else "TERM"} if bits else {}
                    method = "sys_signal"
                elif command == "model":
                    method, params = "sys_model_swap", {"target_model_id": arg}
                elif command in ("allow", "deny"):
                    method, params = "sys_perm_toggle", {"capability_id": arg, "enabled": command == "allow"}
                elif command in ("approve", "reject"):
                    pending = await call(root, "sys_trap_table", token=self.token)
                    matches = [t for t in pending if t["id"].startswith(arg)] if arg else []
                    if len(matches) != 1:
                        raise ValueError("Provide one unambiguous trap ID")
                    method, params = "sys_resolve", {"trap_id": matches[0]["id"], "approved": command == "approve"}
                elif command == "flush":
                    method, params = "sys_mem_flush", {"scope": arg or "session"}
                elif command != "status":
                    method, params = "sys_parse", {"text": text}
                result = await call(root, method, params, self.token)
                log.write(json.dumps(result, indent=2))
                if method == "sys_parse":
                    log.write("PROPOSAL ONLY · use an explicit command to execute")
            except (ValueError, RuntimeError, OSError) as exc:
                log.write(f"DENIED / {exc}")
                if "expired" in str(exc):
                    self.token = ""
                    event.input.password = True
                    event.input.placeholder = "Kernel passphrase"
            finally:
                self.busy = False
                await self.refresh_state()

        async def action_lock(self):
            if self.token:
                try:
                    await call(root, "sys_exit", token=self.token)
                except (OSError, RuntimeError):
                    pass
            self.token = ""
            field = self.query_one(Input)
            field.password = True
            field.placeholder = "Kernel passphrase"

        async def action_voice_auth(self):
            log = self.query_one("#log", RichLog)
            if self.busy:
                return
            if voice_model is None:
                log.write("SPOKEN AUTH / Supply --voice-model PATH to an installed offline faster-whisper model. Nothing was recorded.")
                return
            self.busy = True
            try:
                from .audio import capture_passphrase
                log.write("SPOKEN AUTH / Loading offline recognizer, then recording for 5 seconds. Say your passphrase; transcript is never logged.")
                phrase = await asyncio.to_thread(capture_passphrase, str(voice_model))
                result = await call(root, "sys_auth", {"passcode": phrase})
                phrase = ""
                self.token = result["token"]
                field = self.query_one(Input)
                field.password = False
                field.placeholder = "cardinal > command or natural-language intent"
                log.write("AUTH VERIFIED / Offline spoken passphrase. Temporary recording deleted.")
            except Exception as exc:
                # Some STT/audio libraries include input details in exceptions.
                # Do not render arbitrary exception text from this sensitive path.
                log.write(f"SPOKEN AUTH FAILED / {type(exc).__name__}. Check offline model, microphone access, and exact phrase. Typed authentication remains available.")
            finally:
                self.busy = False
                await self.refresh_state()

        async def action_quit(self):
            await self.action_lock()
            self.exit()

    return Cardinal()


def launch(root: Path, voice_model: Path | None = None):
    create_app(root, voice_model).run()


def main():
    launch(Config.load().root)
