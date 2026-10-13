import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import Settings

class EnvironmentTests(unittest.TestCase):
    def test_local_file_key_and_runtime_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env.local').write_text('GROQ_API_KEY=local-test-key\nFBR_MODEL=\n', encoding='utf-8')
            (root / '.env').write_text('GROQ_API_KEY=base-test-key\n', encoding='utf-8')
            with patch('app.core.ENV_ROOT', root, create=True), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(Settings.load().key, 'local-test-key')
            with patch('app.core.ENV_ROOT', root, create=True), patch.dict(os.environ, {'GROQ_API_KEY': 'runtime-test-key'}, clear=True):
                self.assertEqual(Settings.load().key, 'runtime-test-key')

    def test_base_file_when_local_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env').write_text('GROQ_API_KEY=base-test-key\n', encoding='utf-8')
            with patch('app.core.ENV_ROOT', root, create=True), patch.dict(os.environ, {}, clear=True):
                self.assertEqual(Settings.load().key, 'base-test-key')
