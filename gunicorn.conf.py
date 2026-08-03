"""Gunicorn configuration for Legado TTS Server."""
import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '80')}"

# ── Worker processes ──
# The app keeps rate limits, the daily character quota, the audio cache and the
# /metrics counters in module-level globals guarded by threading.Lock. Those are
# per-process, so running N worker processes silently multiplies the effective
# RATE_LIMIT_RPM and DAILY_CHAR_QUOTA by N, splits the cache N ways, and makes
# /metrics report only whichever worker served the request.
#
# The default is therefore a single process with a thread pool: 'gthread' handles
# concurrent requests inside one address space, so all of the above stay
# consistent. TTS work is network-bound on the upstream provider, so threads are
# the right unit of concurrency here anyway.
#
# Set GUNICORN_WORKERS>1 only if you accept per-worker limits (or enforce rate
# limiting at a reverse proxy instead). GUNICORN_THREADS is the knob to reach for
# when you need more concurrency.
workers = int(os.environ.get('GUNICORN_WORKERS', '1'))
worker_class = 'gthread'
threads = int(os.environ.get(
    'GUNICORN_THREADS',
    str(min(multiprocessing.cpu_count() * 4, 32)) if workers == 1 else '4',
))

# Timeouts
timeout = int(os.environ.get('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info').lower()

# ── Server ──
# preload_app shares the parent's import-time state via fork. Harmless with one
# worker; with several it is actively misleading, because each child then
# diverges from a shared starting point instead of starting empty.
preload_app = workers == 1
max_requests = int(os.environ.get('GUNICORN_MAX_REQUESTS', '10000'))
max_requests_jitter = 1000

# Process naming
proc_name = 'legado-tts'


def on_starting(server):
    """Warn loudly if the operator opted into per-worker limit multiplication."""
    if not os.environ.get('ADMIN_TOKEN', '').strip():
        server.log.warning(
            "ADMIN_TOKEN is not set; direct clients without an Origin header can "
            "access admin APIs. Set a strong token before network exposure.")
    if workers > 1:
        server.log.warning(
            "GUNICORN_WORKERS=%d: rate limits, daily quota, audio cache and "
            "/metrics are per-process, so limits are multiplied by %d and the "
            "cache is split %d ways. Prefer GUNICORN_THREADS for concurrency, "
            "or enforce limits at a reverse proxy.", workers, workers, workers)
