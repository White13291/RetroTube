FROM python:3.12-slim
COPY --from=denoland/deno:bin-2.9.6 /deno /usr/local/bin/deno
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app.py .
COPY frontend/ /frontend/
RUN mkdir -p /data && useradd -r -u 10001 video && chown -R video:video /data /app
USER video
EXPOSE 8765
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8765", "--timeout", "120", "app:app"]
