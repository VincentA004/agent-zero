import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from agent_zero.config import Config
from agent_zero.desktop import launch


class DesktopTests(unittest.TestCase):
    def test_electron_launch_keeps_runtime_private_without_node_override(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(root=Path(directory))
            binary = Path('/fixture/Electron.app/Contents/MacOS/Electron')
            with patch('agent_zero.desktop.electron_binary', return_value=binary), \
                 patch('agent_zero.desktop.call', new=AsyncMock(return_value={'mode': 'userspace'})), \
                 patch.dict(os.environ, {'ELECTRON_RUN_AS_NODE': '1'}), \
                 patch('agent_zero.desktop.subprocess.Popen') as popen:
                launch(config)
            self.assertEqual(popen.call_args.args[0][0], str(binary))
            environment = popen.call_args.kwargs['env']
            self.assertEqual(environment['AGENT_ZERO_HOME'], str(config.root))
            self.assertNotIn('ELECTRON_RUN_AS_NODE', environment)
            self.assertEqual(os.stat(config.root / 'desktop.log').st_mode & 0o777, 0o600)
            self.assertIs(popen.call_args.kwargs['stdout'], popen.call_args.kwargs['stderr'])

    def test_missing_electron_does_not_start_daemon(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch('agent_zero.desktop.electron_binary', side_effect=RuntimeError('npm ci')), \
             patch('agent_zero.desktop.subprocess.Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'npm ci'):
                launch(Config(root=Path(directory)))
            popen.assert_not_called()
