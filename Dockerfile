# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py gunicorn.conf.py ./

RUN groupadd --system --gid 10001 tts \
 && useradd --system --uid 10001 --gid tts --no-create-home tts \
 && mkdir -p /opt/doubao-tts \
 && chown -R tts:tts /opt/doubao-tts /app

EXPOSE 8080

ENV CONFIG_FILE=/opt/doubao-tts/config.json \
    STATS_FILE=/opt/doubao-tts/stats.json \
    MAX_TEXT_LENGTH=5000 \
    CHUNK_SIZE=500 \
    AUDIO_CACHE_SIZE=100 \
    AUDIO_CACHE_MAX_MB=200 \
    RATE_LIMIT_RPM=120 \
    FALLBACK_TO_EDGE=1 \
    REQUEST_TIMEOUT=30 \
    ALLOW_SSML=1 \
    PORT=8080 \
    USE_GUNICORN=1

# Drop privileges. Note this is why PORT defaults to 8080 rather than 80: an
# unprivileged user cannot bind a port below 1024. Publish it as you like
# (`-p 80:8080`).
USER tts

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os,sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/livez', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "if [ \"${USE_GUNICORN}\" = \"1\" ] && command -v gunicorn >/dev/null 2>&1; then exec gunicorn -c gunicorn.conf.py app:app; else exec python3 -u app.py; fi"]
