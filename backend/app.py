"""RetroTube video service. Run behind HTTPS with Gunicorn/Caddy on a VPS."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None

app = Flask(__name__)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
VIDEO_DIR = Path(os.getenv("RETROTUBE_CACHE_DIR", "/tmp/retrotube-cache")).resolve()
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
MAX_DURATION = int(os.getenv("RETROTUBE_MAX_DURATION", "1800"))
MAX_ACTIVE_JOBS = int(os.getenv("RETROTUBE_MAX_ACTIVE_JOBS", "2"))
MAX_QUEUE = int(os.getenv("RETROTUBE_MAX_QUEUE", "8"))
MAX_CACHE_GB = float(os.getenv("RETROTUBE_MAX_CACHE_GB", "3"))
MAX_SOURCE_BYTES = 250 * 1024 * 1024
ALLOWED_ORIGIN = os.getenv("RETROTUBE_FRONTEND_ORIGIN", "https://white13291.github.io").rstrip("/")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Public Piped instances. Their metadata and media proxies are third-party services,
# so they may disappear or decline requests at any time.
PIPED_INSTANCES = (
    "https://pipedapi.kavin.rocks",
    "https://pipedapi-libre.kavin.rocks",
    "https://pipedapi.adminforge.de",
    "https://piped-api.privacy.com.de",
)
PIPED_PROXY_SUFFIXES = ("kavin.rocks", "adminforge.de", "privacy.com.de")
# Invidious serves public video metadata and can proxy an MP4 via
# /latest_version?local=true. Keep endpoints fixed: clients may only send IDs.
# Only instances published by the Invidious project are used.
INVIDIOUS_INSTANCES = (
    "https://inv.nadeko.net",
    "https://invidious.nerdvpn.de",
    "https://yt.chocolatemoo53.com",
    "https://invidious.tiekoetter.com",
)
INVIDIOUS_MEDIA_HOSTS = tuple(urlsplit(base).hostname for base in INVIDIOUS_INSTANCES)
POOL = ThreadPoolExecutor(max_workers=MAX_ACTIVE_JOBS)
JOBS: dict[str, dict] = {}
LOCK = threading.RLock()
DEMO_LOCK = threading.Lock()
REQUESTS: dict[str, deque] = defaultdict(deque)


def approved_media_url(url: str) -> bool:
    """Limit media fetches to vetted HTTPS proxy domains (never private IPs)."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return (parts.scheme == "https" and bool(host) and
                not parts.username and not parts.password and parts.port in (None, 443) and
                (host in INVIDIOUS_MEDIA_HOSTS or
                 any(host == suffix or host.endswith("." + suffix) for suffix in PIPED_PROXY_SUFFIXES)))
    except (TypeError, ValueError):
        return False


class ApprovedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not approved_media_url(newurl):
            raise ValueError("Redirecionamento externo não permitido")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


PIPED_OPENER = build_opener(ApprovedRedirects())


def invidious_json(base: str, path: str):
    """Read a small JSON response only from our allowlisted Invidious instances."""
    if base not in INVIDIOUS_INSTANCES:
        raise ValueError("Instância Invidious não autorizada")
    url = base + path
    if not approved_media_url(url):
        raise ValueError("URL não autorizada")
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 RetroTube/1.0", "Accept": "application/json"})
    with PIPED_OPENER.open(req, timeout=15) as remote:
        size = remote.headers.get("Content-Length")
        if size and int(size) > 2_000_000:
            raise ValueError("Resposta da API muito grande")
        raw = remote.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError("Resposta da API muito grande")
    return json.loads(raw)


def invidious_video(identifier: str, work: Path) -> Path:
    """Obtain a progressive MP4 via a public Invidious instance's local proxy."""
    failures = []
    for base in INVIDIOUS_INSTANCES:
        host = urlsplit(base).hostname or "invidious"
        source = work / "invidious-source.mp4"
        try:
            data = invidious_json(base, "/api/v1/videos/" + identifier + "?region=BR")
            if not isinstance(data, dict):
                raise ValueError("Metadados inválidos")
            duration = int(data.get("lengthSeconds") or 0)
            if duration <= 0 or duration > MAX_DURATION or data.get("liveNow"):
                raise ValueError("Vídeo ao vivo, sem duração ou muito longo")
            # An itag 18 progressive MP4 includes H.264 video and AAC audio.
            # Other progressive MP4s are usable after FFmpeg's iOS 8 conversion.
            choices = []
            for item in data.get("formatStreams") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    itag = int(item.get("itag") or 0)
                    quality = int(re.search(r"\d+", str(item.get("qualityLabel") or "360p")).group())
                except (TypeError, ValueError, AttributeError):
                    continue
                if 0 < itag < 1000 and 0 < quality <= 480 and (
                    "mp4" in str(item.get("type") or "").lower() or
                    str(item.get("container") or "").lower() == "mp4"
                ):
                    choices.append((quality, itag))
            if not choices:
                raise ValueError("Sem MP4 progressivo com áudio até 480p")
            choices.sort(reverse=True)
            set_job(identifier, "alternativo", title=str(data.get("title") or "Vídeo")[:160])
            # 'local=true' keeps the media request on the Invidious video proxy.
            url = base + "/latest_version?" + urlencode({
                "id": identifier, "itag": choices[0][1], "local": "true"
            })
            with PIPED_OPENER.open(Request(url, headers={"User-Agent": "Mozilla/5.0 RetroTube/1.0"}), timeout=25) as remote:
                mime = str(remote.headers.get("Content-Type") or "").split(";")[0].lower().strip()
                if mime not in ("video/mp4", "application/octet-stream", "binary/octet-stream"):
                    raise ValueError("O provedor não devolveu um vídeo MP4")
                if remote.headers.get("Content-Length") and int(remote.headers["Content-Length"]) > MAX_SOURCE_BYTES:
                    raise ValueError("Arquivo excede 250 MB")
                total = 0
                started = time.monotonic()
                with source.open("wb") as target:
                    while True:
                        if time.monotonic() - started > 300:
                            raise TimeoutError("O provedor demorou demais")
                        chunk = remote.read(131072)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_SOURCE_BYTES:
                            raise ValueError("Arquivo excede 250 MB")
                        target.write(chunk)
            if source.stat().st_size:
                return source
            raise ValueError("Vídeo vazio")
        except HTTPError as error:
            failures.append(host + ": HTTP " + str(error.code))
        except (TimeoutError, URLError):
            failures.append(host + ": erro de rede")
        except Exception as error:
            failures.append(host + ": " + str(error)[:90])
        source.unlink(missing_ok=True)
    raise RuntimeError("Invidious indisponível. " + "; ".join(failures))


def search_invidious(query: str):
    """Search YouTube's index through Invidious when the direct path fails."""
    for base in INVIDIOUS_INSTANCES:
        try:
            entries = invidious_json(base, "/api/v1/search?" + urlencode({
                "q": query, "type": "video", "region": "BR"
            }))
            if not isinstance(entries, list):
                continue
            items = []
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") != "video":
                    continue
                identifier = str(entry.get("videoId") or "")
                if not VIDEO_ID.fullmatch(identifier) or not entry.get("title"):
                    continue
                items.append({"id": identifier, "title": str(entry["title"])[:160],
                              "channel": str(entry.get("author") or ""),
                              "duration": entry.get("lengthSeconds") or 0,
                              "thumbnail": "/api/thumb/" + identifier})
                if len(items) >= 12:
                    break
            if items:
                return items
        except Exception as error:
            app.logger.warning("Busca Invidious falhou: %s", type(error).__name__)
    return []


def piped_json(base: str, path: str) -> dict:
    url = base + path
    if not approved_media_url(url):
        raise ValueError("Instância não autorizada")
    with PIPED_OPENER.open(Request(url, headers={"User-Agent": "RetroTube/1.0", "Accept": "application/json"}), timeout=9) as remote:
        if remote.headers.get("Content-Length") and int(remote.headers["Content-Length"]) > 2_000_000:
            raise ValueError("Resposta de API muito grande")
        data = remote.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError("Resposta de API muito grande")
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("Resposta inesperada")
    return result


def piped_video(identifier: str, work: Path) -> Path:
    """Download one public Piped progressive MP4 through its media proxy."""
    failures = []
    for base in PIPED_INSTANCES:
        name = urlsplit(base).hostname or "piped"
        try:
            data = piped_json(base, "/streams/" + identifier)
            duration = int(data.get("duration") or 0)
            if duration > MAX_DURATION or data.get("livestream"):
                raise ValueError("Vídeo ao vivo ou maior que o limite permitido")
            if not duration:
                failures.append(name + ": sem duração")
                continue
            proxy = urlsplit(str(data.get("proxyUrl") or "")).hostname
            formats = [s for s in (data.get("videoStreams") or [])
                       if isinstance(s, dict) and not s.get("videoOnly") and
                       str(s.get("mimeType") or "").startswith("video/mp4") and
                       0 < int(s.get("height") or 0) <= 480 and
                       approved_media_url(str(s.get("url") or "")) and
                       urlsplit(str(s.get("url"))).hostname == proxy]
            formats.sort(key=lambda item: int(item.get("height") or 0), reverse=True)
            if not formats:
                failures.append(name + ": sem MP4 com áudio")
                continue
            set_job(identifier, "alternativo", title=str(data.get("title") or "Vídeo")[:160])
            source = work / "piped-source.mp4"
            req = Request(formats[0]["url"], headers={"User-Agent": "RetroTube/1.0"})
            with PIPED_OPENER.open(req, timeout=20) as remote, source.open("wb") as target:
                total = 0
                started = time.monotonic()
                while True:
                    if time.monotonic() - started > 300:
                        raise TimeoutError("O provedor alternativo demorou demais")
                    chunk = remote.read(131072)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_SOURCE_BYTES:
                        raise ValueError("O arquivo excede 250 MB")
                    target.write(chunk)
            if source.stat().st_size > 0:
                return source
        except HTTPError as error:
            failures.append(name + ": HTTP " + str(error.code))
        except (TimeoutError, URLError):
            failures.append(name + ": erro de rede")
        except Exception as error:
            failures.append(name + ": " + type(error).__name__)
        (work / "piped-source.mp4").unlink(missing_ok=True)
    app.logger.warning("Piped falhou para %s: %s", identifier, "; ".join(failures))
    raise RuntimeError("YouTube e fontes alternativas indisponíveis. Detalhes: " + "; ".join(failures))


def valid_id(value: str) -> str:
    if not VIDEO_ID.fullmatch(value or ""):
        abort(400, "Identificador de vídeo inválido")
    return value


def json_error(message: str, status: int):
    return jsonify({"error": message}), status


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = response.headers.get("Cache-Control", "no-store")
    response.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


def check_rate_limit(action: str, max_requests: int, period: int = 60):
    # These basic per-IP limits reduce accidental server overload.
    # If behind a proxy, configure it to pass a trustworthy X-Real-IP.
    ip = request.remote_addr or "unknown"
    key = action + ":" + ip
    now = time.monotonic()
    with LOCK:
        recent = REQUESTS[key]
        while recent and recent[0] < now - period:
            recent.popleft()
        if len(recent) >= max_requests:
            abort(429, "Limite temporário de requisições. Tente depois.")
        recent.append(now)


def cached_path(identifier: str) -> Path:
    return VIDEO_DIR / (identifier + ".mp4")


def cache_cleanup():
    """Remove oldest completed files when the configured size is exceeded."""
    files = sorted(VIDEO_DIR.glob("*.mp4"), key=lambda p: p.stat().st_mtime)
    limit = int(MAX_CACHE_GB * 1024**3)
    total = sum(p.stat().st_size for p in files)
    for file in files:
        if total <= limit:
            break
        try:
            size = file.stat().st_size
            file.unlink()
            total -= size
        except OSError:
            pass


def set_job(identifier: str, status: str, **kwargs):
    with LOCK:
        previous = JOBS.get(identifier, {})
        JOBS[identifier] = {**previous, "status": status, "updated": time.time(), **kwargs}


def search_videos(query: str):
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp não está instalado no servidor")
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": True, "noplaylist": True, "ignoreerrors": True,
        "socket_timeout": 12
    }
    with YoutubeDL(opts) as ydl:
        result = ydl.extract_info("ytsearch12:" + query, download=False)
    items = []
    for item in (result or {}).get("entries", []):
        if item and VIDEO_ID.fullmatch(str(item.get("id", ""))):
            identifier = item["id"]
            items.append({
                "id": identifier,
                "title": item.get("title") or "Vídeo",
                "channel": item.get("channel") or item.get("uploader") or "",
                "duration": item.get("duration") or 0,
                "thumbnail": "/api/thumb/" + identifier
            })
    return items


def search_piped(query: str):
    """Search result fallback when yt-dlp YouTube search is unavailable."""
    for base in PIPED_INSTANCES:
        try:
            url = base + "/search?" + urlencode({"q": query, "filter": "videos"})
            with PIPED_OPENER.open(Request(url, headers={"User-Agent": "RetroTube/1.0"}), timeout=9) as remote:
                data = json.loads(remote.read(2_000_001))
            items = []
            for entry in (data.get("items", []) if isinstance(data, dict) else []):
                if not isinstance(entry, dict) or entry.get("type") not in ("stream", None):
                    continue
                match = re.search(r"[?&]v=([A-Za-z0-9_-]{11})(?:[&#]|$)", str(entry.get("url") or ""))
                if not match or not entry.get("title"):
                    continue
                identifier = match.group(1)
                items.append({"id": identifier, "title": str(entry["title"])[:160],
                              "channel": str(entry.get("uploaderName") or ""),
                              "duration": entry.get("duration") or 0,
                              "thumbnail": "/api/thumb/" + identifier})
                if len(items) >= 12:
                    break
            if items:
                return items
        except Exception:
            continue
    return []


def prepare_video(identifier: str):
    work = None
    try:
        cache_cleanup()
        work = Path(tempfile.mkdtemp(prefix=identifier + "-", dir=VIDEO_DIR))
        set_job(identifier, "obtendo")
        url = "https://www.youtube.com/watch?v=" + identifier
        opts = {
            "format": "bv*[height<=480]+ba/b[height<=480]/b",
            "outtmpl": str(work / "source.%(ext)s"),
            "merge_output_format": "mkv",
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "socket_timeout": 15, "retries": 2, "fragment_retries": 2,
            "max_filesize": 500 * 1024 * 1024
        }
        source = None
        if YoutubeDL is not None:
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    duration = int(info.get("duration") or 0)
                    if not duration or duration > MAX_DURATION or info.get("is_live"):
                        raise ValueError("O vídeo é ao vivo, não possui duração conhecida ou excede o limite permitido.")
                    set_job(identifier, "baixando", title=info.get("title", "Vídeo"))
                    ydl.download([url])
                candidates = [p for p in work.iterdir() if p.is_file() and p.suffix.lower() in (".mkv", ".mp4", ".webm", ".m4v")]
                if candidates:
                    source = max(candidates, key=lambda p: p.stat().st_size)
            except ValueError:
                raise
            except Exception:
                source = None
        if source is None:
            set_job(identifier, "alternativo")
            try:
                source = invidious_video(identifier, work)
            except RuntimeError as invidious_error:
                try:
                    source = piped_video(identifier, work)
                except RuntimeError as piped_error:
                    raise RuntimeError(str(invidious_error) + " | " + str(piped_error)) from piped_error
        set_job(identifier, "convertendo")
        output = work / "ready.mp4"
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            # iOS 8 hardware decoders are happier with 30 fps H.264 baseline.
            "-vf", "scale=-2:360,fps=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level", "3.0",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart", str(output)
        ]
        finished = subprocess.run(command, capture_output=True, text=True, timeout=2400)
        if finished.returncode or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("A conversão MP4 falhou: " + finished.stderr[-300:])
        os.replace(output, cached_path(identifier))
        set_job(identifier, "pronto")
    except Exception as error:
        # Keep the error short: do not expose subprocess commands or filesystem paths.
        set_job(identifier, "erro", message=str(error)[:350])
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


@app.get("/api/health")
def health():
    return jsonify({
        "ok": YoutubeDL is not None and shutil.which("ffmpeg") is not None,
        "yt_dlp": YoutubeDL is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None
    })


@app.get("/api/search")
def search():
    check_rate_limit("search", 25)
    query = (request.args.get("q") or "").strip()[:90]
    if len(query) < 2:
        return json_error("Digite pelo menos duas letras.", 400)
    try:
        items = search_videos(query)
    except Exception:
        items = []
    if not items:
        items = search_invidious(query)
    if not items:
        items = search_piped(query)
    if items:
        return jsonify({"items": items})
    return json_error("As fontes de busca estão temporariamente indisponíveis.", 503)


@app.get("/api/prepare/<identifier>")
def prepare(identifier):
    identifier = valid_id(identifier)
    if cached_path(identifier).exists():
        return jsonify({"status": "pronto", "video_url": "/api/video/" + identifier})
    check_rate_limit("prepare", 12)
    with LOCK:
        current = JOBS.get(identifier)
        if current and current["status"] not in ("erro", "pronto"):
            return jsonify({"status": current["status"]})
        active = sum(1 for item in JOBS.values() if item["status"] not in ("erro", "pronto"))
        if active >= MAX_QUEUE:
            return json_error("Servidor ocupado. Tente novamente em alguns minutos.", 429)
        JOBS[identifier] = {"status": "na_fila", "updated": time.time()}
        POOL.submit(prepare_video, identifier)
    return jsonify({"status": "na_fila"})


@app.get("/api/status/<identifier>")
def status(identifier):
    identifier = valid_id(identifier)
    if cached_path(identifier).exists():
        return jsonify({"status": "pronto", "video_url": "/api/video/" + identifier})
    with LOCK:
        job = JOBS.get(identifier)
    if not job:
        return json_error("Vídeo não iniciado", 404)
    return jsonify({key: value for key, value in job.items() if key in ("status", "message", "title")})


@app.get("/api/video/<identifier>")
def video(identifier):
    path = cached_path(valid_id(identifier))
    if not path.is_file():
        return json_error("Vídeo ainda não está pronto", 404)
    # Flask/Werkzeug handles Range/206 for iOS Safari's native MP4 player.
    return send_file(path, mimetype="video/mp4", conditional=True, max_age=86400)


@app.get("/api/demo.mp4")
def demo_video():
    """Tiny local H.264/AAC clip to test Safari without contacting YouTube."""
    path = VIDEO_DIR / "retrotube-demo.mp4"
    with DEMO_LOCK:
        if not path.is_file() or path.stat().st_size == 0:
            if not shutil.which("ffmpeg"):
                return json_error("FFmpeg indisponível na VPS.", 503)
            work = VIDEO_DIR / "retrotube-demo-building.mp4"
            command = [
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                "-t", "8", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level", "3.0",
                "-c:a", "aac", "-b:a", "64k", "-ac", "1",
                "-movflags", "+faststart", str(work)
            ]
            try:
                result = subprocess.run(command, capture_output=True, timeout=60)
                if result.returncode != 0 or not work.exists() or work.stat().st_size == 0:
                    return json_error("Não foi possível gerar o vídeo de teste.", 503)
                os.replace(work, path)
            except (OSError, subprocess.TimeoutExpired):
                return json_error("Falha ao gerar o vídeo de teste.", 503)
            finally:
                work.unlink(missing_ok=True)
    return send_file(path, mimetype="video/mp4", conditional=True, max_age=3600)


@app.get("/api/thumb/<identifier>")
def thumbnail(identifier):
    identifier = valid_id(identifier)
    check_rate_limit("thumb", 120)
    try:
        url = "https://i.ytimg.com/vi/" + identifier + "/mqdefault.jpg"
        with urlopen(Request(url, headers={"User-Agent": "RetroTube/1.0"}), timeout=8) as remote:
            data = remote.read(100_000)
        return app.response_class(data, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})
    except Exception:
        abort(404)


@app.get("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.get("/<path:filename>")
def frontend_file(filename):
    if filename not in ("app.js", "config.js", "logo.png", "logo.svg"):
        abort(404)
    return send_from_directory(FRONTEND_DIR, filename)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765)
