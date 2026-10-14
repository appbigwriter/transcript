import subprocess
import unittest
from unittest.mock import patch
from app.pipeline import UserError, command

class DiagnosticTests(unittest.TestCase):
    def failure(self, stderr):
        with patch('app.pipeline.subprocess.run',return_value=subprocess.CompletedProcess(['yt-dlp'],1,'',stderr)):
            with self.assertRaises(UserError) as caught:
                command(['python','-m','yt_dlp'])
        return caught.exception

    def test_cookie_mention_does_not_mask_unavailable_format(self):
        error=self.failure('WARNING: cookies loaded\nERROR: Requested format is not available')
        self.assertEqual(getattr(error,'code',None),'format_unavailable')

    def test_bot_check_is_not_reported_as_ordinary_login(self):
        error=self.failure("ERROR: Sign in to confirm you're not a bot. Use --cookies")
        self.assertEqual(getattr(error,'code',None),'bot_challenge')

    def test_expired_session(self):
        error=self.failure('ERROR: The provided YouTube account cookies are no longer valid')
        self.assertEqual(getattr(error,'code',None),'session_expired')

    def test_raw_details_are_not_exposed(self):
        error=self.failure('ERROR: HTTP Error 403: Forbidden https://example.com/?session=DO_NOT_EXPOSE')
        self.assertEqual(getattr(error,'code',None),'access_denied')
        self.assertNotIn('DO_NOT_EXPOSE',str(error))
