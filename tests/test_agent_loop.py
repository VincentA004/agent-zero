import asyncio
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from agent_zero.config import Config
from agent_zero.runtime import Runtime


def tool(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Config(root=Path(self.temp.name), local_enabled=True))

    async def asyncTearDown(self):
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def test_local_conversation_never_calls_cloud(self):
        self.runtime.local.turn = AsyncMock(return_value={"role": "assistant", "content": "Local answer"})
        self.runtime.cloud.chat = AsyncMock(side_effect=AssertionError("cloud called"))
        result = await self.runtime.chat({"text": "Hello"})
        self.assertEqual(result["message"], "Local answer")
        self.assertFalse(result["preview"])
        self.runtime.cloud.chat.assert_not_called()

    async def test_read_then_final_and_denied_write(self):
        (self.runtime.sandbox.root / "note.txt").write_text("test content")
        self.runtime.local.turn = AsyncMock(side_effect=[tool("read_file", path="note.txt"), tool("write_file", path="bad.txt", content="denied"), {"content": "I read the note; writing is disabled."}])
        result = await self.runtime.chat({"text": "Read note.txt and save a copy"})
        self.assertIn("writing is disabled", result["message"])
        self.assertFalse((self.runtime.sandbox.root / "bad.txt").exists())
        messages = self.runtime.local.turn.call_args.args[0]
        self.assertTrue(any(m.get("role") == "tool" and "test content" in m["content"] for m in messages))
        self.assertTrue(any(m.get("role") == "tool" and "disabled" in m["content"] for m in messages))

    async def test_step_limit(self):
        self.runtime.config.max_agent_steps = 2
        self.runtime.local.turn = AsyncMock(return_value=tool("list_files", path="."))
        result = await self.runtime.chat({"text": "loop"})
        self.assertIn("step limit", result["message"])
        self.assertEqual(self.runtime.local.turn.await_count, 2)

    async def test_model_escalation_pauses_without_execution(self):
        self.runtime.local.turn = AsyncMock(return_value=tool("request_kernel", reason="Write capability needed"))
        result = await self.runtime.chat({"text": "Need authority"})
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.runtime.audio.mode, "locked")
        self.assertFalse(self.runtime.permissions["file_write"])

    async def test_local_inference_preempted(self):
        started = asyncio.Event()
        async def slow(*args):
            started.set()
            await asyncio.Event().wait()
        self.runtime.local.turn = AsyncMock(side_effect=slow)
        task = asyncio.create_task(self.runtime.chat({"text": "slow"}))
        await started.wait()
        await self.runtime.dispatch("sys_call", {"request": "pause"})
        with self.assertRaises(PermissionError):
            await task

    async def test_langgraph_executes_tool_loop(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        async with AsyncSqliteSaver.from_conn_string(str(Path(self.temp.name) / "graph.sqlite")) as saver:
            self.runtime.graph = TrapGraph(self.runtime, saver)
            self.runtime.local.turn = AsyncMock(side_effect=[tool("list_files", path="."), {"content": "The workspace is empty."}])
            self.assertEqual((await self.runtime.chat({"text": "List files"}))["message"], "The workspace is empty.")
            state = await self.runtime.graph.graph.aget_state({"configurable": {"thread_id": "main"}})
            self.assertEqual(state.values["steps"], 2)
            self.assertEqual(state.next, ())

    async def test_langgraph_model_escalation(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        async with AsyncSqliteSaver.from_conn_string(str(Path(self.temp.name) / "graph.sqlite")) as saver:
            self.runtime.graph = TrapGraph(self.runtime, saver)
            self.runtime.local.turn = AsyncMock(return_value=tool("request_kernel", reason="Need operator"))
            result = await self.runtime.chat({"text": "escalate"})
            self.assertEqual(result["status"], "interrupted")
            state = await self.runtime.graph.graph.aget_state({"configurable": {"thread_id": "main"}})
            self.assertEqual(state.next, ("privilege_gate",))


@unittest.skipUnless(sys.platform == "darwin", "macOS sandbox policy")
class IsolationTests(unittest.TestCase):
    def test_worker_cannot_read_credentials_or_connect_to_network(self):
        from agent_zero.sandbox import Sandbox
        from agent_zero.worker import FileWorker
        real = subprocess.run
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sandbox = root / "sandbox"
            sandbox.mkdir()
            secret = root / "credentials.json"
            secret.write_text("not readable")
            worker = FileWorker(Sandbox(sandbox))
            def probe(command, **kwargs):
                code = f'''import json,socket
blocked=[]
try: open({str(secret)!r}).read()
except PermissionError: blocked.append("file")
try: socket.create_connection(("127.0.0.1",11434),timeout=1)
except PermissionError: blocked.append("network")
print(json.dumps({{"result":blocked}}))'''
                return real([*command[:-1], "-c", code], **kwargs)
            with patch("agent_zero.worker.subprocess.run", probe):
                self.assertEqual(worker.call("list", "."), ["file", "network"])

class SpokenAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_spoken_credentials_do_not_enter_history_or_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Config(root=Path(temp)))
            phrase = "a long spoken passphrase"
            runtime.auth.setup(phrase)
            runtime.voice_input.transcribe = AsyncMock(return_value=phrase)
            try:
                result = await runtime.dispatch("voice_auth", {"audio": "fixture", "content_type": "audio/wav"})
                runtime.auth.require(result["token"])
                self.assertEqual(runtime.audio.mode, "kernel")
                self.assertEqual(runtime.store.history("main"), [])
                self.assertNotIn(phrase, json.dumps(runtime.store.events()))
            finally:
                runtime.store.db.close()

    async def test_cancelled_spoken_auth_does_not_grant_token(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Config(root=Path(temp)))
            runtime.auth.setup("a long spoken passphrase")
            started, finish = asyncio.Event(), asyncio.Event()
            async def transcribe(*args):
                started.set()
                await finish.wait()
                return "a long spoken passphrase"
            runtime.voice_input.transcribe = transcribe
            try:
                task = asyncio.create_task(runtime.dispatch("voice_auth", {"audio": "fixture", "content_type": "audio/wav"}))
                await started.wait()
                await runtime.dispatch("voice_cancel")
                finish.set()
                with self.assertRaises(PermissionError):
                    await task
                self.assertEqual(runtime.auth.tokens, {})
            finally:
                runtime.store.db.close()

class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_cancellation_closes_provider_connection(self):
        from agent_zero.local_http import request_local_json
        started, closed = asyncio.Event(), asyncio.Event()
        async def client(reader, writer):
            try:
                await reader.readuntil(b'\r\n\r\n')
                started.set()
                while await reader.read(4096):
                    pass
                closed.set()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(client, '127.0.0.1', 0)
        try:
            task = asyncio.create_task(request_local_json(f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/api/chat', {}))
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(closed.wait(), 2)
        finally:
            server.close()
            await server.wait_closed()

    async def test_chunked_local_response(self):
        from agent_zero.local_http import request_local_json
        async def client(reader, writer):
            await reader.readuntil(b'\r\n\r\n')
            writer.write(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n8\r\n{"ok":1}\r\n0\r\n\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(client, '127.0.0.1', 0)
        try:
            result = await request_local_json(f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/api/chat', {})
            self.assertEqual(result, {'ok': 1})
        finally:
            server.close()
            await server.wait_closed()

class CloudToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_cloud_tool_loop_retains_tool_call_id(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Config(root=Path(temp), cloud_enabled=True))
            message = tool('list_files', path='.')
            message['tool_calls'][0]['id'] = 'call-fixture'
            runtime.cloud.chat = AsyncMock(side_effect=[message, {'role':'assistant','content':'Empty workspace'}])
            try:
                result = await runtime.chat({'text':'List files'})
                self.assertEqual(result['message'], 'Empty workspace')
                messages = runtime.cloud.chat.call_args.args[1]
                self.assertEqual(next(m for m in messages if m['role']=='tool')['tool_call_id'], 'call-fixture')
                tool_names = {item['function']['name'] for item in runtime.cloud.chat.call_args.args[3]}
                self.assertIn('read_file', tool_names)
                self.assertIn('sys_spawn', tool_names)
                self.assertNotIn('sys_model_swap', tool_names)
            finally:
                runtime.store.db.close()

    async def test_invalid_tool_name_is_denied_without_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Config(root=Path(temp), local_enabled=True))
            runtime.local.turn = AsyncMock(side_effect=[tool(['invalid'], path='.'), {'content':'Could not use that tool'}])
            try:
                result = await runtime.chat({'text':'Invalid tool fixture'})
                self.assertEqual(result['message'],'Could not use that tool')
                self.assertTrue(any(item['event']=='tool.denied' for item in runtime.store.events()))
            finally:
                runtime.store.db.close()
