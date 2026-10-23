"""RetroTube video service. Run behind HTTPS with Gunicorn/Caddy on a VPS."""
from __future__ import annotations

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
from urllib.request import Request, urlopen

from flask import Flask, abort, jsonify, request, send_file

try:
    from yt_dlp import YoutubeDL
except ImportError:
    YoutubeDL = None

app = Flask(__name__)
VIDEO_DIR = Path(os.getenv("RETROTUBE_CACHE_DIR", "/tmp/retrotube-cache")).resolve()
VIDEO_DIR.mkdir(parents=True, exist_ok=True)
MAX_DURATION = int(os.getenv("RETROTUBE_MAX_DURATION", "1800"))
MAX_ACTIVE_JOBS = int(os.getenv("RETROTUBE_MAX_ACTIVE_JOBS", "2"))
MAX_QUEUE = int(os.getenv("RETROTUBE_MAX_QUEUE", "8"))
MAX_CACHE_GB = float(os.getenv("RETROTUBE_MAX_CACHE_GB", "3"))
ALLOWED_ORIGIN = os.getenv("RETROTUBE_FRONTEND_ORIGIN", "https://white13291.github.io").rstrip("/")
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
POOL = ThreadPoolExecutor(max_workers=MAX_ACTIVE_JOBS)
JOBS: dict[str, dict] = {}
LOCK = threading.RLock()
REQUESTS: dict[str, deque] = defaultdict(deque)


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


def prepare_video(identifier: str):
    work = None
    try:
        if YoutubeDL is None:
            raise RuntimeError("yt-dlp não está instalado")
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
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = int(info.get("duration") or 0)
            if not duration or duration > MAX_DURATION or info.get("is_live"):
                raise ValueError("O vídeo é ao vivo, não possui duração conhecida ou excede o limite permitido.")
            set_job(identifier, "baixando", title=info.get("title", "Vídeo"))
            ydl.download([url])
        candidates = [p for p in work.iterdir() if p.is_file() and p.suffix.lower() in (".mkv", ".mp4", ".webm", ".m4v")]
        if not candidates:
            raise RuntimeError("O YouTube não forneceu um arquivo reproduzível.")
        source = max(candidates, key=lambda p: p.stat().st_size)
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
        set_job(identifier, "erro", message=str(error)[:180])
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
        return jsonify({"items": search_videos(query)})
    except Exception:
        return json_error("A busca no YouTube está temporariamente indisponível.", 503)


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
    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>RetroTube API</title></head>"
        "<body><h1>RetroTube</h1><p>Servidor de vídeos ativo. Use a interface do GitHub Pages.</p>"
        "<p><a href='/api/health'>Verificar estado do servidor</a></p></body></html>"
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8765)
