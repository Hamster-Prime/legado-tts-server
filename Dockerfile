FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app

COPY --from=builder /install /usr/local
COPY app.py .

RUN mkdir -p /opt/doubao-tts

EXPOSE 80

ENV CONFIG_FILE=/opt/doubao-tts/config.json
ENV STATS_FILE=/opt/doubao-tts/stats.json

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:80/health')"

CMD ["python3", "app.py"]
