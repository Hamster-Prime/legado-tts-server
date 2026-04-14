# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder

WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py .
COPY legado-tts.service /etc/init.d/legado-tts 2>/dev/null || true

RUN mkdir -p /opt/doubao-tts && chmod 777 /opt/doubao-tts

EXPOSE 80

ENV CONFIG_FILE=/opt/doubao-tts/config.json \
    STATS_FILE=/opt/doubao-tts/stats.json \
    MAX_TEXT_LENGTH=5000 \
    CHUNK_SIZE=500 \
    AUDIO_CACHE_SIZE=100 \
    RATE_LIMIT_RPM=120 \
    PORT=80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:80/health')"

CMD ["python3", "-u", "app.py"]
