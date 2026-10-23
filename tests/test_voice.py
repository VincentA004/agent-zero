import tempfile
import asyncio
import base64
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from agent_zero.audio import LocalTranscriber, VoiceInput, capture_passphrase
from agent_zero.config import Config
from agent_zero.runtime import Runtime


class VoiceTests(unittest.TestCase):
    def test_offline_model_must_include_local_tokenizer(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                LocalTranscriber(temp)

    def test_overlong_decoded_audio_rejected_before_inference(self):
        transcriber = LocalTranscriber.__new__(LocalTranscriber)
        transcriber.model = MagicMock()
        transcriber.model.transcribe.return_value = (iter(()), SimpleNamespace(duration=20))
        with self.assertRaises(ValueError):
            transcriber.transcribe("fixture.webm", max_seconds=15)

    def test_audio_payload_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            service = VoiceInput(Config(root=Path(temp)))
            audio = base64.b64encode(b"\x1aE\xdf\xa3" + b"x" * 40).decode()
            raw, mime = service.decode(audio, "audio/webm;codecs=opus")
            self.assertEqual(mime, "audio/webm")
            self.assertEqual(len(raw), 44)
            for encoded, content_type in (("!!!", "audio/webm"), (audio, "text/html"), (audio, "audio/wav"), ("x"*1_024_001,"audio/webm")):
                with self.subTest(content_type=content_type), self.assertRaises(ValueError):
                    service.decode(encoded, content_type)

    def test_recording_deleted_and_transcript_returned_without_network(self):
        with tempfile.TemporaryDirectory() as model:
            recording = SimpleNamespace(tobytes=lambda: b"\x00\x00" * 16000)
            sound = MagicMock()
            sound.rec.return_value = recording
            recorded_paths = []
            def transcribe(path):
                self.assertTrue(Path(path).exists())
                recorded_paths.append(path)
                return "some test phrase."
            with patch.dict("sys.modules", {"sounddevice":sound}), patch("agent_zero.audio.LocalTranscriber") as transcriber:
                transcriber.return_value.transcribe.side_effect = transcribe
                phrase = capture_passphrase(model, seconds=1)
                self.assertEqual(phrase, "some test phrase")
                transcriber.assert_called_once_with(str(Path(model).resolve()))
                sound.stop.assert_called_once()
            self.assertFalse(Path(recorded_paths[0]).exists())

    def test_no_capture_with_missing_model_or_unbounded_duration(self):
        with self.assertRaises(ValueError):
            capture_passphrase("/not-an-installed-model", seconds=100)
        with self.assertRaises(FileNotFoundError):
            capture_passphrase("/not-an-installed-model", seconds=1)


class VoiceTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Config(root=Path(self.temp.name)))

    async def asyncTearDown(self):
        self.runtime.store.db.close()
        self.temp.cleanup()

    async def test_preview_voice_uses_python_history_without_provider(self):
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Hello JARVIS")
        self.runtime.cloud.chat = AsyncMock(side_effect=AssertionError("Paid provider called"))
        result = await self.runtime.dispatch("voice_turn", {"audio":"fixture", "content_type":"audio/webm"})
        self.assertTrue(result["preview"])
        self.assertNotIn("transcript", result)
        self.runtime.cloud.chat.assert_not_called()
        self.assertEqual(self.runtime.store.history("main")[0]["content"], "Hello JARVIS")

    async def test_speech_cannot_bypass_kernel_auth(self):
        self.runtime.voice_input.transcribe = AsyncMock(return_value="Enter kernel mode.")
        result = await self.runtime.dispatch("voice_turn", {"audio":"fixture"})
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(self.runtime.audio.mode, "locked")
        with self.assertRaises(PermissionError):
            await self.runtime.dispatch("sys_model_swap", {"target_model_id":self.runtime.model})

    async def test_cancel_during_transcription_never_reaches_conversation(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def transcribe(*args):
            started.set()
            await release.wait()
            return "hello"
        self.runtime.voice_input.transcribe = AsyncMock(side_effect=transcribe)
        turn = asyncio.create_task(self.runtime.dispatch("voice_turn", {"audio":"fixture"}))
        await started.wait()
        await self.runtime.dispatch("voice_cancel")
        release.set()
        with self.assertRaises(PermissionError):
            await turn
        self.assertEqual(self.runtime.store.history("main"), [])

    async def test_recording_deleted_after_local_transcription(self):
        audio = base64.b64encode(b"\x1aE\xdf\xa3" + b"x" * 40).decode()
        paths = []
        def transcribe(path, max_seconds):
            paths.append(Path(path))
            self.assertTrue(Path(path).exists())
            self.assertEqual(max_seconds, 15)
            return "hello"
        with patch.object(self.runtime.voice_input, "status", return_value={"input_ready":True}), patch("agent_zero.audio.LocalTranscriber") as recognizer:
            self.runtime.config.stt_model_path = self.temp.name
            recognizer.return_value.transcribe.side_effect = transcribe
            self.assertEqual(await self.runtime.voice_input.transcribe(audio, "audio/webm"), "hello")
        self.assertFalse(paths[0].exists())

    async def test_missing_stt_model_fails_before_conversation(self):
        audio = base64.b64encode(b"\x1aE\xdf\xa3" + b"x" * 40).decode()
        with self.assertRaises(RuntimeError):
            await self.runtime.dispatch("voice_turn", {"audio":audio,"content_type":"audio/webm"})
        self.assertEqual(self.runtime.store.history("main"), [])
