import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app.core import Settings, normalize_segments, publication_url, subtitles
from app.main import app
from app.pipeline import UserError, prepare_chunks, request_transcription, transcribe_chunks
from app.store import JobStore

class URLTests(unittest.TestCase):
    def test_platforms_and_tracking_removal(self):
        cases = [
            ("https://youtu.be/BaW_jenozKc?si=tracking", "YouTube"),
            ("https://www.youtube.com/shorts/BaW_jenozKc", "YouTube"),
            ("https://www.youtube.com/watch?v=BaW_jenozKc&list=other", "YouTube"),
            ("https://www.instagram.com/reel/ABC123/?igsh=tracking", "Instagram"),
            ("https://www.instagram.com/stories/account/123456/", "Instagram"),
            ("https://www.tiktok.com/@account/video/123456", "TikTok"),
            ("https://vm.tiktok.com/ZMabc/", "TikTok"),
            ("https://www.facebook.com/reel/123456", "Facebook"),
            ("https://www.facebook.com/watch/?v=123456", "Facebook"),
            ("https://www.facebook.com/stories.php?story_fbid=123&id=456", "Facebook"),
            ("https://x.com/account/status/123456", "X"),
        ]
        for url, platform in cases:
            with self.subTest(url=url):
                clean, detected = publication_url(url)
                self.assertEqual(detected, platform)
                self.assertNotIn("tracking", clean)

    def test_untrusted_or_nonpublication_links(self):
        for url in ["file:///etc/passwd", "https://127.0.0.1/", "http://youtube.com/watch?v=BaW_jenozKc", "https://youtube.com.evil.example/watch?v=BaW_jenozKc", "https://youtube.com@127.0.0.1/watch", "https://www.youtube.com:444/watch?v=BaW_jenozKc", "https://www.youtube.com/playlist?list=abc", "https://instagram.com/profile/", "https://example.com/video", "https://x.com/account/status/123\n"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                publication_url(url)

class ExportTests(unittest.TestCase):
    def test_real_offsets_and_formats(self):
        segment = normalize_segments([{"start": 1.5, "end": 3.1, "text": "Olá, mundo."}], 600, 10)
        self.assertEqual(segment[0]["start"], 601.5)
        self.assertIn("00:10:01,500 --> 00:10:03,100", subtitles(segment))
        self.assertTrue(subtitles(segment, True).startswith("WEBVTT"))
        self.assertIn("00:10:01.500", subtitles(segment, True))

    def test_bad_times(self):
        with self.assertRaises(ValueError):
            normalize_segments([{"start": float("nan"), "end": 3, "text": "test"}], 0, 10)

class APITests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(Settings.load(), key="test-key-not-real")
        self.directory = tempfile.TemporaryDirectory()
        self.audio = Path(self.directory.name) / "chunk.mp3"
        self.audio.write_bytes(b"audio-test")

    def tearDown(self):
        self.directory.cleanup()

    def test_groq_multipart_and_timestamps(self):
        def handler(request):
            self.assertEqual(request.url.host, "api.groq.com")
            self.assertEqual(request.headers["authorization"], "Bearer test-key-not-real")
            body = request.read()
            self.assertIn(b"whisper-large-v3-turbo", body)
            self.assertIn(b"verbose_json", body)
            self.assertIn(b"timestamp_granularities[]", body)
            self.assertIn(b'filename="chunk.mp3"', body)
            return httpx.Response(200, json={"text": "Olá.", "segments": [{"start": 0, "end": 1, "text": "Olá."}], "language": "portuguese"})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = request_transcription(self.audio, self.settings, "pt", client)
        self.assertEqual(result["text"], "Olá.")

    def test_openai_mini_returns_text_without_fake_times(self):
        settings = replace(self.settings, provider="openai", model="gpt-4o-mini-transcribe")
        def handler(request):
            self.assertEqual(request.url.host, "api.openai.com")
            self.assertNotIn(b"timestamp_granularities", request.read())
            return httpx.Response(200, json={"text": "Texto."})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("app.pipeline.duration_of", return_value=8):
            result = transcribe_chunks([self.audio], 8, settings, "auto", client)
        self.assertEqual(result["text"], "Texto.")
        self.assertEqual(result["segments"], [])

    def test_rate_limit_error_does_not_expose_provider_body(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json={"secret": "internal-secret"}))) as client:
            with self.assertRaises(UserError) as caught:
                request_transcription(self.audio, self.settings, "auto", client)
        self.assertNotIn("internal-secret", str(caught.exception))

    def test_multiple_chunks_offsets(self):
        def handler(request):
            return httpx.Response(200, json={"text": "Parte.", "segments": [{"start": 1, "end": 2, "text": "Parte."}]})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client, patch("app.pipeline.duration_of", return_value=600):
            result = transcribe_chunks([self.audio, self.audio], 1200, self.settings, "auto", client)
        self.assertEqual(result["segments"][1]["start"], 601)

class ServiceTests(unittest.TestCase):
    def test_auth_and_missing_key(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"FBR_DATA_DIR": directory, "FBR_PROVIDER": "groq", "FBR_PASSWORD": "test-password-123", "FBR_ALLOW_PUBLIC": "0", "GROQ_API_KEY": ""}), TestClient(app) as client:
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/").status_code, 401)
            response = client.get("/", auth=("fbr", "test-password-123"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("FBR-Transcript", response.text)
            config = client.get("/api/config", auth=("fbr", "test-password-123")).json()
            self.assertFalse(config["ready"])
            self.assertNotIn("key", config)
            self.assertEqual(client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc"}, auth=("fbr", "test-password-123")).status_code, 503)

    def test_job_export_and_csrf(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"FBR_DATA_DIR": directory, "FBR_PROVIDER": "groq", "FBR_ALLOW_PUBLIC": "1", "GROQ_API_KEY": "not-real"}), patch("app.main.worker_loop"), TestClient(app) as client:
            self.assertEqual(client.post("/api/jobs", json={"url": "https://127.0.0.1/video"}).status_code, 400)
            self.assertEqual(client.post("/api/jobs", headers={"Origin": "https://evil.example"}, json={"url": "https://youtu.be/BaW_jenozKc"}).status_code, 403)
            response = client.post("/api/jobs", json={"url": "https://youtu.be/BaW_jenozKc", "language": "pt"})
            self.assertEqual(response.status_code, 202)
            identifier = response.json()["id"]
            self.assertEqual(client.get(f"/api/jobs/{identifier}/download/txt").status_code, 409)
            app.state.store.update(identifier, status="done", result={"text": "Teste de exportação.", "segments": [{"start": 0, "end": 1, "text": "Teste."}]})
            self.assertEqual(client.get(f"/api/jobs/{identifier}/download/txt").text, "Teste de exportação.")
            self.assertIn("00:00:00,000", client.get(f"/api/jobs/{identifier}/download/srt").text)
            self.assertEqual(client.get("/api/jobs/invalid").status_code, 404)

    def test_recovery_queue_limit_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory))
            store.initialize()
            job = store.create({"url": "link"}, 1)
            with self.assertRaises(ValueError):
                store.create({"url": "other"}, 1)
            store.update(job["id"], status="running")
            store.initialize()
            self.assertEqual(store.get(job["id"])["status"], "failed")
            with store.connect() as connection:
                connection.execute("UPDATE jobs SET updated=0")
            store.cleanup(1)
            self.assertIsNone(store.get(job["id"]))

if __name__ == "__main__":
    unittest.main()
