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


class FakePeerVideo:
    def open(self, request, timeout):
        assert request.full_url == "https://tube.tchncs.de/static/webseed/test-360.mp4"
        result = io.BytesIO(b"peertube-test-video-bytes")
        result.headers = {"Content-Type": "video/mp4"}
        return result


class FakePeerThumbnail:
    def open(self, request, timeout):
        assert request.full_url == "https://tube.tchncs.de/lazy-static/thumbnails/demo.jpg"
        result = io.BytesIO(b"tiny-jpeg-test")
        result.headers = {"Content-Type": "image/jpeg"}
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

    def test_dailymotion_search_does_not_require_an_api_key(self):
        searched = []

        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=False):
                searched.append(url)
                return {"entries": [
                    {"id": "x5kesuj", "title": "Notícia do Brasil", "uploader": "TV brasileira", "duration": 99,
                     "thumbnail": "https://s1.dmcdn.net/v/example"},
                    {"id": "@invalid", "title": "Inválido"},
                ]}

        with patch.object(backend, "YoutubeDL", FakeYoutubeDL):
            results = backend.search_dailymotion("notícias brasil")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "dailymotion_x5kesuj")
        self.assertEqual(results[0]["title"], "Notícia do Brasil")
        self.assertIn("www.dailymotion.com/search/", searched[0])

    def test_video_validation_accepts_dailymotion_and_rejects_untrusted_ids(self):
        self.assertEqual(backend.valid_id("dailymotion_x5kesuj"), "dailymotion_x5kesuj")
        self.assertEqual(backend.valid_id("eWNhOwEIdRk"), "eWNhOwEIdRk")
        self.assertFalse(backend.DAILYMOTION_ID.fullmatch("dailymotion_../../secret"))

    def test_peertube_blocks_local_or_untrusted_urls(self):
        self.assertFalse(backend.peertube_public_network("http://tube.tchncs.de/watch"))
        self.assertFalse(backend.peertube_public_network("https://127.0.0.1/admin"))
        self.assertFalse(backend.peertube_public_network("https://localhost/test"))
        self.assertFalse(backend.peertube_public_network("https://user:pass@tube.tchncs.de/api"))
        self.assertFalse(backend.peertube_public_network("https://tube.tchncs.de:8080/api"))
        self.assertTrue(backend.peertube_public_url("https://tube.tchncs.de/api/v1/videos/uuid"))

    def test_peertube_search_without_key_and_persists_origin(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        response = {"data": [{
            "uuid": uid, "name": "Hino Nacional Brasileiro", "duration": 100,
            "url": "https://tube.tchncs.de/videos/watch/" + uid,
            "thumbnailUrl": "https://tube.tchncs.de/lazy-static/thumbnails/a.jpg",
            "channel": {"displayName": "Música BR"},
        }]}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_json", return_value=response), \
                 patch.object(backend, "peertube_check_search_item", return_value={
                     "sources": [{"height": 360, "url": "https://tube.tchncs.de/static/webseed/test-360.mp4"}],
                     "name": "Hino Nacional Brasileiro", "checked": 1700000000
                 }), \
                 patch.object(backend, "peertube_public_network", return_value=True):
                results = backend.search_peertube("brasil")
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["id"], "peertube_" + uid)
                self.assertIn("Hino Nacional", results[0]["title"])
                self.assertEqual(backend.peertube_link(results[0]["id"]), response["data"][0]["url"])
                self.assertEqual(results[0]["thumbnail"], "/api/thumb/peertube_" + uid)
                self.assertEqual(backend.peertube_thumbnail_link(results[0]["id"]),
                                 response["data"][0]["thumbnailUrl"])

    def test_peertube_search_omits_unreachable_origin(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        response = {"data": [{"uuid": uid, "name": "Vídeo indisponível", "duration": 50,
                               "url": "https://peertube.uno/videos/watch/" + uid}]}
        with patch.object(backend, "peertube_json", return_value=response), \
             patch.object(backend, "peertube_public_network", return_value=True), \
             patch.object(backend, "peertube_check_search_item", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "origem acessível"):
                backend.search_peertube("noticias")

    def test_peertube_uses_verified_cached_media_without_origin_api(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        identifier = "peertube_" + uid
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_json", side_effect=AssertionError("não deve consultar origem")), \
                 patch.object(backend, "peertube_public_network", return_value=True), \
                 patch.object(backend, "PEERTUBE_OPENER", FakePeerVideo()):
                backend.remember_peertube_videos({identifier: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "checked": __import__("time").time(), "name": "Hino Nacional",
                    "sources": [{"height": 360, "url": "https://tube.tchncs.de/static/webseed/test-360.mp4"}],
                }})
                self.assertEqual(backend.peertube_video(identifier, Path(folder)).read_bytes(),
                                 b"peertube-test-video-bytes")

    def test_peertube_thumbnail_proxies_to_old_safari(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        name = "peertube_" + uid
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_public_network", return_value=True), \
                 patch.object(backend, "PEERTUBE_OPENER", FakePeerThumbnail()), \
                 patch.object(backend, "check_rate_limit"), \
                 patch.object(backend.app, "response_class", create=True,
                              side_effect=lambda data, **kwargs: (data, kwargs)):
                backend.remember_peertube_videos({name: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "thumbnail": "https://tube.tchncs.de/lazy-static/thumbnails/demo.jpg",
                }})
                image, headers = backend.thumbnail(name)
                self.assertEqual(image, b"tiny-jpeg-test")
                self.assertEqual(headers["mimetype"], "image/jpeg")

    def test_peertube_downloads_progressive_mp4_from_video_details(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        details = {"uuid": uid, "duration": 100, "name": "Hino Nacional", "files": [
            {"fileUrl": "https://tube.tchncs.de/static/webseed/test-360.mp4", "resolution": {"id": 360}}
        ]}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_json", return_value=details), \
                 patch.object(backend, "peertube_public_network", return_value=True), \
                 patch.object(backend, "PEERTUBE_OPENER", FakePeerVideo()):
                backend.remember_peertube_videos({"peertube_" + uid: "https://tube.tchncs.de/videos/watch/" + uid})
                result = backend.peertube_video("peertube_" + uid, Path(folder))
                self.assertEqual(result.read_bytes(), b"peertube-test-video-bytes")

    def test_peertube_rejects_unsafe_media_source(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        details = {"uuid": uid, "duration": 60, "files": [
            {"fileUrl": "http://127.0.0.1/hidden.mp4", "resolution": {"id": 360}}
        ]}
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_json", return_value=details):
                backend.remember_peertube_videos({"peertube_" + uid: "https://tube.tchncs.de/videos/watch/" + uid})
                with self.assertRaisesRegex(ValueError, "não oferece"):
                    backend.peertube_video("peertube_" + uid, Path(folder))

    def test_peertube_retries_metadata_on_federated_server(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        urls = []

        def metadata(url, timeout=15):
            urls.append(url)
            if url.startswith("https://tube.tchncs.de"):
                raise TimeoutError("The read operation timed out")
            return {"uuid": uid, "duration": 35, "name": "Teste de rede", "files": [
                {"fileUrl": "https://tube.tchncs.de/static/webseed/test-360.mp4",
                 "resolution": {"id": 360}}
            ]}

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_json", side_effect=metadata), \
                 patch.object(backend, "peertube_public_network", return_value=True), \
                 patch.object(backend, "PEERTUBE_OPENER", FakePeerVideo()):
                backend.remember_peertube_videos({"peertube_" + uid: "https://tube.tchncs.de/videos/watch/" + uid})
                self.assertTrue(backend.peertube_video("peertube_" + uid, Path(folder)).is_file())
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[1].startswith("https://peertube.tv/api/v1/videos/"))


if __name__ == "__main__":
    unittest.main()
