"""Gunicorn configuration for Legado TTS Server."""
import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '80')}"

# Worker processes
workers = int(os.environ.get('GUNICORN_WORKERS', min(multiprocessing.cpu_count() * 2 + 1, 8)))
worker_class = 'gthread'
threads = int(os.environ.get('GUNICORN_THREADS', '4'))

# Timeouts
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info').lower()

# Server
preload_app = True
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', '10000'))
max_requests_jitter = 1000

# Process naming
proc_name = 'legado-tts'
