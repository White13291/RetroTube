FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
RUN mkdir -p /data && useradd -r -u 10001 video && chown -R video:video /data /app
USER video
EXPOSE 8765
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8765", "--timeout", "120", "app:app"]
