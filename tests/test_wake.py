import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent_zero.config import Config
from agent_zero.runtime import Runtime
from agent_zero.wake import detect_wake, sleep_action


class PhraseTests(unittest.TestCase):
    def test_phrase_and_same_breath_command(self):
        for text, command in (("hey jarvis", ""), ("Hey, Jarvis!", ""), ("HEY JARVIS, what's next?", "what's next?"), (" Hey Jarvis — enter kernel mode. ", "enter kernel mode. ".strip())):
            with self.subTest(text=text):
                self.assertEqual(detect_wake(text), {"awake":True,"command":command})

    def test_ordinary_speech_does_not_wake(self):
        for text in ("", "hello jarvis", "hey siri", "they jarvis", "hey jarvison", "someone said hey jarvis", "say hey jarvis to wake it"):
            with self.subTest(text=text):
                self.assertEqual(detect_wake(text), {"awake":False,"command":""})

    def test_sleep_and_stop_are_explicit(self):
        self.assertEqual(sleep_action("Go to sleep."), "sleep")
        self.assertEqual(sleep_action("Stop listening!"), "stop")
        self.assertIsNone(sleep_action("Stop listening to that song and play another"))


class WakeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Config(root=Path(self.temp.name)))
        self.runtime.cloud.chat = AsyncMock(side_effect=AssertionError("Wake path called cloud inference"))

    async def asyncTearDown(self):
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def test_ambient_speech_never_enters_history_or_cloud(self):
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Private background conversation")
        result = await self.runtime.dispatch("wake_probe", {"audio":"fixture"})
        self.assertFalse(result["awake"])
        self.assertEqual(self.runtime.store.history("main"), [])
        self.assertNotIn("Private background", str(self.runtime.store.events()))
        self.runtime.cloud.chat.assert_not_called()

    async def test_detected_phrase_returns_command_without_executing_it(self):
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Hey Jarvis, tell me a story")
        self.assertEqual(await self.runtime.dispatch("wake_probe", {"audio":"fixture"}), {"awake":True,"command":"tell me a story"})
        self.assertEqual(self.runtime.store.history("main"), [])
        self.runtime.cloud.chat.assert_not_called()

    async def test_cancellation_invalidates_inflight_wake_detection(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def transcribe(*args):
            started.set()
            await release.wait()
            return "Hey Jarvis, do something"
        self.runtime.voice_input.transcribe = AsyncMock(side_effect=transcribe)
        probe = asyncio.create_task(self.runtime.dispatch("wake_probe", {"audio":"fixture"}))
        await started.wait()
        await self.runtime.dispatch("voice_cancel")
        release.set()
        with self.assertRaises(PermissionError):
            await probe
        self.runtime.cloud.chat.assert_not_called()

    async def test_kernel_mode_blocks_wake_recognition(self):
        self.runtime.auth.setup("a long test passphrase")
        await self.runtime.dispatch("sys_auth", {"passcode":"a long test passphrase"})
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Hey Jarvis")
        with self.assertRaises(PermissionError):
            await self.runtime.dispatch("wake_probe", {"audio":"fixture"})
        self.runtime.voice_input.transcribe.assert_not_called()

    async def test_no_speech_is_a_nonmatch(self):
        self.runtime.voice_input.transcribe = AsyncMock(side_effect=ValueError("No speech detected"))
        self.assertFalse((await self.runtime.dispatch("wake_probe", {"audio":"fixture"}))["awake"])

    async def test_sleep_commands_do_not_call_model_or_enter_history(self):
        self.runtime.config.cloud_enabled = True
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Go to sleep.")
        result = await self.runtime.dispatch("voice_turn", {"audio":"fixture"})
        self.assertTrue(result["sleep"])
        self.assertFalse(result["disable_wake"])
        result = await self.runtime.dispatch("chat", {"text":"Stop listening."})
        self.assertTrue(result["disable_wake"])
        self.assertEqual(self.runtime.store.history("main"), [])
        self.runtime.cloud.chat.assert_not_called()

    async def test_retired_registry_selection_migrates(self):
        self.runtime.store.set_setting("model", "minimax/minimax-m3.1-flash")
        replacement = Runtime(Config(root=Path(self.temp.name)))
        try:
            self.assertEqual(replacement.model, "z-ai/glm-5.3-flash")
            self.assertEqual(replacement.store.setting("model"), replacement.model)
            self.assertFalse(replacement.config.cloud_enabled)
        finally:
            replacement.store.db.close()

    async def test_free_route_has_zero_price_ceiling_and_no_paid_fallback(self):
        self.runtime.config.cloud_enabled = True
        from agent_zero.engines import OpenRouter
        gateway = OpenRouter(self.runtime.config)
        model = next(model for model in self.runtime.registry["cloud"] if model["free"])
        response = {"choices":[{"message":{"content":"free fixture"}}],"usage":{"total_tokens":1,"cost":0}}
        with patch.dict("os.environ", {"OPENROUTER_API_KEY":"test-only"}), patch("agent_zero.engines.request_json", return_value=response) as provider:
            self.assertEqual(await gateway.chat(model["id"], [{"role":"user","content":"hi"}], model), "free fixture")
        payload = provider.call_args.args[1]
        self.assertEqual(payload["provider"]["max_price"], {"prompt":0,"completion":0})
        self.assertFalse(payload["provider"]["allow_fallbacks"])
        self.assertTrue(payload["model"].endswith(":free"))
