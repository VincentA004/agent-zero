import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent_zero.config import Config
from agent_zero.runtime import Runtime


@unittest.skipUnless(importlib.util.find_spec("textual"), "optional TUI extra is not installed")
class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_console_authentication_command_and_lock(self):
        from agent_zero.tui import create_app
        from textual.widgets import Input
        with tempfile.TemporaryDirectory() as temp:
            runtime = Runtime(Config(root=Path(temp)))
            async def fake_call(root, method, params=None, token=""):
                if method == "sys_auth":
                    return {"token":"test-token", "expires_in":300}
                if method == "status":
                    return runtime.snapshot()
                return {"mode":"userspace"}
            client = AsyncMock(side_effect=fake_call)
            try:
                with patch("agent_zero.tui.call", client):
                    app = create_app(Path(temp))
                    async with app.run_test(size=(120,40)) as pilot:
                        field = app.query_one(Input)
                        self.assertTrue(field.password)
                        field.value = "test-only-passphrase"
                        await pilot.press("enter")
                        self.assertEqual(app.token, "test-token")
                        self.assertFalse(field.password)
                        field.value = "model z-ai/glm-5.3-flash"
                        await pilot.press("enter")
                        client.assert_any_await(Path(temp), "sys_model_swap", {"target_model_id":"z-ai/glm-5.3-flash"}, "test-token")
                        field.value = "mem grant team 1 r"
                        await pilot.press("enter")
                        client.assert_any_await(Path(temp), "sys_mem_command", {"text":"mem grant team 1 r"}, "test-token")
                        field.value = "schedule list"
                        await pilot.press("enter")
                        client.assert_any_await(Path(temp), "sys_control_command", {"text":"schedule list"}, "test-token")
                        await pilot.press("ctrl+l")
                        self.assertEqual(app.token, "")
                        self.assertTrue(field.password)
            finally:
                runtime.store.db.close()
