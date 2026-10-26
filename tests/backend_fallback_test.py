"""Backend tests without requiring a running VPS, Flask or external services."""
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
    def test_quality_paths_keep_existing_360p_cache_and_isolate_other_sizes(self):
        name = "peertube_da880324-5efa-4ff0-8767-7697503bc746"
        self.assertEqual(backend.quality_path(name, 360), backend.cached_path(name))
        self.assertTrue(str(backend.quality_path(name, 480)).endswith("-480p.mp4"))
        self.assertTrue(str(backend.quality_path(name, 720)).endswith("-720p.mp4"))

    def test_peertube_confirms_every_available_resolution_before_advertising(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        name = "peertube_" + uid
        sources = [
            {"height": 360, "url": "https://tube.tchncs.de/video360.mp4"},
            {"height": 480, "url": "https://tube.tchncs.de/video480.mp4"},
            {"height": 720, "url": "https://tube.tchncs.de/video720.mp4"},
        ]
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_media_alive",
                              side_effect=lambda url: url.endswith("360.mp4") or url.endswith("720.mp4")) as probe:
                backend.remember_peertube_videos({name: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "checked": __import__("time").time(), "candidates": sources,
                }})
                self.assertEqual(backend.peertube_available_qualities(name), [360, 480, 720])
                # 480p can be downscaled from the verified 720p stream.
                self.assertEqual(probe.call_count, 3)
                self.assertEqual(backend.peertube_available_qualities(name), [360, 480, 720])
                self.assertEqual(probe.call_count, 3)  # The check is cached.

    def test_peertube_does_not_offer_unreachable_480p_or_720p(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        name = "peertube_" + uid
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_media_alive",
                              side_effect=lambda url: url.endswith("360.mp4")):
                backend.remember_peertube_videos({name: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "checked": __import__("time").time(), "candidates": [
                        {"height": q, "url": f"https://tube.tchncs.de/{q}.mp4"}
                        for q in (360, 480, 720)
                    ]
                }})
                self.assertEqual(backend.peertube_available_qualities(name), [360])

    def test_peertube_480p_source_allows_360p_and_480p_but_not_720p(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        name = "peertube_" + uid
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_media_alive", return_value=True) as probe:
                backend.remember_peertube_videos({name: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "checked": __import__("time").time(), "candidates": [
                        {"height": 480, "url": "https://tube.tchncs.de/480.mp4"}
                    ],
                }})
                self.assertEqual(backend.peertube_available_qualities(name), [360, 480])
                self.assertEqual(probe.call_count, 1)

    def test_240p_only_video_does_not_advertise_higher_resolutions(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        name = "peertube_" + uid
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "PEERTUBE_INDEX", Path(folder) / "peertube-index.json"), \
                 patch.object(backend, "peertube_media_alive", return_value=True) as probe:
                backend.remember_peertube_videos({name: {
                    "url": "https://tube.tchncs.de/videos/watch/" + uid,
                    "checked": __import__("time").time(),
                    "candidates": [{"height": 240, "url": "https://tube.tchncs.de/240.mp4"}],
                }})
                self.assertEqual(backend.peertube_available_qualities(name), [])
                self.assertEqual(probe.call_count, 0)

    def test_prepare_isolated_quality_jobs_and_video_urls(self):
        name = "peertube_da880324-5efa-4ff0-8767-7697503bc746"
        queued = []
        fake_pool = types.SimpleNamespace(submit=lambda *args: queued.append(args))
        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "VIDEO_DIR", Path(folder)), \
                 patch.object(backend, "JOBS", {}), \
                 patch.object(backend, "POOL", fake_pool), \
                 patch.object(backend, "check_rate_limit"), \
                 patch.object(backend, "request", types.SimpleNamespace(args={"quality": "720"})):
                self.assertEqual(backend.prepare(name)["status"], "na_fila")
                self.assertEqual(queued[-1][1:], (name, 720))
                self.assertEqual(backend.status(name)["status"], "na_fila")
                with patch.object(backend, "request", types.SimpleNamespace(args={"quality": "360"})):
                    self.assertEqual(backend.prepare(name)["status"], "na_fila")
                    self.assertEqual(queued[-1][1:], (name, 360))
                backend.quality_path(name, 720).write_bytes(b"a ready 720p file")
                self.assertIn("quality=720", backend.status(name)["video_url"])
                self.assertFalse(backend.quality_path(name, 360).is_file())

    def test_ffmpeg_720p_uses_supported_level_and_separate_mp4(self):
        name = "peertube_da880324-5efa-4ff0-8767-7697503bc746"
        commands = []

        def fake_download(identifier, folder, quality):
            src = folder / "source.mp4"
            src.write_bytes(b"source")
            return src

        def fake_ffmpeg(command, **_kwargs):
            commands.append(command)
            Path(command[-1]).write_bytes(b"converted 720p")
            return types.SimpleNamespace(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(backend, "VIDEO_DIR", Path(folder)), \
                 patch.object(backend, "peertube_video", side_effect=fake_download), \
                 patch.object(backend, "cache_cleanup"), \
                 patch.object(backend.subprocess, "run", side_effect=fake_ffmpeg):
                backend.quality_path(name, 360).write_bytes(b"previous 360p")
                backend.prepare_video(name, 720)
                self.assertEqual(backend.quality_path(name, 720).read_bytes(), b"converted 720p")
                self.assertEqual(backend.quality_path(name, 360).read_bytes(), b"previous 360p")
                self.assertEqual(commands[0][commands[0].index("-vf") + 1], "scale=-2:720,fps=30")
                self.assertEqual(commands[0][commands[0].index("-level") + 1], "3.1")

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
                     "name": "Hino Nacional Brasileiro", "checked": 1700000000, "media_checked": True
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

    def test_peertube_prefers_working_alternate_download_to_missing_mp4(self):
        uid = "da880324-5efa-4ff0-8767-7697503bc746"
        dead_url = "https://tube.tchncs.de/static/web-videos/missing.mp4"
        alive_url = "https://tube.tchncs.de/download/videos/" + uid + "/360"
        details = {"uuid": uid, "duration": 45, "name": "Teste", "files": [{
            "fileUrl": dead_url, "fileDownloadUrl": alive_url,
            "resolution": {"id": 360}, "size": 2000000,
        }]}
        choices = backend.peertube_file_choices(details)
        self.assertIn((360, dead_url), choices)
        self.assertIn((360, alive_url), choices)
        item = {"id": "peertube_" + uid, "original_url": "https://tube.tchncs.de/videos/watch/" + uid,
                "title": "Teste"}
        with patch.object(backend, "peertube_json", return_value=details), \
             patch.object(backend, "peertube_media_alive", side_effect=lambda url: url == alive_url):
            result = backend.peertube_check_search_item(item)
        self.assertEqual(result["sources"], [{"height": 360, "url": alive_url}])

    def test_peertube_rejects_http_404_media_preflight(self):
        from urllib.error import HTTPError

        class MissingMedia:
            def open(self, *args, **kwargs):
                raise HTTPError("https://tube.tchncs.de/x.mp4", 404, "Not Found", {}, None)

        with patch.object(backend, "peertube_public_network", return_value=True), \
             patch.object(backend, "PEERTUBE_OPENER", MissingMedia()):
            self.assertFalse(backend.peertube_media_alive("https://tube.tchncs.de/x.mp4"))

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
                    "checked": __import__("time").time(), "name": "Hino Nacional", "media_checked": True,
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
