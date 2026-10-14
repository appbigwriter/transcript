import os
import unittest
from pathlib import Path
from unittest.mock import patch
from app.core import Settings
from app.pipeline import UserError, extract_source
from app.youtube import diagnose_error
from urllib.error import HTTPError

class FallbackTests(unittest.TestCase):
    def test_http_diagnostic_is_precise_and_redacted(self):
        error=diagnose_error(HTTPError('https://example.com/?token=secret',403,'secret-details',None,None),'download')
        self.assertEqual(error.code,'youtube_http_403_download')
        self.assertNotIn('secret',str(error))
        self.assertIn('HTTP 403',str(error))

    def test_runtime_diagnostic_does_not_expose_paths(self):
        error=diagnose_error(PermissionError('/private/secret-path'),'streams')
        self.assertEqual(error.code,'youtube_runtime_error')
        self.assertNotIn('secret-path',str(error))

    def setUp(self):
        self.settings=Settings.load()

    def test_bot_failure_uses_alternative_once(self):
        with patch.dict(os.environ,{'FBR_YOUTUBE_ENGINE':'auto'}), patch('app.pipeline.extract_ytdlp_source',side_effect=UserError('challenge','bot_challenge')), patch('app.youtube.download_audio',return_value=(Path('audio'),{'extractor':'pytubefix'})) as alternative:
            source,metadata=extract_source('https://youtu.be/ZTSI3DDP_4A','YouTube',self.settings,Path('.'))
            self.assertEqual(metadata['extractor'],'pytubefix')
            alternative.assert_called_once()

    def test_private_or_rate_limited_content_not_retried(self):
        for code in ['private_content','age_restricted','rate_limited']:
            with self.subTest(code=code), patch.dict(os.environ,{'FBR_YOUTUBE_ENGINE':'auto'}), patch('app.pipeline.extract_ytdlp_source',side_effect=UserError('restricted',code)), patch('app.youtube.download_audio') as alternative:
                with self.assertRaises(UserError):
                    extract_source('https://youtu.be/ZTSI3DDP_4A','YouTube',self.settings,Path('.'))
                alternative.assert_not_called()

    def test_other_platform_keeps_its_extractor(self):
        with patch.dict(os.environ,{'FBR_YOUTUBE_ENGINE':'pytubefix'}), patch('app.pipeline.extract_ytdlp_source',return_value=(Path('audio'),{})) as primary, patch('app.youtube.download_audio') as alternative:
            extract_source('https://instagram.com/reel/example','Instagram',self.settings,Path('.'))
            primary.assert_called_once()
            alternative.assert_not_called()

    def test_explicit_engine_avoids_primary(self):
        with patch.dict(os.environ,{'FBR_YOUTUBE_ENGINE':'pytubefix'}), patch('app.pipeline.extract_ytdlp_source') as primary, patch('app.youtube.download_audio',return_value=(Path('audio'),{})) as alternative:
            extract_source('https://youtu.be/ZTSI3DDP_4A','YouTube',self.settings,Path('.'))
            primary.assert_not_called()
            alternative.assert_called_once()
