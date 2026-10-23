from __future__ import annotations

import asyncio
import shutil
import tempfile
import wave
import base64
import binascii
import importlib.util
from pathlib import Path


class AudioRouter:
    """One output owner. Generation changes invalidate queued userspace audio."""

    def __init__(self):
        self.mode = "userspace"
        self.generation = 0
        self.process = None

    async def transition(self, mode):
        self.generation += 1
        self.mode = mode
        if self.process and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        self.process = None

    async def speak(self, text, mode, generation):
        if mode != self.mode or generation != self.generation:
            return False
        # macOS's installed voice is a free local fallback. No cloud synthesis.
        if not shutil.which("say"):
            return False
        self.process = await asyncio.create_subprocess_exec("say", "-r", "185" if mode == "kernel" else "165", "--", text[:4000])
        await self.process.wait()
        return True


class LocalTranscriber:
    """Optional local-only adapter. Model installation is always an explicit step."""

    def __init__(self, model_path: str):
        path = Path(model_path).expanduser().resolve(strict=True)
        if not all((path / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
            raise ValueError("Offline speech model requires model.bin, config.json, and tokenizer.json")
        from faster_whisper import WhisperModel
        self.model = WhisperModel(str(path), device="cpu", compute_type="int8", local_files_only=True)

    def transcribe(self, audio_path: str, max_seconds: float | None = None):
        segments, info = self.model.transcribe(audio_path, beam_size=1)
        if max_seconds is not None and info.duration > max_seconds + 0.5:
            raise ValueError("Voice clip exceeds the 15-second limit")
        return " ".join(segment.text.strip() for segment in segments)


class VoiceInput:
    """Bounded browser audio -> local STT. No uploads to a speech provider."""

    MAX_BYTES = 768_000
    MAX_SECONDS = 15
    FORMATS = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a", "audio/wav": ".wav"}

    def __init__(self, config):
        self.config = config
        self.lock = asyncio.Lock()
        self.transcriber = None

    def status(self):
        model = self.config.stt_model_path
        available = bool(model and all((Path(model).expanduser() / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")))
        installed = importlib.util.find_spec("faster_whisper") is not None
        return {
            "input_ready": available and installed,
            "reason": "Ready" if available and installed else "Install the voice extra and set stt_model_path to an installed offline model directory.",
            "max_seconds": self.MAX_SECONDS,
            "config_path": str(self.config.root / "config.json"),
            "output": "Installed local browser voice",
            "spoken_auth": "Explicit browser capture or native TUI F2; offline only",
            "wake_phrase": "hey jarvis",
            "wake_engine": "Local offline speech recognition",
        }

    def decode(self, encoded, content_type):
        mime = str(content_type).split(";")[0].strip().lower()
        if mime not in self.FORMATS:
            raise ValueError("Unsupported audio format")
        if not isinstance(encoded, str) or not encoded or len(encoded) > 1_024_000:
            raise ValueError("Voice clip is empty or too large")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid audio encoding") from None
        if not 16 <= len(raw) <= self.MAX_BYTES:
            raise ValueError("Voice clip is empty or too large")
        valid = {"audio/webm": raw.startswith(b"\x1aE\xdf\xa3"), "audio/ogg": raw.startswith(b"OggS"),
                 "audio/wav": raw.startswith(b"RIFF") and raw[8:12] == b"WAVE", "audio/mp4": raw[4:8] == b"ftyp"}
        if not valid[mime]:
            raise ValueError("Audio data does not match its format")
        return raw, mime

    async def transcribe(self, encoded, content_type):
        raw, mime = self.decode(encoded, content_type)
        if not self.status()["input_ready"]:
            raise RuntimeError(self.status()["reason"])
        async with self.lock:
            return await asyncio.to_thread(self._transcribe, raw, mime)

    def _transcribe(self, raw, mime):
        try:
            if self.transcriber is None:
                path = Path(self.config.stt_model_path).expanduser().resolve(strict=True)
                self.transcriber = LocalTranscriber(str(path))
            with tempfile.TemporaryDirectory(prefix="agent-zero-turn-") as temporary:
                audio = Path(temporary) / ("voice" + self.FORMATS[mime])
                audio.write_bytes(raw)
                audio.chmod(0o600)
                text = self.transcriber.transcribe(str(audio), max_seconds=self.MAX_SECONDS)
                if not text.strip():
                    raise ValueError("No speech detected")
                return text.strip()
        except ValueError:
            raise
        except Exception:
            # Decoder/model errors may include input details or local paths.
            raise RuntimeError("Offline speech recognition failed; check the installed model and audio format") from None


def capture_passphrase(model_path: str, seconds: int = 5):
    """Explicit microphone action only; transient recordings never enter agent state."""
    if not 1 <= seconds <= 10:
        raise ValueError("Recording duration must be 1..10 seconds")
    path = Path(model_path).expanduser().resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Supply a locally installed faster-whisper model directory")
    try:
        import sounddevice as sound
    except ImportError:
        raise RuntimeError("Install the voice extra before using spoken authentication") from None
    # Load before opening the microphone. local_files_only prevents downloads.
    transcriber = LocalTranscriber(str(path))
    with tempfile.TemporaryDirectory(prefix="agent-zero-voice-") as temporary:
        recording = sound.rec(int(seconds * 16000), samplerate=16000, channels=1, dtype="int16")
        try:
            sound.wait()
        finally:
            sound.stop()
        audio = Path(temporary) / "passphrase.wav"
        with wave.open(str(audio), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(recording.tobytes())
        # ASR commonly adds a sentence period; do not otherwise normalize secrets.
        return transcriber.transcribe(str(audio)).strip().removesuffix(".")
