import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from agent_zero.config import Config
from agent_zero.runtime import Runtime


class KernelEntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.runtime = Runtime(Config(root=Path(self.directory.name)))
        self.runtime.cloud.chat = AsyncMock(side_effect=AssertionError('No cloud call during entry'))

    async def asyncTearDown(self):
        await self.runtime.processes.interrupt_all()
        self.runtime.store.db.close()
        self.directory.cleanup()

    async def test_first_use_creates_local_passphrase_and_cannot_replace_it(self):
        phrase = 'fixture passphrase only'
        result = await self.runtime.dispatch('sys_auth', {'create': True, 'passcode': phrase, 'confirmation': phrase})
        self.runtime.auth.require(result['token'])
        self.assertEqual(self.runtime.audio.mode, 'kernel')
        self.assertNotIn(phrase, str(self.runtime.store.events()))
        self.assertEqual(self.runtime.store.history('main'), [])
        with self.assertRaises(PermissionError):
            await self.runtime.dispatch('sys_auth', {'create': True, 'passcode': 'replacement phrase', 'confirmation': 'replacement phrase'})
        self.runtime.auth.authenticate(phrase)

    async def test_bad_setup_does_not_create_credentials(self):
        for params in ({'passcode': 'short', 'confirmation': 'short'}, {'passcode': 'long fixture phrase', 'confirmation': 'different'}):
            with self.assertRaises(ValueError):
                await self.runtime.dispatch('sys_auth', {'create': True, **params})
            self.assertFalse(self.runtime.auth.configured)

    async def test_voice_mode_request_locks_without_granting_authority(self):
        self.runtime.voice_input.transcribe = AsyncMock(return_value='Jarvis, can you enter kernel mode?')
        result = await self.runtime.dispatch('voice_turn', {'audio': 'fixture'})
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(self.runtime.audio.mode, 'locked')
        self.assertEqual(self.runtime.auth.tokens, {})
        self.runtime.cloud.chat.assert_not_called()
