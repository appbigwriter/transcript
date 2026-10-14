import os
import tempfile
import time
import sqlite3
from contextlib import closing
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.core import Settings
from app.main import app
from app.pipeline import downloader_arguments
from app.sessions import SessionStore, filtered_cookies

def cookies(domain='.youtube.com', value='fake-cookie-for-test', expiry=None):
    expiry = int(time.time() + 86400) if expiry is None else expiry
    return f'# Netscape HTTP Cookie File\n{domain}\tTRUE\t/\tTRUE\t{expiry}\tSID\t{value}\n'

class SessionTests(unittest.TestCase):
    def test_real_firefox_profile_sync(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_SESSION_SECRET':'test-encryption-secret'}):
            root=Path(directory)
            profile=root/'firefox'
            profile.mkdir()
            with closing(sqlite3.connect(profile/'cookies.sqlite')) as connection:
                connection.execute('PRAGMA user_version=16')
                connection.execute('CREATE TABLE moz_cookies (host TEXT,name TEXT,value TEXT,path TEXT,expiry INTEGER,isSecure INTEGER)')
                connection.execute('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?)',('.youtube.com','SID','fixture-youtube-cookie','/',int((time.time()+86400)*1000),1))
                connection.execute('INSERT INTO moz_cookies VALUES (?,?,?,?,?,?)',('.instagram.com','sessionid','fixture-instagram-cookie','/',int((time.time()+86400)*1000),1))
                connection.commit()
            settings=replace(Settings.load(),data_dir=root/'data',password='private-test-password',allow_public=False)
            with patch.dict(os.environ,{'FBR_BROWSER_PROFILE':str(profile)}):
                store=SessionStore(settings)
                self.assertTrue(store.sync_firefox('youtube')['configured'])
                content=store.read('youtube')['content']
                self.assertIn('fixture-youtube-cookie',content)
                self.assertNotIn('fixture-instagram-cookie',content)

    def test_platform_filter_expiry_and_malformed(self):
        content = cookies() + cookies('.instagram.com', 'other-platform').split('\n',1)[1]
        filtered, metadata = filtered_cookies(content, 'youtube')
        self.assertEqual(metadata['count'], 1)
        self.assertNotIn('other-platform', filtered)
        for text in ['not netscape', cookies('.youtube.com.evil.example'), cookies(expiry=1), '# Netscape HTTP Cookie File\ninvalid']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                filtered_cookies(text, 'youtube')

    def test_encrypted_persistence_tamper_and_remove(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_SESSION_SECRET':'test-encryption-secret'}):
            settings=replace(Settings.load(),data_dir=Path(directory),password='private-test-password',allow_public=False)
            store=SessionStore(settings)
            info=store.save('youtube',cookies())
            self.assertTrue(info['configured'])
            self.assertNotIn('content', info)
            self.assertNotIn(b'fake-cookie-for-test', store.path('youtube').read_bytes())
            fresh=SessionStore(settings)
            destination=Path(directory)/'session.txt'
            self.assertTrue(fresh.materialize('youtube', destination))
            self.assertIn('fake-cookie-for-test',destination.read_text())
            raw=store.path('youtube').read_bytes()
            store.path('youtube').write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            self.assertEqual(store.describe('youtube')['status'],'unreadable')
            store.remove('youtube')
            self.assertFalse(store.describe('youtube')['configured'])
            self.assertTrue(store.has_override('youtube'))

    def test_selected_session_and_legacy_disabled_after_removal(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_SESSION_SECRET':'test-encryption-secret'}):
            root=Path(directory)
            settings=replace(Settings.load(),data_dir=root,password='private-test-password',allow_public=False,cookies_file=str(root/'legacy.txt'))
            (root/'legacy.txt').write_text(cookies())
            sessions=SessionStore(settings)
            sessions.save('youtube',cookies(value='selected-session'))
            previous=Path.cwd()
            os.chdir(directory)
            try:
                args=downloader_arguments(settings,'youtube')
                self.assertIn('--cookies',args)
                self.assertIn('selected-session',(root/'session.txt').read_text())
                self.assertNotIn('youtube:player_client=android,ios,web',args)
                sessions.remove('youtube')
                self.assertNotIn('--cookies',downloader_arguments(settings,'youtube'))
            finally:
                os.chdir(previous)

class SessionEndpointTests(unittest.TestCase):
    def test_extraction_diagnostics_work_without_transcription_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_DATA_DIR':directory,'FBR_PROVIDER':'groq','FBR_PASSWORD':'private-test-password','FBR_ALLOW_PUBLIC':'0','GROQ_API_KEY':''}), patch('app.main.worker_loop'), TestClient(app) as client:
            client.auth=('fbr','private-test-password')
            runtime=client.get('/api/diagnostics/runtime')
            self.assertEqual(runtime.status_code,200)
            self.assertEqual(runtime.json()['diagnostic_version'],1)
            result=client.post('/api/diagnostics/extraction',json={'url':'https://youtu.be/ZTSI3DDP_4A'})
            self.assertEqual(result.status_code,202)
            self.assertEqual(result.json()['kind'],'extraction_check')

    def test_private_api_import_check_delete_and_no_secret_readback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_DATA_DIR':directory,'FBR_PASSWORD':'private-test-password','FBR_ALLOW_PUBLIC':'0','FBR_SESSION_SECRET':'test-encryption-secret','GROQ_API_KEY':'test-key'}), patch('app.main.worker_loop'), TestClient(app) as client:
            client.auth=('fbr','private-test-password')
            self.assertEqual(client.get('/api/sessions').status_code,200)
            result=client.put('/api/sessions/youtube',json={'content':cookies()})
            self.assertEqual(result.status_code,200)
            listing=client.get('/api/sessions')
            self.assertNotIn('fake-cookie-for-test',listing.text)
            self.assertEqual(client.post('/api/sessions/youtube/check',json={'url':'https://www.instagram.com/reel/abc/'}).status_code,400)
            result=client.post('/api/sessions/youtube/check',json={'url':'https://youtu.be/BaW_jenozKc'})
            self.assertEqual(result.status_code,202)
            self.assertEqual(result.json()['kind'],'session_check')
            self.assertEqual(client.delete('/api/sessions/youtube',headers={'Origin':'https://evil.example'}).status_code,403)
            self.assertEqual(client.delete('/api/sessions/youtube').status_code,200)

    def test_public_mode_cannot_manage_sessions(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_DATA_DIR':directory,'FBR_ALLOW_PUBLIC':'1'}), patch('app.main.worker_loop'), TestClient(app) as client:
            self.assertEqual(client.get('/api/sessions').status_code,403)
            self.assertEqual(client.put('/api/sessions/youtube',json={'content':cookies()}).status_code,403)

    def test_upload_queue_limits_cleanup_and_origin(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'FBR_DATA_DIR':directory,'FBR_PROVIDER':'groq','GROQ_API_KEY':'test-key','FBR_ALLOW_PUBLIC':'1','FBR_MAX_QUEUE':'1'}), patch('app.main.worker_loop'), TestClient(app) as client:
            headers={'X-File-Name':'audio.mp3','Content-Type':'application/octet-stream'}
            self.assertEqual(client.post('/api/jobs/upload',headers={'X-File-Name':'evil.exe'},content=b'test').status_code,400)
            self.assertEqual(client.post('/api/jobs/upload',headers={**headers,'Origin':'https://evil.example'},content=b'audio').status_code,403)
            self.assertEqual(client.post('/api/jobs/upload',headers=headers,content=b'').status_code,400)
            result=client.post('/api/jobs/upload?language=pt',headers=headers,content=b'not-actual-audio')
            self.assertEqual(result.status_code,202)
            job=result.json()
            self.assertEqual(job['kind'],'upload')
            self.assertIsNone(job['url'])
            self.assertTrue((Path(directory)/'uploads'/(job['upload_id']+'.media')).is_file())
            self.assertEqual(client.post('/api/jobs/upload',headers=headers,content=b'other').status_code,429)
            self.assertEqual(len(list((Path(directory)/'uploads').glob('*.media'))),1)

if __name__=='__main__':
    unittest.main()
