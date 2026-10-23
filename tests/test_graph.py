import importlib.util
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.runtime import Runtime


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "optional agent extra is not installed")
class GraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_graph_conversation_can_be_trapped_while_waiting(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = Runtime(Config(root=root, cloud_enabled=True))
            started = asyncio.Event()
            async def slow_provider(*args):
                started.set()
                await asyncio.Event().wait()
            runtime.cloud.chat = AsyncMock(side_effect=slow_provider)
            try:
                async with AsyncSqliteSaver.from_conn_string(str(root / "graph.sqlite")) as saver:
                    runtime.graph = TrapGraph(runtime, saver)
                    task = asyncio.create_task(runtime.dispatch("chat", {"text":"slow graph request"}))
                    await asyncio.wait_for(started.wait(), 2)
                    await asyncio.wait_for(runtime.dispatch("sys_call", {"request":"preempt"}), 2)
                    with self.assertRaises(PermissionError):
                        await task
                    state = await runtime.graph.graph.aget_state({"configurable":{"thread_id":"main"}})
                    self.assertEqual(state.next, ("privilege_gate",))
                    self.assertEqual(len(runtime.store.pending()), 1)
            finally:
                runtime.store.db.close()

    async def test_conversation_node_and_checkpoint_flush(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = Runtime(Config(root=root, cloud_enabled=True))
            runtime.cloud.chat = AsyncMock(return_value="Stubbed graph response; no provider called")
            runtime.auth.setup("a long test passphrase")
            try:
                async with AsyncSqliteSaver.from_conn_string(str(root / "graph.sqlite")) as saver:
                    runtime.graph = TrapGraph(runtime, saver)
                    result = await runtime.dispatch("chat", {"text":"hello graph"})
                    self.assertEqual(result["message"], "Stubbed graph response; no provider called")
                    self.assertEqual(runtime.cloud.chat.await_count, 1)
                    token = (await runtime.dispatch("sys_auth", {"passcode":"a long test passphrase"}))["token"]
                    await runtime.dispatch("sys_mem_flush", {"scope":"all"}, token)
                    self.assertEqual(runtime.store.history("main"), [])
                    self.assertEqual([item async for item in saver.alist(None)], [])
            finally:
                runtime.store.db.close()

    async def test_interrupt_survives_rebuild_and_resumes_authenticated(self):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from agent_zero.graph import TrapGraph
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = Runtime(Config(root=root))
            runtime.auth.setup("a long test passphrase")
            try:
                async with AsyncSqliteSaver.from_conn_string(str(root / "graph.sqlite")) as saver:
                    runtime.graph = TrapGraph(runtime, saver)
                    result = await runtime.dispatch("sys_call", {"request": "swap model"})
                    state = await runtime.graph.graph.aget_state({"configurable":{"thread_id":"main"}})
                    self.assertEqual(state.next, ("privilege_gate",))
                async with AsyncSqliteSaver.from_conn_string(str(root / "graph.sqlite")) as saver:
                    runtime.graph = TrapGraph(runtime, saver)
                    token = (await runtime.dispatch("sys_auth", {"passcode":"a long test passphrase"}))["token"]
                    await runtime.dispatch("sys_resolve", {"trap_id":result["trap_id"],"approved":False}, token)
                    state = await runtime.graph.graph.aget_state({"configurable":{"thread_id":"main"}})
                    self.assertEqual(state.next, ())
                    self.assertFalse(state.values["approved"])
            finally:
                runtime.store.db.close()
