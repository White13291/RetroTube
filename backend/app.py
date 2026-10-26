"""RetroTube video service. Run behind HTTPS with Gunicorn/Caddy on a VPS."""
from __future__ import annotations

import json
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlencode, urlsplit
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
DAILYMOTION_ID = re.compile(r"^dailymotion_([A-Za-z0-9]{5,14})$")
PEERTUBE_ID = re.compile(r"^peertube_([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})$")
PEERTUBE_SEARCH = ("https://search.joinpeertube.org", "https://peertube.tv")
PEERTUBE_INDEX = VIDEO_DIR / "peertube-index.json"
VIDEO_QUALITIES = (360, 480, 720, 1080)
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
QUALITY_MEMORY: dict[str, tuple[float, list[int]]] = {}
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


def peertube_public_url(url: str) -> bool:
    """Never let search results direct the server at local hosts or IP literals."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        return bool(parts.scheme == "https" and "." in host and
                    re.fullmatch(r"[a-z0-9.-]{4,253}", host) and
                    not any(host == suffix or host.endswith("." + suffix) for suffix in
                            ("localhost", "local", "internal", "test", "invalid")) and
                    not parts.username and not parts.password and parts.port in (None, 443) and
                    not host.startswith("-") and not host.endswith("-"))
    except (TypeError, ValueError):
        return False


def peertube_public_network(url: str) -> bool:
    """Reject hosts resolving to loopback, private, link-local or reserved IPs."""
    if not peertube_public_url(url):
        return False
    host = urlsplit(url).hostname
    try:
        if ipaddress.ip_address(host):
            return False  # Only domain names are accepted.
    except ValueError:
        pass
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(entry[4][0]).is_global for entry in addresses)
    except (OSError, ValueError):
        return False


class PublicPeerRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not peertube_public_network(newurl):
            raise ValueError("Redirecionamento de vídeo não autorizado")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


PEERTUBE_OPENER = build_opener(PublicPeerRedirects())


def peertube_json(url: str, timeout: int = 15):
    if not peertube_public_network(url):
        raise ValueError("Servidor PeerTube não autorizado")
    req = Request(url, headers={"User-Agent": "RetroTube/1.0", "Accept": "application/json"})
    with PEERTUBE_OPENER.open(req, timeout=timeout) as remote:
        length = remote.headers.get("Content-Length")
        if length and int(length) > 2_500_000:
            raise ValueError("Resposta PeerTube muito grande")
        data = remote.read(2_500_001)
    if len(data) > 2_500_000:
        raise ValueError("Resposta PeerTube muito grande")
    return json.loads(data)


def remember_peertube_videos(videos: dict[str, dict | str]):
    """Persist indexed video origins and thumbnails across container restarts."""
    if not videos:
        return
    with LOCK:
        try:
            entries = json.loads(PEERTUBE_INDEX.read_text(encoding="utf-8"))
            if not isinstance(entries, dict):
                entries = {}
        except (OSError, ValueError):
            entries = {}
        entries.update(videos)
        entries = dict(list(entries.items())[-500:])
        temporary = PEERTUBE_INDEX.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, PEERTUBE_INDEX)


def peertube_link(identifier: str) -> str:
    if not PEERTUBE_ID.fullmatch(identifier):
        raise ValueError("Vídeo PeerTube inválido")
    with LOCK:
        try:
            entries = json.loads(PEERTUBE_INDEX.read_text(encoding="utf-8"))
            entry = entries.get(identifier, "") if isinstance(entries, dict) else ""
            link = entry.get("url", "") if isinstance(entry, dict) else entry
        except (OSError, ValueError):
            link = ""
    if not link:
        raise ValueError("Vídeo não localizado. Pesquise novamente no RetroTube.")
    if not peertube_public_url(link):
        raise ValueError("Endereço do vídeo não autorizado")
    path = urlsplit(link).path
    if not path.endswith("/" + identifier[len("peertube_"):]):
        raise ValueError("Endereço PeerTube inconsistente")
    return link


def peertube_thumbnail_link(identifier: str) -> str:
    if not PEERTUBE_ID.fullmatch(identifier):
        return ""
    with LOCK:
        try:
            entries = json.loads(PEERTUBE_INDEX.read_text(encoding="utf-8"))
            entry = entries.get(identifier, {}) if isinstance(entries, dict) else {}
            link = str(entry.get("thumbnail") or "") if isinstance(entry, dict) else ""
        except (OSError, ValueError):
            link = ""
    return link if peertube_public_url(link) else ""


def peertube_cached_sources(identifier: str):
    """Use recently verified media URLs, avoiding a second slow origin API call."""
    with LOCK:
        try:
            entries = json.loads(PEERTUBE_INDEX.read_text(encoding="utf-8"))
            entry = entries.get(identifier, {}) if isinstance(entries, dict) else {}
        except (OSError, ValueError):
            return None
    if (not isinstance(entry, dict) or entry.get("media_checked") is not True or
            time.time() - float(entry.get("checked", 0) or 0) > 1200):
        return None
    sources = []
    for item in (entry.get("sources") or [])[:5]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not peertube_public_url(url):
            continue
        try:
            height = int(item.get("height") or 0)
        except (ValueError, TypeError):
            continue
        if 0 < height <= 1080:
            sources.append((height, url))
    if not sources:
        return None
    return {"choices": sources, "title": str(entry.get("name") or "Vídeo")[:160]}


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


def invidious_video(identifier: str, work: Path, quality: int = 360) -> Path:
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
                    source_height = int(re.search(r"\d+", str(item.get("qualityLabel") or "360p")).group())
                except (TypeError, ValueError, AttributeError):
                    continue
                if 0 < itag < 1000 and (0 < source_height <= 480 if quality == 360 else
                                      quality <= source_height <= 1080) and (
                    "mp4" in str(item.get("type") or "").lower() or
                    str(item.get("container") or "").lower() == "mp4"
                ):
                    choices.append((source_height, itag))
            if not choices:
                raise ValueError("Sem MP4 progressivo com áudio nesta resolução")
            choices.sort(key=lambda item: abs(item[0] - quality))
            set_job(identifier, "alternativo", quality=quality, title=str(data.get("title") or "Vídeo")[:160])
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


def piped_video(identifier: str, work: Path, quality: int = 360) -> Path:
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
                       (0 < int(s.get("height") or 0) <= 480 if quality == 360 else
                        quality <= int(s.get("height") or 0) <= 1080) and
                       approved_media_url(str(s.get("url") or "")) and
                       urlsplit(str(s.get("url"))).hostname == proxy]
            formats.sort(key=lambda item: abs(int(item.get("height") or 0) - quality))
            if not formats:
                failures.append(name + ": sem MP4 com áudio")
                continue
            set_job(identifier, "alternativo", quality=quality, title=str(data.get("title") or "Vídeo")[:160])
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
    if not (VIDEO_ID.fullmatch(value or "") or DAILYMOTION_ID.fullmatch(value or "") or
            PEERTUBE_ID.fullmatch(value or "")):
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


def quality_path(identifier: str, quality: int) -> Path:
    # The original 360p filename remains valid for previously cached videos.
    return cached_path(identifier) if quality == 360 else VIDEO_DIR / f"{identifier}-{quality}p.mp4"


def quality_job(identifier: str, quality: int) -> str:
    return f"{identifier}:{quality}"


def requested_quality() -> int:
    raw = request.args.get("quality", "360")
    if raw not in ("360", "480", "720", "1080"):
        abort(400, "Qualidade inválida")
    return int(raw)


def quality_video_url(identifier: str, quality: int) -> str:
    return "/api/video/" + identifier + "?quality=" + str(quality)


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


def set_job(identifier: str, status: str, quality: int = 360, **kwargs):
    with LOCK:
        key = quality_job(identifier, quality)
        previous = JOBS.get(key, {})
        JOBS[key] = {**previous, "status": status, "updated": time.time(), **kwargs}


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


def search_dailymotion(query: str):
    """Use yt-dlp's Dailymotion search extractor; no user API key required."""
    if YoutubeDL is None:
        raise RuntimeError("yt-dlp não está instalado no servidor")
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": False, "ignoreerrors": True,
        "playlistend": 8, "socket_timeout": 12,
        "retries": 1, "geo_bypass_country": "BR",
    }
    search_url = "https://www.dailymotion.com/search/" + quote(query, safe="") + "/videos"
    with YoutubeDL(opts) as ydl:
        result = ydl.extract_info(search_url, download=False)
    items = []
    for item in (result or {}).get("entries") or []:
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get("id") or "")
        if not DAILYMOTION_ID.fullmatch("dailymotion_" + raw_id):
            continue
        if item.get("is_live") or item.get("availability") in ("private", "premium_only", "needs_auth"):
            continue
        items.append({
            "id": "dailymotion_" + raw_id,
            "title": str(item.get("title") or "Vídeo do Dailymotion")[:160],
            "channel": str(item.get("uploader") or item.get("channel") or "Dailymotion")[:100],
            "duration": item.get("duration") or 0,
            "thumbnail": str(item.get("thumbnail") or ""),
        })
    return items


def peertube_file_choices(details: dict):
    """Return reasonably sized public PeerTube MP4/WebM sources."""
    choices = []
    video_lists = [details.get("files") or []]
    for playlist in details.get("streamingPlaylists") or []:
        if isinstance(playlist, dict):
            video_lists.append(playlist.get("files") or [])
    for files in video_lists:
        for entry in files:
            if not isinstance(entry, dict):
                continue
            try:
                height = int((entry.get("resolution") or {}).get("id") or 0)
            except (ValueError, TypeError, AttributeError):
                continue
            if not 0 < height <= 1080:
                continue
            try:
                size = int(entry.get("size") or 0)
            except (ValueError, TypeError):
                size = 0
            if size > MAX_SOURCE_BYTES:
                continue
            for field in ("fileUrl", "fileDownloadUrl"):
                file_url = str(entry.get(field) or "")
                if not peertube_public_url(file_url):
                    continue
                file_path = urlsplit(file_url).path.lower()
                if field == "fileUrl" and not (file_path.endswith(".mp4") or file_path.endswith(".webm")):
                    continue
                if field == "fileDownloadUrl" and not (
                    file_path.endswith(".mp4") or file_path.endswith(".webm") or "/download/videos/" in file_path
                ):
                    continue
                if (height, file_url) not in choices:
                    choices.append((height, file_url))
    choices.sort(key=lambda choice: (choice[0] > 480, abs(choice[0] - 360)))
    # Preserve higher sizes even when many low-res URLs fill the first page.
    selected = choices[:24]
    for target in (720, 1080):
        for source in (choice for choice in choices if choice[0] >= target):
            if source not in selected:
                selected.append(source)
            if sum(1 for height, _ in selected if height >= target) >= 3:
                break
    return selected[:30]


def peertube_media_alive(url: str, timeout: int = 5) -> bool:
    """Try a tiny HTTP range before advertising a video; 404 means unusable."""
    if not peertube_public_network(url):
        return False
    try:
        req = Request(url, headers={"User-Agent": "RetroTube/1.0",
                                    "Accept": "video/*,application/octet-stream",
                                    "Range": "bytes=0-1023"})
        with PEERTUBE_OPENER.open(req, timeout=timeout) as remote:
            mime = (remote.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if not (mime.startswith("video/") or mime in ("application/octet-stream", "binary/octet-stream")):
                return False
            # A partial (206) response can advertise the full size in Content-Range.
            content_range = str(remote.headers.get("Content-Range") or "")
            full_size = re.search(r"/(\d+)$", content_range)
            if full_size and int(full_size.group(1)) > MAX_SOURCE_BYTES:
                return False
            length = remote.headers.get("Content-Length")
            if length and int(length) > MAX_SOURCE_BYTES:
                return False
            return bool(remote.read(32))
    except (OSError, ValueError, URLError, HTTPError, TimeoutError):
        return False


def peertube_check_search_item(item: dict):
    """Quickly reject dead PeerTube origins before offering videos to the iPad."""
    uid = item["id"][len("peertube_"):]
    origin = "https://" + urlsplit(item["original_url"]).hostname
    try:
        details = peertube_json(origin + "/api/v1/videos/" + uid, timeout=6)
        if not isinstance(details, dict) or str(details.get("uuid") or "").lower() != uid:
            return None
        if details.get("isLive") or int(details.get("duration") or 0) not in range(1, MAX_DURATION + 1):
            return None
        choices = peertube_file_choices(details)
        if not choices:
            return None
        # A metadata response can advertise files that have already vanished.
        # Check actual bytes before showing the video in the search results.
        working = []
        for height, url in choices[:3]:
            if peertube_media_alive(url):
                working.append((height, url))
                break  # One verified source is enough; keep search responsive.
        if not working:
            return None
        return {"sources": [{"height": h, "url": u} for h, u in working],
                "candidates": [{"height": h, "url": u} for h, u in choices],
                "name": str(details.get("name") or item["title"])[:160],
                "checked": time.time(), "media_checked": True}
    except Exception:
        return None


def search_peertube(query: str):
    """Search PeerTube without hiding results that lack particular resolutions."""
    failures = []
    for base in PEERTUBE_SEARCH:
        try:
            response = peertube_json(base + "/api/v1/search/videos?" + urlencode({
                "search": query, "count": 15, "start": 0
            }))
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                raise ValueError("Resposta de busca inválida")
            items = []
            links = {}
            for entry in response["data"]:
                if not isinstance(entry, dict):
                    continue
                uid = str(entry.get("uuid") or "").lower()
                identifier = "peertube_" + uid
                if not PEERTUBE_ID.fullmatch(identifier) or not entry.get("name"):
                    continue
                if entry.get("isLive") or entry.get("nsfw"):
                    continue
                try:
                    duration = int(entry.get("duration") or 0)
                except (ValueError, TypeError):
                    continue
                if not 0 < duration <= MAX_DURATION:
                    continue
                original = str(entry.get("url") or "")
                if not peertube_public_url(original):
                    continue
                parts = urlsplit(original)
                if not (parts.path.endswith("/" + uid) or parts.path.endswith("/" + str(entry.get("shortUUID") or "***"))):
                    continue
                # Store a canonical origin video URL; never accept a client-supplied hostname.
                canonical = "https://" + parts.hostname + "/videos/watch/" + uid
                if not peertube_public_network(canonical):
                    continue
                thumbnail = str(entry.get("thumbnailUrl") or "")
                if not peertube_public_url(thumbnail):
                    thumb_path = str(entry.get("thumbnailPath") or "")
                    thumbnail = (base + thumb_path) if thumb_path.startswith("/") else ""
                account = entry.get("channel") or entry.get("account") or {}
                if not isinstance(account, dict):
                    account = {}
                items.append({
                    "id": identifier,
                    "title": str(entry["name"])[:160],
                    "channel": str(account.get("displayName") or account.get("name") or "PeerTube")[:100],
                    "duration": duration,
                    "thumbnail": "/api/thumb/" + identifier,
                    "original_url": canonical,
                })
                links[identifier] = {"url": canonical, "thumbnail": thumbnail}
                if len(items) >= 16:
                    break
            if items:
                # Origin health and available resolutions are independent from
                # search visibility. Prioritize verified media, but preserve the
                # other valid catalog entries so 240p-only videos do not vanish.
                with ThreadPoolExecutor(max_workers=8) as workers:
                    checks = list(workers.map(peertube_check_search_item, items))
                verified = []
                unchecked = []
                catalog_links = {}
                for item, check in zip(items, checks):
                    identifier = item["id"]
                    if check:
                        verified.append(item)
                        catalog_links[identifier] = {**links[identifier], **check}
                    else:
                        unchecked.append(item)
                        catalog_links[identifier] = links[identifier]
                remember_peertube_videos(catalog_links)
                return verified + unchecked
            failures.append(urlsplit(base).hostname + ": sem vídeos no índice")
        except Exception as error:
            app.logger.warning("Busca PeerTube %s falhou: %s", base, str(error)[:150])
            failures.append(urlsplit(base).hostname + ": indisponível")
    raise RuntimeError("PeerTube não retornou resultados. " + "; ".join(failures))


def peertube_video_details(identifier: str) -> dict:
    """Retrieve and validate origin metadata, with the existing federated fallback."""
    uid = PEERTUBE_ID.fullmatch(identifier).group(1)
    original = peertube_link(identifier)
    origin = "https://" + urlsplit(original).hostname
    details = None
    failures = []
    metadata_origins = [origin]
    if origin != "https://peertube.tv":
        metadata_origins.append("https://peertube.tv")
    for server in metadata_origins:
        try:
            candidate = peertube_json(server + "/api/v1/videos/" + uid, timeout=22)
            if isinstance(candidate, dict) and str(candidate.get("uuid") or "").lower() == uid:
                details = candidate
                break
            failures.append(urlsplit(server).hostname + ": vídeo não encontrado")
        except (OSError, HTTPError, URLError, ValueError, TimeoutError) as error:
            message = "tempo esgotado" if isinstance(error, (TimeoutError, socket.timeout)) or "timed out" in str(error).lower() else type(error).__name__
            failures.append(urlsplit(server).hostname + ": " + message)
    if details is None:
        raise RuntimeError("Origem PeerTube sem resposta. " + "; ".join(failures) + ". Tente outro vídeo.")
    duration = int(details.get("duration") or 0)
    if not 0 < duration <= MAX_DURATION or details.get("isLive"):
        raise ValueError("Vídeo sem duração conhecida, ao vivo ou longo demais")
    return details


def peertube_available_qualities(identifier: str) -> list[int]:
    """Verify a real, reachable media source for each advertised output size."""
    with LOCK:
        try:
            entries = json.loads(PEERTUBE_INDEX.read_text(encoding="utf-8"))
            entry = entries.get(identifier, {}) if isinstance(entries, dict) else {}
        except (OSError, ValueError):
            entry = {}
    now = time.time()
    if (isinstance(entry, dict) and now - float(entry.get("qualities_checked") or 0) <
            (900 if entry.get("qualities") else 90) and
            isinstance(entry.get("qualities"), list)):
        return [q for q in VIDEO_QUALITIES if q in entry["qualities"]]
    choices = []
    if (isinstance(entry, dict) and now - float(entry.get("checked") or 0) < 1200):
        for source in entry.get("candidates") or []:
            if isinstance(source, dict) and peertube_public_url(str(source.get("url") or "")):
                try:
                    height = int(source.get("height") or 0)
                except (ValueError, TypeError):
                    continue
                if 0 < height <= 1080:
                    choices.append((height, source["url"]))
    if not choices:
        choices = peertube_file_choices(peertube_video_details(identifier))
    cached = peertube_cached_sources(identifier)
    verified = list(cached["choices"]) if cached else []
    checked_urls = {url for _, url in verified}
    for quality in VIDEO_QUALITIES:
        if any(height >= quality for height, _ in verified):
            continue
        eligible = sorted((h, url) for h, url in choices if h >= quality and url not in checked_urls)
        for height, url in eligible[:3]:
            checked_urls.add(url)
            if peertube_media_alive(url):
                verified.append((height, url))
                break
    available = [q for q in VIDEO_QUALITIES if any(h >= q for h, _ in verified)]
    # Older indexes stored only the original URL as a string. Keep that link
    # when upgrading the cache; it is required for later downloads.
    entry = entry if isinstance(entry, dict) else {"url": entry} if isinstance(entry, str) else {}
    entry.update({"sources": [{"height": h, "url": u} for h, u in verified],
                  "candidates": [{"height": h, "url": u} for h, u in choices],
                  "checked": now, "media_checked": bool(verified),
                  "qualities": available, "qualities_checked": now})
    remember_peertube_videos({identifier: entry})
    return available


def peertube_video(identifier: str, work: Path, quality: int = 360) -> Path:
    """Download a source capable of the requested resolution, without upscaling."""
    cached = peertube_cached_sources(identifier)
    choices = cached["choices"] if cached else []
    if not choices or (quality > 360 and not any(h >= quality for h, _ in choices)):
        details = peertube_video_details(identifier)
        choices = peertube_file_choices(details)
        video_title = str(details.get("name") or "Vídeo")[:160]
    else:
        video_title = cached["title"]
    if quality > 360:
        choices = [(h, url) for h, url in choices if h >= quality]
        choices.sort(key=lambda item: item[0] - quality)
    if not choices:
        raise ValueError("Este vídeo PeerTube não oferece fonte pública nesta resolução")
    errors = []
    # Retry the smallest preferred source once after trying alternatives.
    for _, url in (choices[:5] + choices[:1]):
        source = work / ("peertube-source" + (".webm" if urlsplit(url).path.lower().endswith(".webm") else ".mp4"))
        try:
            if not peertube_public_network(url):
                raise ValueError("Servidor de vídeo não autorizado")
            set_job(identifier, "baixando", quality=quality, title=video_title)
            req = Request(url, headers={"User-Agent": "RetroTube/1.0", "Accept": "video/*"})
            with PEERTUBE_OPENER.open(req, timeout=35) as remote:
                content_type = (remote.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                    raise ValueError("Fonte não retornou um arquivo de vídeo")
                length = remote.headers.get("Content-Length")
                if length and int(length) > MAX_SOURCE_BYTES:
                    raise ValueError("Vídeo excede 250 MB")
                transferred = 0
                started = time.monotonic()
                with source.open("wb") as target:
                    while True:
                        if time.monotonic() - started > 300:
                            raise TimeoutError("Transferência muito lenta")
                        block = remote.read(131072)
                        if not block:
                            break
                        transferred += len(block)
                        if transferred > MAX_SOURCE_BYTES:
                            raise ValueError("Vídeo excede 250 MB")
                        target.write(block)
            if source.stat().st_size > 0:
                return source
        except Exception as error:
            errors.append(str(error)[:90])
            app.logger.warning("Fonte PeerTube rejeitada: %s", type(error).__name__)
            source.unlink(missing_ok=True)
    raise RuntimeError("Não foi possível obter um MP4 deste PeerTube: " + "; ".join(errors)[:200])


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


def prepare_video(identifier: str, quality: int = 360):
    work = None
    try:
        cache_cleanup()
        work = Path(tempfile.mkdtemp(prefix=identifier + "-", dir=VIDEO_DIR))
        set_job(identifier, "obtendo", quality=quality)
        peer_match = PEERTUBE_ID.fullmatch(identifier)
        dm_match = DAILYMOTION_ID.fullmatch(identifier)
        url = ("https://www.dailymotion.com/video/" + dm_match.group(1)
               if dm_match else "https://www.youtube.com/watch?v=" + identifier)
        opts = {
            "format": ("bv*[height<=480]+ba/b[height<=480]/b" if quality == 360 else
                       f"bv*[height={quality}]+ba/b[height={quality}]/"
                       f"bv*[height>={quality}][height<=1080]+ba/b[height>={quality}][height<=1080]"),
            "outtmpl": str(work / "source.%(ext)s"),
            "merge_output_format": "mkv",
            "noplaylist": True, "quiet": True, "no_warnings": True,
            "socket_timeout": 15, "retries": 2, "fragment_retries": 2,
            "max_filesize": 500 * 1024 * 1024
        }
        source = None
        if peer_match:
            source = peertube_video(identifier, work, quality=quality)
        elif YoutubeDL is not None:
            try:
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    duration = int(info.get("duration") or 0)
                    if not duration or duration > MAX_DURATION or info.get("is_live"):
                        raise ValueError("O vídeo é ao vivo, não possui duração conhecida ou excede o limite permitido.")
                    set_job(identifier, "baixando", quality=quality, title=info.get("title", "Vídeo"))
                    ydl.download([url])
                candidates = [p for p in work.iterdir() if p.is_file() and p.suffix.lower() in (".mkv", ".mp4", ".webm", ".m4v")]
                if candidates:
                    source = max(candidates, key=lambda p: p.stat().st_size)
            except ValueError:
                raise
            except Exception as error:
                app.logger.warning("Extração %s falhou: %s", "Dailymotion" if dm_match else "YouTube", type(error).__name__)
                source = None
        if source is None:
            if dm_match:
                raise RuntimeError("Dailymotion não forneceu um vídeo reproduzível à VPS. Tente outro vídeo.")
            set_job(identifier, "alternativo", quality=quality)
            try:
                source = invidious_video(identifier, work, quality=quality)
            except RuntimeError as invidious_error:
                try:
                    source = piped_video(identifier, work, quality=quality)
                except RuntimeError as piped_error:
                    raise RuntimeError(str(invidious_error) + " | " + str(piped_error)) from piped_error
        set_job(identifier, "convertendo", quality=quality)
        output = work / "ready.mp4"
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            # iOS 8 hardware decoders are happier with 30 fps H.264 baseline.
            "-vf", f"scale=-2:{quality},fps=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
            "-pix_fmt", "yuv420p", "-profile:v", "baseline", "-level",
            "4.0" if quality == 1080 else "3.1" if quality == 720 else "3.0",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-movflags", "+faststart", str(output)
        ]
        finished = subprocess.run(command, capture_output=True, text=True, timeout=2400)
        if finished.returncode or not output.exists() or output.stat().st_size == 0:
            raise RuntimeError("A conversão MP4 falhou: " + finished.stderr[-300:])
        os.replace(output, quality_path(identifier, quality))
        set_job(identifier, "pronto", quality=quality)
    except Exception as error:
        # Keep the error short: do not expose subprocess commands or filesystem paths.
        app.logger.warning("Erro ao preparar %s em %sp: %s", identifier, quality, str(error)[:350])
        set_job(identifier, "erro", quality=quality, message=str(error)[:350])
    finally:
        if work:
            shutil.rmtree(work, ignore_errors=True)


def available_video_qualities(identifier: str) -> list[int]:
    """Show a size only when the source has at least that many pixels vertically."""
    now = time.time()
    with LOCK:
        saved = QUALITY_MEMORY.get(identifier)
        if saved and now - saved[0] < (600 if saved[1] else 90):
            available = list(saved[1])
        else:
            available = None
    if available is None:
        try:
            if PEERTUBE_ID.fullmatch(identifier):
                available = peertube_available_qualities(identifier)
            else:
                if YoutubeDL is None:
                    raise RuntimeError("Extrator indisponível")
                raw_id = DAILYMOTION_ID.fullmatch(identifier)
                url = ("https://www.dailymotion.com/video/" + raw_id.group(1)
                       if raw_id else "https://www.youtube.com/watch?v=" + identifier)
                opts = {"quiet": True, "no_warnings": True, "skip_download": True,
                        "noplaylist": True, "socket_timeout": 12, "retries": 1}
                with YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                duration = int(info.get("duration") or 0)
                if duration <= 0 or duration > MAX_DURATION or info.get("is_live"):
                    raise ValueError("Duração de vídeo inválida")
                heights = []
                has_audio = False
                for fmt in info.get("formats") or []:
                    if not isinstance(fmt, dict) or not fmt.get("url"):
                        continue
                    if fmt.get("acodec") not in (None, "none"):
                        has_audio = True
                    if fmt.get("vcodec") == "none":
                        continue
                    try:
                        height = int(fmt.get("height") or 0)
                    except (TypeError, ValueError):
                        continue
                    if 0 < height <= 1080:
                        heights.append(height)
                available = ([quality for quality in VIDEO_QUALITIES
                              if any(height >= quality for height in heights)] if has_audio else [])
        except Exception as error:
            app.logger.warning("Resoluções não verificadas para %s: %s", identifier, type(error).__name__)
            available = []
        with LOCK:
            if len(QUALITY_MEMORY) >= 256:
                QUALITY_MEMORY.clear()
            QUALITY_MEMORY[identifier] = (now, list(available))
    # Previously converted files are also genuinely available, even offline.
    return [quality for quality in VIDEO_QUALITIES
            if quality in available or quality_path(identifier, quality).is_file()]


@app.get("/api/health")
def health():
    return jsonify({
        "ok": YoutubeDL is not None and shutil.which("ffmpeg") is not None,
        "yt_dlp": YoutubeDL is not None,
        "ffmpeg": shutil.which("ffmpeg") is not None
    })


@app.get("/api/qualities/<identifier>")
def qualities(identifier):
    identifier = valid_id(identifier)
    check_rate_limit("qualities", 20)
    return jsonify({"qualities": available_video_qualities(identifier)})


@app.get("/api/search")
def search():
    check_rate_limit("search", 25)
    query = (request.args.get("q") or "").strip()[:90]
    if len(query) < 2:
        return json_error("Digite pelo menos duas letras.", 400)
    source = (request.args.get("source") or "peertube").lower()
    if source not in ("youtube", "dailymotion", "peertube"):
        return json_error("Fonte de vídeo inválida.", 400)
    if source == "peertube":
        try:
            return jsonify({"items": search_peertube(query)})
        except Exception as error:
            app.logger.warning("Busca PeerTube falhou: %s", type(error).__name__)
            return json_error(str(error)[:280], 503)
    if source == "dailymotion":
        try:
            items = search_dailymotion(query)
        except Exception as error:
            app.logger.warning("Busca Dailymotion falhou: %s", type(error).__name__)
            items = []
        if items:
            return jsonify({"items": items})
        return json_error("Dailymotion não retornou resultados. Tente outra pesquisa.", 503)
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
    quality = requested_quality()
    if quality_path(identifier, quality).is_file():
        return jsonify({"status": "pronto", "video_url": quality_video_url(identifier, quality)})
    check_rate_limit("prepare", 12)
    with LOCK:
        key = quality_job(identifier, quality)
        current = JOBS.get(key)
        if current and current["status"] not in ("erro", "pronto"):
            return jsonify({"status": current["status"]})
        active = sum(1 for item in JOBS.values() if item["status"] not in ("erro", "pronto"))
        if active >= MAX_QUEUE:
            return json_error("Servidor ocupado. Tente novamente em alguns minutos.", 429)
        JOBS[key] = {"status": "na_fila", "updated": time.time()}
        POOL.submit(prepare_video, identifier, quality)
    return jsonify({"status": "na_fila"})


@app.get("/api/status/<identifier>")
def status(identifier):
    identifier = valid_id(identifier)
    quality = requested_quality()
    if quality_path(identifier, quality).is_file():
        return jsonify({"status": "pronto", "video_url": quality_video_url(identifier, quality)})
    with LOCK:
        job = JOBS.get(quality_job(identifier, quality))
    if not job:
        return json_error("Vídeo não iniciado", 404)
    return jsonify({key: value for key, value in job.items() if key in ("status", "message", "title")})


@app.get("/api/video/<identifier>")
def video(identifier):
    path = quality_path(valid_id(identifier), requested_quality())
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
    if DAILYMOTION_ID.fullmatch(identifier):
        abort(404)
    check_rate_limit("thumb", 120)
    if PEERTUBE_ID.fullmatch(identifier):
        photo = peertube_thumbnail_link(identifier)
        if photo and peertube_public_network(photo):
            try:
                with PEERTUBE_OPENER.open(Request(photo, headers={"User-Agent": "RetroTube/1.0"}), timeout=12) as remote:
                    mime = (remote.headers.get("Content-Type") or "").split(";")[0].lower().strip()
                    if mime not in ("image/jpeg", "image/png", "image/webp"):
                        raise ValueError("Resposta não é imagem")
                    raw = remote.read(500_001)
                if 0 < len(raw) <= 500_000:
                    return app.response_class(raw, mimetype=mime,
                                              headers={"Cache-Control": "public, max-age=1800"})
            except Exception as error:
                app.logger.warning("Capa PeerTube indisponível: %s", type(error).__name__)
        return send_from_directory(FRONTEND_DIR, "logo.png")
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
