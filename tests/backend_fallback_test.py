"""Unit tests for the Piped fallback without requiring a running VPS or Flask."""
import importlib.util
import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class AppStub:
    def __init__(self, name):
        self.logger = types.SimpleNamespace(warning=lambda *args: None)

    def get(self, path):
        return lambda fn: fn

    def after_request(self, fn):
        return fn


sys.modules["flask"] = types.SimpleNamespace(
    Flask=AppStub, abort=lambda *a: None, jsonify=lambda x: x,
    request=None, send_file=lambda *a, **k: None,
    send_from_directory=lambda *a, **k: None,
)
script = Path(__file__).resolve().parents[1] / "backend" / "app.py"
spec = importlib.util.spec_from_file_location("retrotube_backend_test", script)
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


class FakeProxy:
    def open(self, request, timeout):
        return io.BytesIO(b"mp4-test-bytes")


class FakeInvidiousProxy:
    def open(self, request, timeout):
        assert request.full_url.startswith("https://inv.nadeko.net/latest_version?")
        assert "local=true" in request.full_url
        result = io.BytesIO(b"invidious-mp4-test")
        result.headers = {"Content-Type": "video/mp4"}
        return result


class PipedFallbackTests(unittest.TestCase):
    def test_hosts_are_restricted(self):
        self.assertTrue(backend.approved_media_url("https://pipedproxy-bom.kavin.rocks/videoplayback?id=1"))
        self.assertFalse(backend.approved_media_url("http://127.0.0.1/private"))
        self.assertFalse(backend.approved_media_url("https://localhost/admin"))
        self.assertFalse(backend.approved_media_url("https://kavin.rocks.evil.example/video"))
        self.assertFalse(backend.approved_media_url("https://adminforge.de:8000/video"))
        self.assertTrue(backend.approved_media_url("https://inv.nadeko.net/latest_version?id=abc"))
        self.assertFalse(backend.approved_media_url("https://inv.nadeko.net.evil.example/video"))

    def test_downloads_progressive_video_from_approved_proxy(self):
        metadata = {
            "duration": 8, "title": "Teste", "livestream": False,
            "proxyUrl": "https://pipedproxy-bom.kavin.rocks",
            "videoStreams": [{
                "videoOnly": False, "mimeType": "video/mp4", "height": 360,
                "url": "https://pipedproxy-bom.kavin.rocks/videoplayback?id=1",
            }],
        }
        with tempfile.TemporaryDirectory() as work:
            with patch.object(backend, "piped_json", return_value=metadata), \
                 patch.object(backend, "PIPED_OPENER", FakeProxy()):
                path = backend.piped_video("dQw4w9WgXcQ", Path(work))
                self.assertEqual(path.read_bytes(), b"mp4-test-bytes")

    def test_rejects_untrusted_proxy_from_json(self):
        metadata = {
            "duration": 8, "proxyUrl": "https://localhost",
            "videoStreams": [{
                "videoOnly": False, "mimeType": "video/mp4", "height": 360,
                "url": "https://localhost/private",
            }],
        }
        with tempfile.TemporaryDirectory() as work:
            with patch.object(backend, "piped_json", return_value=metadata):
                with self.assertRaisesRegex(RuntimeError, "alternativas"):
                    backend.piped_video("dQw4w9WgXcQ", Path(work))

    def test_invidious_downloads_progressive_mp4_through_local_proxy(self):
        metadata = {
            "lengthSeconds": 12, "title": "Vídeo brasileiro", "liveNow": False,
            "formatStreams": [
                {"itag": "18", "qualityLabel": "360p", "type": "video/mp4"},
                {"itag": "22", "qualityLabel": "720p", "type": "video/mp4"},
            ],
        }
        with tempfile.TemporaryDirectory() as work:
            with patch.object(backend, "INVIDIOUS_INSTANCES", ("https://inv.nadeko.net",)), \
                 patch.object(backend, "invidious_json", return_value=metadata), \
                 patch.object(backend, "PIPED_OPENER", FakeInvidiousProxy()):
                path = backend.invidious_video("eWNhOwEIdRk", Path(work))
                self.assertEqual(path.read_bytes(), b"invidious-mp4-test")

    def test_invidious_rejects_missing_progressive_mp4(self):
        metadata = {"lengthSeconds": 10, "formatStreams": [
            {"itag": "251", "type": "audio/webm", "qualityLabel": "360p"},
        ]}
        with tempfile.TemporaryDirectory() as work:
            with patch.object(backend, "INVIDIOUS_INSTANCES", ("https://inv.nadeko.net",)), \
                 patch.object(backend, "invidious_json", return_value=metadata):
                with self.assertRaisesRegex(RuntimeError, "Sem MP4"):
                    backend.invidious_video("eWNhOwEIdRk", Path(work))

    def test_invidious_search_results(self):
        entries = [{"type": "video", "videoId": "eWNhOwEIdRk", "title": "Brasileiro", "author": "Canal BR", "lengthSeconds": 10},
                   {"type": "channel", "author": "Canal ignorado"}]
        with patch.object(backend, "INVIDIOUS_INSTANCES", ("https://inv.nadeko.net",)), \
             patch.object(backend, "invidious_json", return_value=entries):
            results = backend.search_invidious("música brasileira")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["id"], "eWNhOwEIdRk")


if __name__ == "__main__":
    unittest.main()
