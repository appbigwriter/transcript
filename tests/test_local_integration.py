import hashlib
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient

from app.main import app
from app.store import JobStore
from desktop.extract import choose_caption, normalize_cues, parse_caption, extract
from desktop.runtime import Queue, Runner, protect_password, server_url


def caption_process_fixture(url, language, config, directory, pipe):
    pipe.send(('artifact', {'kind': 'transcript', 'text': 'texto ' * 20000}))
    pipe.close()


class CaptionTests(unittest.TestCase):
    def test_collector_receives_result_when_child_closes_pipe(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Queue(root)
            queue.add(['https://youtu.be/ZTSI3DDP_4A'])
            runner = Runner(queue)
            with patch('desktop.runtime.extract_child', caption_process_fixture):
                result = runner.collect(queue.all()[0], {'language': 'pt'}, Path(root))
            self.assertEqual(result['text'], 'texto ' * 20000)

    def test_growing_auto_captions_and_later_spoken_repetitions(self):
        cues = [dict(start=0, end=2, text='Olá'), dict(start=.5, end=2.5, text='Olá mundo'),
                dict(start=1, end=3, text='Olá mundo agora'), dict(start=10, end=12, text='Olá mundo agora')]
        result = normalize_cues(cues)
        self.assertEqual([c['text'] for c in result], ['Olá', 'mundo', 'agora', 'Olá mundo agora'])

    def test_vtt_and_json3_parse_real_times(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'subtitle.vtt'
            path.write_text('WEBVTT\n\n00:01.500 --> 00:03.250 align:start\n<v Narrador>Olá &amp; você</v>\n\n', encoding='utf-8')
            self.assertEqual(parse_caption(path), [dict(start=1.5, end=3.25, text='Olá & você')])
            path = path.with_suffix('.json3')
            path.write_text(json.dumps({'events': [{'tStartMs': 1500, 'dDurationMs': 1750, 'segs': [{'utf8': 'Olá'}]}]}))
            self.assertEqual(parse_caption(path)[0]['start'], 1.5)

    def test_requested_language_precedes_manual_other_language(self):
        info = {'subtitles': {'en': [{'ext': 'vtt'}]}, 'automatic_captions': {'pt-orig': [{'ext': 'json3'}]}}
        self.assertEqual(choose_caption(info, 'pt'), ('pt-orig', 'automatic'))
        info['subtitles']['pt'] = [{'ext': 'vtt'}]
        self.assertEqual(choose_caption(info, 'pt'), ('pt', 'manual'))

    def test_caption_path_does_not_download_audio_or_require_ffmpeg(self):
        with tempfile.TemporaryDirectory() as root:
            info = dict(title='Teste', duration=2, subtitles={'pt': [{'ext': 'json3'}]})
            class Downloader:
                def __init__(self, options): self.params = options
                def __enter__(self): return self
                def __exit__(self, *args): pass
                def extract_info(self, url, download): return info
                def process_ie_result(self, metadata, download):
                    self_test.assertTrue(self.params['skip_download'])
                    Path(root, 'source.pt.json3').write_text(json.dumps({'events': [
                        {'tStartMs': 0, 'dDurationMs': 2000, 'segs': [{'utf8': 'Texto existente'}]}]}))
            self_test = self
            with patch('yt_dlp.YoutubeDL', Downloader), patch('desktop.extract.binary', return_value=None):
                artifact = extract('https://youtu.be/ZTSI3DDP_4A', 'pt', {'browser': 'none'}, Path(root))
            self.assertEqual(artifact['text'], 'Texto existente')
            self.assertEqual(artifact['source_type'], 'captions_manual')
            self.assertNotIn('audio', artifact)

    def test_simultaneous_submissions_reserve_only_one_server_job(self):
        with tempfile.TemporaryDirectory() as root:
            store = JobStore(Path(root))
            store.initialize()
            def submit(_):
                return store.create_local(dict(kind='upload'), 10, 'a'*32, 'same-fingerprint')
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(submit, range(8)))
            self.assertEqual(len({job['id'] for job, created in results}), 1)
            self.assertEqual(sum(created for job, created in results), 1)

    def test_local_queue_recovers_uncertain_send_and_deduplicates_links(self):
        with tempfile.TemporaryDirectory() as root:
            queue = Queue(root)
            self.assertEqual(queue.add(['https://youtu.be/ZTSI3DDP_4A', 'https://youtu.be/ZTSI3DDP_4A']), 1)
            task = queue.all()[0]
            queue.update(task['id'], status='submitting')
            self.assertEqual(Queue(root).all()[0]['status'], 'uncertain')
            self.assertNotIn('password', queue.all()[0])

    def test_remote_connection_requires_https_and_no_embedded_password(self):
        for value in ['http://example.com', 'https://user:secret@example.com', 'https://example.com/api', 'https://example.com?key=x']:
            with self.assertRaises(ValueError): server_url(value)
        self.assertEqual(server_url('http://127.0.0.1:4180/'), 'http://127.0.0.1:4180')

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_windows_password_is_protected_and_round_trips(self):
        protected = protect_password('test-password-only')
        self.assertNotIn('test-password-only', protected)
        self.assertEqual(protect_password(protected, decrypt=True), 'test-password-only')


class LocalAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'FBR_DATA_DIR': self.temp.name, 'FBR_ALLOW_PUBLIC': '0',
            'FBR_PASSWORD': 'test-password-long', 'FBR_USERNAME': 'fbr', 'FBR_PROVIDER': 'groq', 'GROQ_API_KEY': ''})
        self.environment.start()
        self.worker = patch('app.main.worker_loop')
        self.worker.start()
        self.client = TestClient(app)
        self.client.__enter__()
        self.client.auth = ('fbr', 'test-password-long')
        self.identifier = 'a' * 32
        self.source = dict(url='https://youtu.be/ZTSI3DDP_4A', title='Publicação de teste', language='pt', duration=2)
        self.caption = dict(self.source, text='Olá mundo', segments=[dict(start=0, end=2, text='Olá mundo')], source_type='captions_manual')

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.worker.stop()
        self.environment.stop()
        self.temp.cleanup()

    def test_caption_import_without_transcription_key_and_exactly_once(self):
        self.assertFalse(self.client.get('/api/local/capabilities').json()['audio_ready'])
        headers = {'x-local-request-id': self.identifier}
        first = self.client.post('/api/local/transcripts', headers=headers, json=self.caption)
        self.assertEqual(first.status_code, 200, first.text)
        job = first.json()
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['result']['provider'], 'platform_captions')
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=self.caption).json()['id'], job['id'])
        self.assertEqual(self.client.get('/api/local/submissions/' + self.identifier).json()['id'], job['id'])
        exported = self.client.get('/api/jobs/' + job['id'] + '/download/json')
        self.assertEqual(exported.json()['source_type'], 'captions_manual')
        changed = dict(self.caption, text='Outro texto')
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=changed).status_code, 409)

    def test_expired_submission_cannot_create_a_second_chargeable_job(self):
        headers = {'x-local-request-id': self.identifier}
        job = self.client.post('/api/local/transcripts', headers=headers, json=self.caption).json()
        with self.client.app.state.store.connect() as c:
            c.execute('DELETE FROM jobs WHERE id=?', (job['id'],))
        self.assertEqual(self.client.get('/api/local/submissions/' + self.identifier).status_code, 410)
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=self.caption).status_code, 410)

    def test_bad_caption_and_anonymous_import_are_refused(self):
        headers = {'x-local-request-id': self.identifier}
        bad = dict(self.caption, segments=[dict(start=4, end=2, text='Ruim')])
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=bad).status_code, 400)
        bad = dict(self.caption, url='https://127.0.0.1/private')
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=bad).status_code, 400)
        self.assertEqual(self.client.post('/api/local/transcripts', headers=headers, json=self.caption, auth=('bad', 'bad')).status_code, 401)
        self.assertEqual(self.client.post('/api/local/transcripts', headers=dict(headers, Origin='https://evil.example'), json=self.caption).status_code, 403)

    def test_audio_digest_metadata_and_duplicate_upload_cleanup(self):
        from dataclasses import replace
        self.client.app.state.settings = replace(self.client.app.state.settings, key='test-not-real')
        content = b'test-upload-not-real-audio'
        headers = {'x-local-request-id': self.identifier, 'x-content-sha256': hashlib.sha256(content).hexdigest(),
                   'x-local-source': quote(json.dumps(self.source)), 'content-type': 'audio/mpeg'}
        first = self.client.post('/api/local/audio', headers=headers, content=content)
        self.assertEqual(first.status_code, 202, first.text)
        second = self.client.post('/api/local/audio', headers=headers, content=content)
        self.assertEqual(first.json()['id'], second.json()['id'])
        self.assertEqual(len(list((Path(self.temp.name) / 'uploads').glob('*.media'))), 1)
        self.assertEqual(first.json()['source_url'], self.source['url'])
        self.assertEqual(self.client.post('/api/local/audio', headers=dict(headers, **{'x-local-request-id': 'b'*32}), content=b'wrong').status_code, 400)
        self.assertEqual(len(list((Path(self.temp.name) / 'uploads').glob('*.media'))), 1)


if __name__ == '__main__':
    unittest.main()
