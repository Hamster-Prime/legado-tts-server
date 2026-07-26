#!/usr/bin/env python3
"""TTS服务 - 支持 Edge、火山引擎、腾讯云、小米MiMo

为开源阅读 (Legado) 量身打造的聚合语音合成服务。
单一接口，后端根据音色参数自动分发请求。
"""

import os
import json
import base64
import uuid
import hmac
import hashlib
import time
import asyncio
import io
import logging
import math
import atexit
import signal
import re
import threading
import subprocess
import queue
import platform
import shutil
from collections import OrderedDict, deque
try:
    import fcntl
except ImportError:
    fcntl = None
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, Response, render_template_string, jsonify, after_this_request
from flask_cors import CORS
import requests
import edge_tts
import gzip
import functools

__version__ = '1.9.0'

def gzipped(f):
    """Decorator to gzip responses for clients that support it."""
    @functools.wraps(f)
    def view_func(*args, **kwargs):
        @after_this_request
        def zipper(response):
            accept_encoding = request.headers.get('Accept-Encoding', '')
            if 'gzip' not in accept_encoding.lower():
                return response
            if (response.status_code < 200 or response.status_code >= 300 or
                'Content-Encoding' in response.headers):
                return response
            # Never buffer a streamed/passthrough response just to compress it
            if response.direct_passthrough or response.is_streamed:
                return response
            # Don't compress small responses or audio
            if response.content_length is not None and response.content_length < 1000:
                return response
            if response.content_type and response.content_type.startswith('audio/'):
                return response
            # Compress response
            gzip_buffer = io.BytesIO()
            gzip_file = gzip.GzipFile(mode='wb', compresslevel=6, fileobj=gzip_buffer)
            gzip_file.write(response.data)
            gzip_file.close()
            response.data = gzip_buffer.getvalue()
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = str(len(response.data))
            return response
        return f(*args, **kwargs)
    return view_func


# Metrics
_metrics = {
    'requests_total': 0,
    'requests_success': 0,
    'requests_failed': 0,
    'chars_total': 0,
    'cache_hits_total': 0,
    'cache_lookups_total': 0,  # denominator for the hit ratio (same granularity as hits)
    'response_time_ms': deque(maxlen=1000),
    '_start_time': time.time(),
}
_metrics_lock = threading.Lock()

# ──────────────────────────────────────────────
# Request audit log (ring buffer of recent requests)
AUDIT_LOG_SIZE = int(os.environ.get('AUDIT_LOG_SIZE', '200'))
# An actual ring buffer: deque(maxlen=N) drops the oldest entry in O(1) on
# append, where the previous list + `del log[:len(log)-N]` was O(n) per request.
_audit_log = deque(maxlen=AUDIT_LOG_SIZE)
_audit_lock = threading.Lock()

def _audit_record(method, path, status, provider=None, voice=None, chars=0, ms=0, ip='', request_id=''):
    """Append a request record to the audit ring buffer."""
    rec = {
        'ts': datetime.now().isoformat(),
        'method': method,
        'path': path,
        'status': status,
        'provider': provider,
        'voice': voice,
        'chars': chars,
        'ms': round(ms, 1),
        'ip': ip,
        'request_id': request_id,
    }
    with _audit_lock:
        _audit_log.append(rec)  # deque(maxlen=AUDIT_LOG_SIZE) self-trims
    # Publish to SSE subscribers
    try:
        _sse_publish('tts_request', rec)
    except Exception:
        pass

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
LOG_JSON = os.environ.get('LOG_JSON', '0') == '1'

if LOG_JSON:
    class _JsonFormatter(logging.Formatter):
        def format(self, record):
            return json.dumps({
                'ts': self.formatTime(record),
                'level': record.levelname,
                'msg': record.getMessage(),
                'module': record.module,
            }, ensure_ascii=False)
    _handler = logging.StreamHandler()
    _handler.setFormatter(_JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[_handler])
else:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
log = logging.getLogger('tts-server')

# ──────────────────────────────────────────────
# Flask App
# ──────────────────────────────────────────────
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('MAX_CONTENT_LENGTH', str(10 * 1024 * 1024)))  # 10MB default
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/speech/*": {"origins": "*"},
    r"/v1/*": {"origins": "*"},
    r"/health": {"origins": "*"},
    r"/metrics": {"origins": "*"},
})


@app.errorhandler(404)
def handle_404(e):
    return _error_response('Not found', 404, 'not_found')

@app.errorhandler(405)
def handle_405(e):
    return _error_response('Method not allowed', 405, 'method_not_allowed')

@app.errorhandler(500)
def handle_500(e):
    log.error('Internal server error: %s', e)
    return _error_response('Internal server error', 500, 'server_error')


@app.before_request
def _before_request():
    request._start_time = time.time()
    request._request_id = request.headers.get('X-Request-ID', uuid.uuid4().hex[:12])
    request._rate_limit_info = {'limit': RATE_LIMIT_RPM, 'remaining': -1, 'reset': -1}


@app.after_request
def _after_request(response):
    # _start_time is missing when the request is rejected before before_request runs
    # (e.g. 413 from MAX_CONTENT_LENGTH), so fall back rather than raising in the hook.
    rt_ms = (time.time() - getattr(request, '_start_time', time.time())) * 1000
    if '/speech/stream' in request.path or '/v1/audio/speech' in request.path or '/api/speech/batch' in request.path:
        with _metrics_lock:
            _metrics['requests_total'] += 1
            if 200 <= response.status_code < 300:
                _metrics['requests_success'] += 1
            else:
                _metrics['requests_failed'] += 1
            _metrics['response_time_ms'].append(int(rt_ms))  # deque(maxlen=1000) self-trims
        # Audit log for TTS requests
        _audit_record(
            method=request.method,
            path=request.path,
            status=response.status_code,
            provider=getattr(request, '_tts_provider', None),
            voice=getattr(request, '_tts_voice', None),
            chars=getattr(request, '_tts_chars', 0),
            ms=rt_ms,
            ip=request.remote_addr or '',
            request_id=getattr(request, '_request_id', ''),
        )
    response.headers['X-Response-Time'] = str(int(rt_ms)) + 'ms'
    response.headers['X-Request-ID'] = getattr(request, '_request_id', '')
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # Rate limit headers
    rl_info = getattr(request, '_rate_limit_info', None)
    if rl_info:
        response.headers['X-RateLimit-Limit'] = str(rl_info.get('limit', ''))
        response.headers['X-RateLimit-Remaining'] = str(rl_info.get('remaining', ''))
        response.headers['X-RateLimit-Reset'] = str(rl_info.get('reset', ''))
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Server'] = f'Legado-TTS/{__version__}'
    return response

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/opt/doubao-tts/config.json')
STATS_FILE = os.environ.get('STATS_FILE', '/opt/doubao-tts/stats.json')
MAX_TEXT_LENGTH = int(os.environ.get('MAX_TEXT_LENGTH', '5000'))
BATCH_MAX_TEXTS = int(os.environ.get('BATCH_MAX_TEXTS', '20'))  # max texts per batch request
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
TEXT_NORMALIZE = os.environ.get('TEXT_NORMALIZE', '1') == '1'  # enable text normalization
AUDIO_CACHE_SIZE = int(os.environ.get('AUDIO_CACHE_SIZE', '100'))  # max cached items
AUDIO_CACHE_MAX_MB = int(os.environ.get('AUDIO_CACHE_MAX_MB', '200'))  # max cache memory MB
CHUNK_SIZE = int(os.environ.get('CHUNK_SIZE', '500'))  # chars per chunk for long text
RATE_LIMIT_RPM = int(os.environ.get('RATE_LIMIT_RPM', '120'))  # requests per minute, 0=unlimited
RATE_LIMIT_WHITELIST = set(filter(None, os.environ.get('RATE_LIMIT_WHITELIST', '127.0.0.1,::1').split(',')))
DAILY_CHAR_QUOTA = int(os.environ.get('DAILY_CHAR_QUOTA', '0'))  # daily char limit per IP, 0=unlimited
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN', '')  # protect config/stats/cache endpoints
API_KEYS = set(filter(None, os.environ.get('API_KEYS', '').split(',')))  # optional: restrict TTS access
API_KEYS_REQUIRED = os.environ.get('API_KEYS_REQUIRED', '0') == '1'  # require API key for TTS
ALLOW_SSML = os.environ.get('ALLOW_SSML', '1') == '1'  # allow SSML input
FALLBACK_TO_EDGE = os.environ.get('FALLBACK_TO_EDGE', '1') == '1'  # auto-fallback to Edge on failure
FALLBACK_VOICE = os.environ.get('FALLBACK_VOICE', 'zh-CN-XiaoxiaoNeural')  # voice to use when fallback to Edge
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '')  # optional webhook for error notifications
WEBHOOK_EVENTS = os.environ.get('WEBHOOK_EVENTS', 'error')  # comma-separated: error,synthesis,startup
REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '30'))  # seconds per provider request
FFMPEG_TIMEOUT = int(os.environ.get('FFMPEG_TIMEOUT', '30'))  # seconds per ffmpeg invocation

if LOG_LEVEL != 'INFO':
    log.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))

# Shared HTTP session with connection pooling & retry
_http_session = requests.Session()
_http_session.headers.update({'User-Agent': f'LegadoTTS/{__version__}'})
_adapter_kwargs = dict(pool_connections=10, pool_maxsize=20, max_retries=2, pool_block=False)
_http_session.mount('https://', requests.adapters.HTTPAdapter(**_adapter_kwargs))
_http_session.mount('http://', requests.adapters.HTTPAdapter(**_adapter_kwargs))

# Thread pool for async edge-tts execution
_edge_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='edge-tts')

# LRU audio cache: key=(text,voice,rate_pct) -> bytes
_audio_cache: OrderedDict = OrderedDict()
_audio_cache_lock = threading.Lock()
_audio_cache_bytes = 0  # total bytes in cache

# Rate limiter: sliding window per IP
_rate_limits: dict = {}  # ip -> list of timestamps
_rate_lock = threading.Lock()
_rate_limits_cleanup = 0  # counter to periodically clean up stale IPs
_daily_char_usage = {}  # {ip: {date: chars}}
_daily_char_lock = threading.Lock()
_daily_cleanup_counter = 0


def _cache_key(provider, text, voice, pct, style=None, volume='+0%', pitch='+0Hz'):
    """Cache key covering every parameter that changes the rendered audio.

    style/volume/pitch must be part of the key: otherwise two requests for the
    same text and voice with different prosody return each other's audio.
    """
    return (provider, voice, int(round(pct)), style or '', volume or '', pitch or '', text)


def _cache_get(key):
    with _audio_cache_lock:
        if key in _audio_cache:
            _audio_cache.move_to_end(key)
            return _audio_cache[key]
    return None


def _cache_put(key, data):
    global _audio_cache_bytes
    if not data:
        return
    max_bytes = AUDIO_CACHE_MAX_MB * 1024 * 1024
    data_size = len(data)
    with _audio_cache_lock:
        # Drop any existing entry first so its bytes are accounted for exactly once
        old = _audio_cache.pop(key, None)
        if old is not None:
            _audio_cache_bytes -= len(old)
        # A single item larger than the whole budget is never cached: storing it
        # would evict the entire cache and still leave us over the limit.
        if data_size > max_bytes:
            return
        while _audio_cache and (_audio_cache_bytes + data_size > max_bytes
                                or len(_audio_cache) >= AUDIO_CACHE_SIZE):
            _, evicted = _audio_cache.popitem(last=False)
            _audio_cache_bytes -= len(evicted)
        _audio_cache[key] = data
        _audio_cache_bytes += data_size


def _cache_clear():
    global _audio_cache_bytes
    with _audio_cache_lock:
        _audio_cache.clear()
        _audio_cache_bytes = 0


def _cache_info():
    with _audio_cache_lock:
        count, nbytes = len(_audio_cache), _audio_cache_bytes
    return {
        'count': count,
        'bytes': nbytes,
        'max_items': AUDIO_CACHE_SIZE,
        'max_mb': AUDIO_CACHE_MAX_MB,
        # Back-compat aliases
        'size': count,
        'max_size': AUDIO_CACHE_SIZE,
    }


def _check_rate_limit(ip):
    """Return True if rate limit exceeded, False otherwise.
    Sets request._rate_limit_info for response headers."""
    global _rate_limits, _rate_limits_cleanup
    if RATE_LIMIT_RPM <= 0:
        request._rate_limit_info = {'limit': 0, 'remaining': -1, 'reset': -1}
        return False
    # Whitelist IPs bypass rate limiting
    if ip in RATE_LIMIT_WHITELIST:
        request._rate_limit_info = {'limit': 0, 'remaining': -1, 'reset': -1}
        return False
    # Authenticated requests (valid ADMIN_TOKEN) bypass rate limiting
    if ADMIN_TOKEN and _is_admin():
        request._rate_limit_info = {'limit': 0, 'remaining': -1, 'reset': -1}
        return False
    now = time.time()
    cutoff = now - 60
    with _rate_lock:
        times = _rate_limits.get(ip, [])
        times = [t for t in times if t > cutoff]
        remaining = max(0, RATE_LIMIT_RPM - len(times) - 1)
        reset_at = int(times[0] + 60) if times else int(now + 60)
        if len(times) >= RATE_LIMIT_RPM:
            _rate_limits[ip] = times
            request._rate_limit_info = {'limit': RATE_LIMIT_RPM, 'remaining': 0, 'reset': reset_at}
            return True
        times.append(now)
        _rate_limits[ip] = times
        request._rate_limit_info = {'limit': RATE_LIMIT_RPM, 'remaining': remaining, 'reset': reset_at}
        # Evict stale IPs periodically (every 100 checks) to prevent memory leak
        _rate_limits_cleanup += 1
        if _rate_limits_cleanup >= 100:
            _rate_limits_cleanup = 0
            stale_cutoff = now - 300
            stale = [k for k, v in _rate_limits.items() if v[-1] <= stale_cutoff]
            for k in stale:
                del _rate_limits[k]
    return False


# ──────────────────────────────────────────────
# Text chunking for long text synthesis
# ──────────────────────────────────────────────

# Zero-width split after sentence-final punctuation. It must NOT consume anything
# (an earlier `\s*` suffix silently swallowed whitespace, so ''.join(chunks) != text).
_SPLIT_RE = re.compile(r'(?<=[。！？.!?\n])(?=.)', re.DOTALL)

# Secondary break points, highest priority first, used when a sentence-level chunk
# is still longer than max_chunk. Falls back to a hard cut if none is found.
_SUB_SEPARATORS = ('\n', '；', ';', '，', ',', '、', '：', ':', ' ')


def _split_text_chunks(text, max_chunk=None):
    """Split text into chunks at natural break points, each at most max_chunk chars.

    ``''.join(_split_text_chunks(t)) == t`` always holds: nothing is stripped or
    dropped, so joining the synthesized audio reproduces the original text.
    """
    if max_chunk is None:
        max_chunk = CHUNK_SIZE
    max_chunk = max(1, int(max_chunk))
    if len(text) <= max_chunk:
        return [text]
    # First pass: group whole sentences up to max_chunk
    chunks = []
    current = ''
    for s in _SPLIT_RE.split(text):
        if not s:
            continue
        if current and len(current) + len(s) > max_chunk:
            chunks.append(current)
            current = s
        else:
            current += s
    if current:
        chunks.append(current)
    # Second pass: an over-long sentence is broken at the latest secondary
    # separator inside the window, and only hard-cut when there is none.
    final = []
    for c in chunks:
        while len(c) > max_chunk:
            cut = -1
            for sep in _SUB_SEPARATORS:
                pos = c.rfind(sep, 0, max_chunk)
                if pos >= 0:
                    cut = max(cut, pos + len(sep))
                    if sep == '\n':
                        break
            if cut <= 0:
                cut = max_chunk
            final.append(c[:cut])
            c = c[cut:]
        if c:
            final.append(c)
    return final if final else [text]


def _concat_mp3(segments):
    """Concatenate MP3 byte segments. Simple append works for MP3 frames."""
    return b''.join(segments)


_FFMPEG_AVAILABLE = shutil.which('ffmpeg') is not None
_FORMAT_MIME = {
    'mp3': 'audio/mpeg',
    'wav': 'audio/wav',
    'ogg': 'audio/ogg; codecs=opus',
    'aac': 'audio/aac',
    'flac': 'audio/flac',
    'pcm': 'audio/pcm',
    'opus': 'audio/ogg; codecs=opus',
}


# ffmpeg output muxer + codec args per requested format. Note 'opus' must name
# the codec explicitly: the ogg muxer defaults to vorbis, which would contradict
# the 'audio/ogg; codecs=opus' content type we advertise for it.
_FFMPEG_ARGS = {
    'wav': ['-f', 'wav'],
    'ogg': ['-c:a', 'libopus', '-f', 'ogg'],
    'opus': ['-c:a', 'libopus', '-f', 'ogg'],
    'aac': ['-f', 'adts'],
    'flac': ['-f', 'flac'],
    'pcm': ['-ar', '24000', '-ac', '1', '-f', 's16le'],
}


def _convert_audio(audio_bytes: bytes, fmt: str):
    """Convert audio to the requested format using ffmpeg.

    Returns (bytes, actual_format). On any failure — ffmpeg missing, unknown
    format, non-zero exit, timeout — the original MP3 bytes are returned along
    with 'mp3', so callers never advertise a content type the payload doesn't
    match.
    """
    if fmt == 'mp3' or not _FFMPEG_AVAILABLE or fmt not in _FFMPEG_ARGS:
        return audio_bytes, 'mp3'
    try:
        proc = subprocess.run(
            ['ffmpeg', '-hide_banner', '-loglevel', 'error',
             '-i', 'pipe:0'] + _FFMPEG_ARGS[fmt] + ['-y', 'pipe:1'],
            input=audio_bytes, capture_output=True, timeout=FFMPEG_TIMEOUT,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout, fmt
        log.debug("Audio conversion to %s failed (rc=%d): %s", fmt, proc.returncode,
                  proc.stderr[:200] if proc.stderr else '')
    except subprocess.TimeoutExpired:
        log.warning("Audio conversion to %s timed out after %ss", fmt, FFMPEG_TIMEOUT)
    except Exception as e:
        log.debug("Audio conversion to %s failed: %s", fmt, e)
    return audio_bytes, 'mp3'


def _shutdown_edge_executor():
    global _edge_executor
    if _edge_executor is None:
        return
    executor = _edge_executor
    _edge_executor = None
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        executor.shutdown(wait=False)


# Registered via _graceful_shutdown (below), which calls this; a second
# atexit.register here would run the whole shutdown path twice.


# ──────────────────────────────────────────────
# Admin auth helper
# ──────────────────────────────────────────────

def _send_webhook(event_type, data):
    """Send webhook notification in background thread."""
    if not WEBHOOK_URL:
        return
    events = [e.strip() for e in WEBHOOK_EVENTS.split(',')]
    if event_type not in events:
        return
    def _do_send():
        try:
            payload = {
                'event': event_type,
                'timestamp': datetime.now().isoformat(),
                'version': __version__,
                'data': data,
            }
            _http_session.post(WEBHOOK_URL, json=payload, timeout=(5, 10))
        except Exception as e:
            log.debug('Webhook send failed: %s', e)
    threading.Thread(target=_do_send, daemon=True).start()


def _check_api_key():
    """Check if request has a valid API key. Returns error Response or None."""
    if not API_KEYS_REQUIRED or not API_KEYS:
        return None
    auth = request.headers.get('Authorization', '')
    token = request.args.get('api_key', '') or request.args.get('token', '')
    key = None
    if auth.startswith('Bearer '):
        key = auth[7:]
    elif token:
        key = token
    if not key or key not in API_KEYS:
        # ADMIN_TOKEN also works as API key
        if ADMIN_TOKEN and key == ADMIN_TOKEN:
            return None
        return _error_response('Invalid or missing API key', 401, 'authentication_error')
    return None


def _check_daily_quota(ip, chars):
    """Check and update daily character quota. Returns error Response or None."""
    global _daily_cleanup_counter
    if DAILY_CHAR_QUOTA <= 0:
        return None
    today = datetime.now().strftime('%Y-%m-%d')
    exceeded = False
    with _daily_char_lock:
        usage = _daily_char_usage.get(ip, {})
        current = usage.get(today, 0)
        if current + chars > DAILY_CHAR_QUOTA:
            # Build the response outside the lock (below); Response construction
            # touches the request context and has no business running under it.
            exceeded = True
            remaining = max(0, DAILY_CHAR_QUOTA - current)
        else:
            usage[today] = current + chars
            _daily_char_usage[ip] = usage
            # Clean old dates
            for d in [d for d in usage if d != today]:
                del usage[d]
            # Periodically evict stale IPs (every 100 checks)
            _daily_cleanup_counter += 1
            if _daily_cleanup_counter >= 100:
                _daily_cleanup_counter = 0
                for k in [k for k, v in _daily_char_usage.items() if not v]:
                    del _daily_char_usage[k]
    if exceeded:
        return _error_response(
            f'Daily quota exceeded ({DAILY_CHAR_QUOTA} chars/day)', 429, 'quota_exceeded',
            headers={'X-DailyQuota-Limit': str(DAILY_CHAR_QUOTA),
                     'X-DailyQuota-Remaining': str(remaining),
                     'Retry-After': '86400'},
            remaining=remaining)
    return None


def _int_arg(name, default, minimum=None, maximum=None):
    """Read an integer query parameter, clamped, without raising on garbage.

    ``?limit=abc`` used to reach int() directly and surface as a 500.
    """
    try:
        value = int(str(request.args.get(name, default)).strip())
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _error_response(message, status=400, error_type='invalid_request_error',
                    headers=None, **extra):
    """Return standardized error JSON response.

    Any keyword arguments beyond the named ones are merged into the ``error``
    object (e.g. ``remaining=`` on a quota rejection).
    """
    body = {
        'message': message,
        'type': error_type,
        'request_id': getattr(request, '_request_id', None),
    }
    body.update(extra)
    return Response(
        json.dumps({'error': body}, ensure_ascii=False),
        status=status,
        mimetype='application/json',
        headers=headers or {},
    )


def _extract_bearer_token():
    """Extract a bearer token from the Authorization header or ?token= query arg."""
    auth = request.headers.get('Authorization', '')
    if auth[:7].lower() == 'bearer ':
        token = auth[7:].strip()
    else:
        token = auth.strip()
    return token or request.args.get('token', '')


def _is_admin():
    """Return True if admin auth is satisfied (or not required)."""
    if not ADMIN_TOKEN:
        return True
    return hmac.compare_digest(_extract_bearer_token(), ADMIN_TOKEN)


def _check_admin():
    """Return error Response if ADMIN_TOKEN is set but request lacks auth."""
    if _is_admin():
        return None
    return _error_response('Unauthorized', 401, 'authentication_error')

DEFAULT_CONFIG = {
    'provider': 'edge',
    'appid': '',
    'access_token': '',
    'default_voice': 'zh-CN-XiaoxiaoNeural',
    'cluster': 'volcano_tts',
    'tencent_secret_id': '',
    'tencent_secret_key': '',
    'tencent_voice': '501002',
    'tencent_region': 'ap-guangzhou',
    'edge_voice': 'zh-CN-XiaoxiaoNeural',
    'xiaomi_api_key': '',
    'xiaomi_voice': 'mimo_default',
    'fishaudio_api_key': '',
    'fishaudio_voice': 'fish-animated',
    'fishaudio_reference_id': '',
    'pronunciation_dict': {},  # custom word->replacement mapping
    'voice_favorites': [],  # list of voice IDs the user has bookmarked
}

# ──────────────────────────────────────────────
# Voice catalogs
# ──────────────────────────────────────────────
DOUBAO_VOICES = [
    {"id": "zh_female_cancan_mars_bigtts", "name": "知性灿灿"},
    {"id": "zh_female_shuangkuaisisi_moon_bigtts", "name": "爽快思思"},
    {"id": "zh_female_tiexinnvsheng_mars_bigtts", "name": "贴心女生"},
    {"id": "zh_female_jitangmeimei_mars_bigtts", "name": "鸡汤妹妹"},
    {"id": "zh_female_mengyatou_mars_bigtts", "name": "萌丫头"},
    {"id": "zh_male_shaonianzixin_moon_bigtts", "name": "少年梓辛"},
    {"id": "zh_male_wennuanahu_moon_bigtts", "name": "温暖阿虎"},
    {"id": "zh_male_jieshuonansheng_mars_bigtts", "name": "磁性解说"},
]

TENCENT_VOICES = [
    {"id": "501002", "name": "智菊 - 阅读女声"},
    {"id": "501000", "name": "智斌 - 阅读男声"},
    {"id": "501001", "name": "智兰 - 资讯女声"},
    {"id": "501003", "name": "智宇 - 阅读男声"},
    {"id": "601009", "name": "爱小芊 - 多情感女声"},
    {"id": "601008", "name": "爱小豪 - 多情感男声"},
    {"id": "601010", "name": "爱小娇 - 多情感女声"},
]

EDGE_VOICES = [
    # Chinese Mainland
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 - 女声"},
    {"id": "zh-CN-YunxiNeural", "name": "云希 - 男声"},
    {"id": "zh-CN-YunjianNeural", "name": "云健 - 男声"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊 - 女声"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬 - 新闻"},
    {"id": "zh-CN-XiaochenNeural", "name": "晓辰 - 女声"},
    {"id": "zh-CN-XiaohanNeural", "name": "晓涵 - 女声"},
    {"id": "zh-CN-XiaomengNeural", "name": "晓梦 - 女声"},
    {"id": "zh-CN-XiaomoNeural", "name": "晓墨 - 女声"},
    {"id": "zh-CN-XiaoqiuNeural", "name": "晓秋 - 女声"},
    {"id": "zh-CN-XiaoruiNeural", "name": "晓睿 - 女声"},
    {"id": "zh-CN-XiaoshuangNeural", "name": "晓双 - 童声"},
    {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱 - 女声"},
    {"id": "zh-CN-XiaoyanNeural", "name": "晓颜 - 女声"},
    {"id": "zh-CN-XiaoyouNeural", "name": "晓悠 - 童声"},
    {"id": "zh-CN-YunfengNeural", "name": "云枫 - 男声"},
    {"id": "zh-CN-YunhaoNeural", "name": "云皓 - 男声"},
    {"id": "zh-CN-YunxiaNeural", "name": "云夏 - 男声"},
    {"id": "zh-CN-YunyeNeural", "name": "云野 - 男声"},
    {"id": "zh-CN-YunzeNeural", "name": "云泽 - 男声"},
    # Chinese Taiwan
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻 - 台湾女声"},
    {"id": "zh-TW-YunJheNeural", "name": "雲哲 - 台湾男声"},
    {"id": "zh-TW-HsiaoYuNeural", "name": "曉雨 - 台湾女声"},
    # Chinese HK (Cantonese)
    {"id": "zh-HK-HiuMaanNeural", "name": "曉曼 - 粤语女声"},
    {"id": "zh-HK-WanLungNeural", "name": "雲龍 - 粤语男声"},
    {"id": "zh-HK-HiuGaaiNeural", "name": "曉佳 - 粤语女声"},
    # English
    {"id": "en-US-JennyNeural", "name": "Jenny - English F"},
    {"id": "en-US-GuyNeural", "name": "Guy - English M"},
    {"id": "en-US-AriaNeural", "name": "Aria - English F"},
    {"id": "en-US-DavisNeural", "name": "Davis - English M"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia - British F"},
    {"id": "en-GB-RyanNeural", "name": "Ryan - British M"},
    # Japanese
    {"id": "ja-JP-NanamiNeural", "name": "Nanami - Japanese F"},
    {"id": "ja-JP-KeitaNeural", "name": "Keita - Japanese M"},
    # Korean
    {"id": "ko-KR-SunHiNeural", "name": "SunHi - Korean F"},
    {"id": "ko-KR-InJoonNeural", "name": "InJoon - Korean M"},
]

XIAOMI_VOICES = [
    {"id": "mimo_default", "name": "MiMo默认语音"},
    {"id": "default_zh", "name": "中文女声"},
    {"id": "default_eh", "name": "英文女声"},
]

FISH_AUDIO_VOICES = [
    {"id": "fish-animated", "name": "Fish Animated - 活泼女声"},
    {"id": "fish-speech-zh", "name": "Fish Speech - 自然女声"},
    {"id": "fish-audio-male", "name": "Fish Audio - 沉稳男声"},
    {"id": "fish-narrator", "name": "Fish Narrator - 讲述者"},
    {"id": "custom", "name": "Fish 自定义 (在配置中设置reference_id)"},
]

ALL_PROVIDERS = ['doubao', 'tencent', 'edge', 'xiaomi', 'fishaudio']
_ALL_VOICE_IDS = set()
# ──────────────────────────────────────────────
# Voice name -> ID lookup (for OpenAI compat)
# ──────────────────────────────────────────────

_VOICE_NAME_TO_ID = {}
for _voices in [EDGE_VOICES, DOUBAO_VOICES, TENCENT_VOICES, XIAOMI_VOICES, FISH_AUDIO_VOICES]:
    for _v in _voices:
        _ALL_VOICE_IDS.add(_v['id'])
        _VOICE_NAME_TO_ID[_v['name'].lower()] = _v['id']

# Common aliases for easier configuration
_VOICE_ALIASES = {
    # OpenAI compatible aliases
    'alloy': 'zh-CN-XiaoxiaoNeural',
    'echo': 'zh-CN-YunxiNeural',
    'fable': 'zh-CN-XiaoyiNeural',
    'onyx': 'zh-CN-YunjianNeural',
    'nova': 'zh-CN-XiaochenNeural',
    'shimmer': 'zh-CN-XiaohanNeural',
    # Chinese shorthand
    '晓晓': 'zh-CN-XiaoxiaoNeural',
    '云希': 'zh-CN-YunxiNeural',
    '晓伊': 'zh-CN-XiaoyiNeural',
    '云健': 'zh-CN-YunjianNeural',
    '晓辰': 'zh-CN-XiaochenNeural',
    '晓涵': 'zh-CN-XiaohanNeural',
    # Doubao shorthand ('甸甘' was a typo for the 知性灿灿 voice; kept as an alias
    # so any existing Legado config that used it keeps working)
    '灿灿': 'zh_female_cancan_mars_bigtts',
    '甸甘': 'zh_female_cancan_mars_bigtts',
}
# Aliases must resolve to a real voice ID, otherwise resolve_provider() sends the
# request to a provider that will reject it.
_VOICE_ALIASES = {k: v for k, v in _VOICE_ALIASES.items() if v in _ALL_VOICE_IDS}
_VOICE_NAME_TO_ID.update({k.lower(): v for k, v in _VOICE_ALIASES.items()})


def _sanitize_voice(value):
    """Strip header-injection characters and whitespace from a voice parameter."""
    if value is None:
        return ''
    return str(value).strip().replace('\r', '').replace('\n', '').replace('\x00', '')


def _resolve_voice_alias(voice):
    """Map a display name or alias to a canonical voice ID.

    Known IDs are returned untouched so a voice whose ID also appears as some
    other voice's display name can never be rewritten.
    """
    if not voice or voice in _ALL_VOICE_IDS:
        return voice
    return _VOICE_NAME_TO_ID.get(voice.lower(), voice)


def _tts_headers(audio, requested_provider, requested_voice,
                 final_provider, final_voice, chars):
    """Standard audio response headers, including fallback disclosure."""
    headers = {
        'Content-Length': str(len(audio)),
        'X-TTS-Provider': final_provider,
        'X-TTS-Voice': final_voice,
        'X-TTS-Chars': str(chars),
        'Cache-Control': 'no-store',
    }
    if final_provider != requested_provider or final_voice != requested_voice:
        headers['X-TTS-Requested-Provider'] = requested_provider
        headers['X-TTS-Requested-Voice'] = requested_voice
        headers['X-TTS-Fallback'] = 'true'
    return headers

# ──────────────────────────────────────────────
# File helpers
# ──────────────────────────────────────────────

def _read_json(path, default):
    """Read JSON file, return default on any error."""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        if os.path.exists(path):
            log.warning("Failed to read %s: %s", path, e)
        return default


def _write_json(path, data):
    """Write JSON file atomically via tmp + replace."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# In-memory config cache with mtime check
_config_cache = {'data': None, 'mtime': None}
_config_cache_lock = threading.Lock()


def _copy_config(cfg):
    """Copy a config dict one level deep so callers cannot mutate the cached
    nested containers (e.g. pronunciation_dict, favorites) in place."""
    out = {}
    for k, v in cfg.items():
        if isinstance(v, dict):
            out[k] = dict(v)
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    return out


def load_config():
    """Load config with in-memory cache. Re-reads file if mtime changed."""
    try:
        st = os.stat(CONFIG_FILE)
        # Include size and inode: mtime alone has coarse granularity on some
        # filesystems, so two writes in the same tick could go unnoticed.
        stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        stamp = None
    with _config_cache_lock:
        if _config_cache['data'] is not None and _config_cache['mtime'] == stamp:
            return _copy_config(_config_cache['data'])
    # Cache miss or file changed
    cfg = _read_json(CONFIG_FILE, None)
    if not isinstance(cfg, dict):
        cfg = {}
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v.copy() if isinstance(v, (dict, list)) else v
    with _config_cache_lock:
        _config_cache['data'] = cfg
        _config_cache['mtime'] = stamp
    return _copy_config(cfg)


def save_config(config):
    _write_json(CONFIG_FILE, config)
    # Invalidate cache
    with _config_cache_lock:
        _config_cache['data'] = None
        _config_cache['mtime'] = None


# ──────────────────────────────────────────────
# Stats (file-locked)
# ──────────────────────────────────────────────

_empty_provider_stats = {'total_chars': 0, 'total_requests': 0, 'history': []}


def _new_provider_stats():
    """Return a fresh empty stats dict (deep copy to prevent shared references)."""
    return {'total_chars': 0, 'total_requests': 0, 'history': []}


def load_stats():
    data = _read_json(STATS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    for p in ALL_PROVIDERS:
        if p not in data or not isinstance(data[p], dict):
            data[p] = _new_provider_stats()
        for k in _empty_provider_stats:
            if k not in data[p]:
                data[p][k] = [] if k == 'history' else _empty_provider_stats[k]
    return data


def _apply_stats_update(stats, chars, provider, voice=None):
    # The on-disk file may be corrupt or hold a non-dict at this key
    ps = stats.get(provider)
    if not isinstance(ps, dict):
        ps = _new_provider_stats()
    if not isinstance(ps.get('history'), list):
        ps['history'] = []
    if not isinstance(ps.get('voice_stats'), dict):
        ps.pop('voice_stats', None)
    ps['total_chars'] = ps.get('total_chars', 0) + chars
    ps['total_requests'] = ps.get('total_requests', 0) + 1
    today = datetime.now().strftime('%Y-%m-%d')
    history = ps.get('history', [])
    day_entry = next((d for d in history if d.get('date') == today), None)
    if day_entry:
        day_entry['chars'] = day_entry.get('chars', 0) + chars
        day_entry['requests'] = day_entry.get('requests', 0) + 1
    else:
        history.append({'date': today, 'chars': chars, 'requests': 1})
    ps['history'] = history[-30:]
    # Per-voice stats (top 20 voices)
    if voice:
        voice_stats = ps.get('voice_stats', {})
        vs = voice_stats.get(voice, {'chars': 0, 'requests': 0})
        vs['chars'] = vs.get('chars', 0) + chars
        vs['requests'] = vs.get('requests', 0) + 1
        voice_stats[voice] = vs
        # Keep only top 20 voices by request count
        if len(voice_stats) > 20:
            sorted_vs = sorted(voice_stats.items(), key=lambda x: x[1].get('requests', 0), reverse=True)
            voice_stats = dict(sorted_vs[:20])
        ps['voice_stats'] = voice_stats
    stats[provider] = ps
    return stats


# Path errors that will never resolve by themselves: a wrong owner, a read-only
# mount, a typo'd directory. Distinct from transient IO, which is worth retrying.
_STATS_FATAL_ERRORS = (PermissionError, FileNotFoundError, NotADirectoryError,
                       IsADirectoryError)


def _update_stats_with_retry(chars, provider, voice=None, retries=3, delay=0.1):
    for attempt in range(1, retries + 1):
        try:
            stats = load_stats()
            _write_json(STATS_FILE, _apply_stats_update(stats, chars, provider, voice=voice))
            return
        except _STATS_FATAL_ERRORS:
            raise           # retrying an unfixable path problem just adds latency
        except OSError as e:
            if attempt == retries:
                raise
            log.warning("Stats update retry %d/%d after error: %s", attempt, retries, e)
            time.sleep(delay * attempt)


# Set once if STATS_FILE turns out to be unwritable. Without this latch a
# read-only or wrong-owner stats path logged an ERROR and burned three sleeping
# retries on *every* request -- for a condition only an operator can fix.
# Synthesis itself does not depend on stats, so we degrade to the in-memory
# counters (/metrics stays correct) and say so exactly once.
_stats_readonly = False


def update_stats(chars, provider, voice=None):
    global _stats_readonly
    if not provider:
        return
    with _metrics_lock:
        _metrics['chars_total'] += chars
    if _stats_readonly:
        return
    try:
        parent = os.path.dirname(STATS_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if fcntl is None:
            _update_stats_with_retry(chars, provider, voice=voice)
            return
        with open(STATS_FILE, 'a+', encoding='utf-8') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                try:
                    f.seek(0)
                    raw = f.read()
                    stats = json.loads(raw) if raw.strip() else {}
                except (json.JSONDecodeError, ValueError):
                    stats = {}
                stats = _apply_stats_update(stats, chars, provider, voice=voice)
                f.seek(0)
                f.truncate()
                json.dump(stats, f, indent=2, ensure_ascii=False)
            finally:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    pass
    except _STATS_FATAL_ERRORS as e:
        _stats_readonly = True
        log.error("Stats path unusable (%s); persistence disabled for this "
                  "process. Set STATS_FILE to a writable path or fix ownership. "
                  "In-memory counters and /metrics are unaffected.", e)
    except Exception as e:
        log.error("Failed to update stats: %s", e)


# ──────────────────────────────────────────────
# Retry helper
# ──────────────────────────────────────────────

def _retry(func, retries=2, delay=0.5):
    for attempt in range(1, retries + 1):
        try:
            return func()
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == retries:
                raise
            log.warning("Request failed (attempt %d/%d): %s", attempt, retries, e)
            time.sleep(delay * attempt)


# ──────────────────────────────────────────────
# TTS Providers
# ──────────────────────────────────────────────

def synthesize_doubao(text, voice, speed_ratio=1.0):
    config = load_config()
    if not config.get('appid') or not config.get('access_token'):
        return None, "未配置火山引擎AppID或Token"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer; {config['access_token']}",
    }
    payload = {
        "app": {"appid": config["appid"], "token": "placeholder",
                "cluster": config.get("cluster", "volcano_tts")},
        "user": {"uid": "legado_user"},
        "audio": {"voice_type": voice, "encoding": "mp3", "speed_ratio": speed_ratio},
        "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"},
    }
    def _do():
        resp = _http_session.post(
            "https://openspeech.bytedance.com/api/v1/tts",
            headers=headers, json=payload, timeout=(min(REQUEST_TIMEOUT, 10), REQUEST_TIMEOUT))
        result = resp.json()
        if result.get("code") != 3000:
            return None, f"火山引擎API错误: {result.get('message', 'Unknown')}"
        b64 = result.get("data", "")
        if not b64:
            return None, "火山引擎返回空音频"
        return base64.b64decode(b64), None
    try:
        return _retry(_do)
    except Exception as e:
        return None, str(e)


def _tencent_sign(secret_key, date, service, string_to_sign):
    def h256(key, msg):
        return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
    sd = h256(('TC3' + secret_key).encode('utf-8'), date)
    ss = h256(sd, service)
    si = h256(ss, 'tc3_request')
    return hmac.new(si, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()


def synthesize_tencent(text, voice, speed=0):
    config = load_config()
    sid, skey = config.get('tencent_secret_id', ''), config.get('tencent_secret_key', '')
    if not sid or not skey:
        return None, "未配置腾讯云SecretId或SecretKey"
    if not voice.isdigit():
        return None, f"腾讯云VoiceType必须为数字，当前值: {voice}"
    svc, host, act, ver = 'tts', 'tts.tencentcloudapi.com', 'TextToVoice', '2019-08-23'
    region, algo = config.get('tencent_region', 'ap-guangzhou'), 'TC3-HMAC-SHA256'
    ts = int(time.time())
    date = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%Y-%m-%d')
    payload = json.dumps({"Text": text, "SessionId": str(uuid.uuid4()),
                         "VoiceType": int(voice), "Codec": "mp3",
                         "SampleRate": 16000, "Speed": speed})
    ct = 'application/json; charset=utf-8'
    ch = f'content-type:{ct}\nhost:{host}\nx-tc-action:{act.lower()}\n'
    sh = 'content-type;host;x-tc-action'
    hp = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    cr = f'POST\n/\n\n{ch}\n{sh}\n{hp}'
    cs = f'{date}/{svc}/tc3_request'
    hc = hashlib.sha256(cr.encode('utf-8')).hexdigest()
    sts = f'{algo}\n{ts}\n{cs}\n{hc}'
    sig = _tencent_sign(skey, date, svc, sts)
    auth = f'{algo} Credential={sid}/{cs}, SignedHeaders={sh}, Signature={sig}'
    hdrs = {'Authorization': auth, 'Content-Type': ct, 'Host': host,
            'X-TC-Action': act, 'X-TC-Timestamp': str(ts),
            'X-TC-Version': ver, 'X-TC-Region': region}
    def _do():
        resp = _http_session.post(f'https://{host}', headers=hdrs, data=payload, timeout=(min(REQUEST_TIMEOUT, 10), REQUEST_TIMEOUT))
        result = resp.json()
        if 'Response' in result and 'Audio' in result['Response']:
            return base64.b64decode(result['Response']['Audio']), None
        err = result.get('Response', {}).get('Error', {})
        return None, f"腾讯云错误: {err.get('Message', str(result))}"
    try:
        return _retry(_do)
    except Exception as e:
        return None, str(e)


def _edge_communicate(text, voice, rate='+0%', style=None, volume='+0%', pitch='+0Hz'):
    """Build an edge_tts.Communicate for the given prosody settings."""
    if ALLOW_SSML and text.strip().startswith('<speak'):
        # SSML carries its own prosody; passing rate/volume/pitch too is rejected
        return edge_tts.Communicate(text, voice)
    kwargs = {'rate': rate}
    if style:
        kwargs['style'] = style
    if volume != '+0%':
        kwargs['volume'] = volume
    if pitch != '+0Hz':
        kwargs['pitch'] = pitch
    return edge_tts.Communicate(text, voice, **kwargs)


def synthesize_edge(text, voice, rate='+0%', style=None, volume='+0%', pitch='+0Hz'):
    async def _synth():
        comm = _edge_communicate(text, voice, rate, style, volume, pitch)
        buf = io.BytesIO()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.write(chunk["data"])
        return buf.getvalue()
    try:
        try:
            asyncio.get_running_loop()
            loop_running = True
        except RuntimeError:
            loop_running = False
        if loop_running:
            executor = _edge_executor
            if executor is None:
                return None, 'Edge TTS executor is shut down'
            # The coroutine is created inside the branch that consumes it, so it
            # is never left un-awaited (which would emit a RuntimeWarning).
            fut = executor.submit(asyncio.run, _synth())
            return fut.result(timeout=REQUEST_TIMEOUT), None
        return asyncio.run(_synth()), None
    except Exception as e:
        return None, str(e)


_EDGE_VOICES_TTL = int(os.environ.get('EDGE_VOICES_TTL', '3600'))
_edge_live_cache = {'voices': None, 'ts': 0.0}
_edge_live_lock = threading.Lock()


def _edge_live_voices():
    """Return Microsoft's live voice list, cached for EDGE_VOICES_TTL seconds.

    The upstream list is ~320 entries and changes rarely; fetching it on every
    request added a network round trip to each page load of the admin UI.
    """
    now = time.time()
    with _edge_live_lock:
        if (_edge_live_cache['voices'] is not None
                and now - _edge_live_cache['ts'] < _EDGE_VOICES_TTL):
            return _edge_live_cache['voices']
    voices = asyncio.run(edge_tts.list_voices())
    with _edge_live_lock:
        _edge_live_cache['voices'] = voices
        _edge_live_cache['ts'] = now
    return voices


def _edge_stream_iter(text, voice, rate='+0%', style=None, volume='+0%', pitch='+0Hz'):
    """Yield Edge TTS audio chunks as they arrive, without buffering the whole clip.

    edge-tts is async and Flask handlers are sync, so the async generator is
    drained one chunk at a time on a dedicated event loop owned by this
    iterator. Previously the 'streaming' path ran the coroutine to completion
    into a list first, so nothing was sent until synthesis had fully finished.
    """
    loop = asyncio.new_event_loop()
    try:
        comm = _edge_communicate(text, voice, rate, style, volume, pitch)
        agen = comm.stream()
        while True:
            try:
                chunk = loop.run_until_complete(
                    asyncio.wait_for(agen.__anext__(), timeout=REQUEST_TIMEOUT))
            except StopAsyncIteration:
                break
            if chunk.get('type') == 'audio' and chunk.get('data'):
                yield chunk['data']
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


def synthesize_fishaudio(text, voice):
    config = load_config()
    api_key = config.get('fishaudio_api_key', '')
    if not api_key:
        return None, "未配置Fish Audio API Key"
    # Map voice to Fish Audio reference_id
    voice_map = {
        'fish-animated': 'b7f4b37e-6e92-4f72-a650-d2c8a1dc50e3',
        'fish-speech-zh': '7f92f8a6-4cf1-4590-88c2-4e0f84eb265e',
        'fish-audio-male': 'a5e6c9db-2b6c-4c3e-8c1e-2e8f0c3a4b5d',
        'fish-narrator': '9d4f0c1e-5a2b-4c8d-9e6f-1a2b3c4d5e6f',
    }
    ref_id = config.get('fishaudio_reference_id', '')
    if not ref_id:
        ref_id = voice_map.get(voice, voice)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "text": text,
        "reference_id": ref_id,
        "format": "mp3",
    }
    def _do():
        resp = _http_session.post(
            "https://api.fish.audio/v1/tts",
            headers=headers, json=payload, timeout=(min(REQUEST_TIMEOUT, 10), REQUEST_TIMEOUT))
        if resp.status_code != 200:
            return None, f"Fish Audio API错误 ({resp.status_code}): {resp.text[:200]}"
        return resp.content, None
    try:
        return _retry(_do)
    except Exception as e:
        return None, str(e)


def _build_xiaomi_style(speed_ratio):
    """Build style tag for Xiaomi MiMo TTS based on speed ratio."""
    if abs(speed_ratio - 1.0) < 0.05:
        return "<style>语速适中 吐字清晰 节奏稳定</style>"
    if speed_ratio > 1.0:
        if speed_ratio >= 1.8: return "<style>语速很快 节奏紧凑</style>"
        if speed_ratio >= 1.4: return "<style>语速偏快 表达流畅</style>"
        return "<style>语速稍快 自然流畅</style>"
    if speed_ratio <= 0.5: return "<style>语速很慢 咬字清晰</style>"
    if speed_ratio <= 0.8: return "<style>语速偏慢 语气舒缓</style>"
    return "<style>语速稍慢 清晰稳重</style>"


def synthesize_xiaomi(text, voice, speed_ratio=1.0):
    config = load_config()
    api_key = config.get('xiaomi_api_key', '')
    if not api_key:
        return None, "未配置小米API Key"
    style = _build_xiaomi_style(speed_ratio)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    payload = {
        "model": "mimo-v2-tts",
        "messages": [
            {"role": "user", "content": style + text},
            {"role": "assistant", "content": style + text},
        ],
        "audio": {"format": "mp3", "voice": voice},
    }
    def _do():
        resp = _http_session.post(
            "https://api.xiaomimimo.com/v1/chat/completions",
            headers=headers, json=payload, timeout=(min(REQUEST_TIMEOUT, 10), REQUEST_TIMEOUT))
        resp.raise_for_status()
        result = resp.json()
        choices = result.get('choices', [])
        if choices:
            audio = choices[0].get('message', {}).get('audio', {})
            if 'data' in audio:
                return base64.b64decode(audio['data']), None
        return None, f"小米API返回错误: {resp.text[:200]}"
    try:
        return _retry(_do)
    except Exception as e:
        return None, str(e)


# ──────────────────────────────────────────────
# Voice routing & dispatch
# ──────────────────────────────────────────────

def resolve_provider(voice):
    """Determine TTS provider from voice ID."""
    if not voice:
        return None
    # Sanitize voice to prevent header injection
    voice = _sanitize_voice(voice)
    if 'Neural' in voice and '-' in voice:
        return 'edge'
    if voice.isdigit() and 1 <= int(voice) <= 999999:
        return 'tencent'
    if voice.startswith('zh_'):
        return 'doubao'
    if voice in ('mimo_default', 'default_zh', 'default_eh') or voice.startswith('mimo_'):
        return 'xiaomi'
    if voice.startswith('fish-') or voice == 'custom':
        return 'fishaudio'
    return None


# Speed presets for easy configuration
_SPEED_PRESETS = {
    'very-slow': -30, 'slow': -15, 'normal': 0, 'fast': 20, 'very-fast': 40,
    '很慢': -30, '慢速': -15, '正常': 0, '快速': 20, '很快': 40,
    '0.5x': -50, '0.75x': -25, '1x': 0, '1.25x': 25, '1.5x': 50, '2x': 100,
}

_LEGADO_RATE_RE = re.compile(r'^(\d+(?:\.\d+)?)([-+]\d+(?:\.\d+)?)%$')


def _pct_to_rate(pct):
    """Format a percentage offset as an Edge TTS rate string ('+10%' / '-10%')."""
    n = int(round(pct))
    return f'+{n}%' if n >= 0 else f'{n}%'


def parse_rate(rate_str):
    """Parse rate string to float percentage offset for TTS.

    Legado (开源阅读) speakSpeed format (confirmed from source code):
      speakSpeed = AppConfig.speechRatePlay + 5
      speechRatePlay SeekBar range: 0~20, default 10
      => speakSpeed range: 5~25, normal speed = 15
      URL template: {{String(speakSpeed)}}+0%  => e.g. "15+0%"

    Conversion formula:
      pct = (speakSpeed - 15) / 15 * 100
      e.g. speakSpeed=5  => -67% (slow)
           speakSpeed=15 => 0%   (normal)
           speakSpeed=25 => +67% (fast)

    Other supported formats:
    - '+50%', '-20%'  -> 50.0, -20.0  (direct percentage offset)
    - 'fast', 'slow'  -> preset values
    - '1.5x'          -> 50.0  (speed multiplier, 1.0=normal)
    """
    s = str(rate_str).strip().lower()
    if not s:
        return 0.0
    if s in _SPEED_PRESETS:
        return float(_SPEED_PRESETS[s])
    # Legado speakSpeed format: "<integer>+0%" or "<integer>-0%"
    # speakSpeed is an integer (5~25), normal=15
    legado_m = _LEGADO_RATE_RE.match(s)
    if legado_m:
        speak_speed = float(legado_m.group(1))
        base_offset = float(legado_m.group(2))
        # If speakSpeed looks like a Legado integer speed (1~30 range)
        # convert: (speakSpeed - 15) / 15 * 100 + base_offset
        # Normal speakSpeed=15 => 0%, speakSpeed=5 => -67%, speakSpeed=25 => +67%
        pct = (speak_speed - 15.0) / 15.0 * 100.0 + base_offset
        return max(-90.0, min(200.0, pct))
    # x-suffix multiplier: '1.5x' -> 50%
    if s.endswith('x'):
        try:
            multiplier = float(s[:-1])
            return max(-90.0, min(200.0, (multiplier - 1.0) * 100.0))
        except (ValueError, TypeError):
            pass
    # Direct percentage: '+50%', '-20%', '50'
    try:
        return float(s.replace('%', '').replace('+', '').strip())
    except (ValueError, TypeError):
        return 0.0


# ──────────────────────────────────────────────
# Text normalization tables and patterns.
# These are module-level and pre-compiled: they used to be rebuilt as dict
# literals (and ~40 regexes re-compiled) on every single synthesis request.
# ──────────────────────────────────────────────

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_BLANK_LINES_RE = re.compile(r'\n{3,}')
_HSPACE_RE = re.compile(r'(?<=\S)[ \t]+')
_LEADING_SPACE_RE = re.compile(r'^[ \t]+', re.MULTILINE)

# Common emoji -> Chinese description. Duplicate keys in the original literal
# silently shadowed each other; only the surviving (last) value is kept here.
_EMOJI_MAP = {
    '😊': '，微笑，', '😃': '，大笑，', '😄': '，微笑，', '😁': '，开心，',
    '😆': '，憨笑，', '😂': '，笑哭，', '🤣': '，大笑，',
    '😇': '，天使，', '🙂': '，微笑，', '🙃': '，颠倒，', '😉': '，眨眼，',
    '😌': '，松口气，', '😍': '，喜欢，', '🥰': '，爱慕，', '😘': '，亲亲，',
    '😗': '，亲亲，', '😙': '，亲亲，', '😚': '，亲亲，', '😜': '，调皮，',
    '🤪': '，搞怪，', '😝': '，吐舌，', '🤗': '，抱抱，', '🤭': '，偷笑，',
    '🤫': '，嘘，', '🤔': '，思考，', '🤐': '，闭嘴，', '🤨': '，疑惑，',
    '😐': '，中立，', '😑': '，无语，', '😒': '，不爽，', '🙄': '，白眼，',
    '😬': '，尴尬，', '🤥': '，说谎，', '😔': '，难过，',
    '😪': '，困，', '🤤': '，流口水，', '😴': '，睡觉，', '😷': '，口罩，',
    '🤒': '，生病，', '🤕': '，受伤，', '🤢': '，恶心，', '🤮': '，呕吐，',
    '🤧': '，感冒，', '🥶': '，冷，', '🥴': '，晕，',
    '😵': '，晕，', '🤠': '，牛仔，', '🥳': '，庆祝，',
    '😎': '，酷，', '🤓': '，书呆子，', '🧐': '，好奇，', '😕': '，困惑，',
    '🫤': '，失望，', '😟': '，担心，', '🙁': '，难过，', '☹️': '，难过，',
    '😮': '，惊讶，', '😯': '，惊讶，', '😲': '，震惊，',
    '🥺': '，恳求，', '😦': '，惊讶，', '😧': '，痛苦，', '😨': '，害怕，',
    '😰': '，冷汗，', '😥': '，失望，', '😢': '，哭，', '😭': '，大哭，',
    '😱': '，恐惧，', '😖': '，痛苦，', '😣': '，痛苦，', '😞': '，失望，',
    '😓': '，汗，', '😩': '，累，', '😫': '，累，', '🥱': '，困，',
    '😤': '，生气，', '😡': '，愤怒，', '😠': '，生气，', '🤬': '，骂脏话，',
    '🤯': '，头炸，', '😳': '，害羞，', '🥵': '，脸红发热，',
    '❤️': '，爱心，', '💔': '，心碎，', '💕': '，两颗心，', '💓': '，心跳，',
    '💗': '，爱心，', '💖': '，爱心，', '💘': '，丘比特，', '💝': '，礼物心，',
    '💟': '，爱心，', '❣️': '，爱心，', '💞': '，心心，',
    '👍🏻': '，赞，', '👏🏻': '，鼓掌，',
    '👍': '，赞，', '👎': '，踩，', '👌': '，好的，', '✌️': '，耶，',
    '🤞': '，好运，', '🤟': '，爱你，', '🤘': '，摇滚，', '👊': '，拳头，',
    '✊': '，加油，', '👋': '，再见，', '🤚': '，举手，', '🖐️': '，击掌，',
    '✋': '，停，', '🖖': '，挥手，', '🙌': '，举双手，', '👏': '，鼓掌，',
    '🙏': '，拜托，', '🤝': '，握手，',
}
# One pass instead of ~120 str.replace() calls. Longest key first so the
# skin-tone variants ('👍🏻') win over their base glyph ('👍').
_EMOJI_RE = re.compile('|'.join(re.escape(k) for k in
                                sorted(_EMOJI_MAP, key=len, reverse=True)))

# The map covers ~113 glyphs; there are thousands. Anything unmapped used to
# reach the provider verbatim, where each engine invents its own handling —
# Edge reads some aloud by CLDR name ("grinning face"), others produce a gap.
# Strip the rest instead, along with the invisible modifiers (variation
# selectors, skin-tone modifiers, ZWJ) that would otherwise be left orphaned
# once their base glyph is replaced.
_RESIDUAL_EMOJI_RE = re.compile(
    '['
    '\U0001F000-\U0001FAFF'   # pictographs, symbols, tiles, supplemental
    '\U00002600-\U000027BF'   # misc symbols + dingbats
    '\U00002190-\U000021FF'   # arrows
    '\U00002B00-\U00002BFF'   # misc symbols and arrows
    '\U0001F1E6-\U0001F1FF'   # regional indicators (flags)
    '\U0000FE00-\U0000FE0F'   # variation selectors
    '\U0001F3FB-\U0001F3FF'   # skin-tone modifiers
    '\U0000200D'              # zero-width joiner
    '\U000020E3'              # combining enclosing keycap-ish
    ']')

# An ellipsis carries meaning (trailing off, hesitation) that engines render as
# a pause, so fold the ASCII/CJK spellings onto '…' *before* the collapse below
# would flatten '...' to a single '.' and lose it. '…' is not in the collapse
# class, so it survives from here on.
_ELLIPSIS_RE = re.compile(r'\.{3,}|。{3,}|·{3,}')

# Runs of adjacent CJK/ASCII punctuation collapse to the single strongest mark,
# so an emoji-inserted '，' next to an existing '。' doesn't read as two pauses.
_PUNCT_RUN_RE = re.compile(r'[，。！？、；：,.!?;:]{2,}')
# Strongest first: a terminal stop beats a comma, and '！'/'？' beat '。'.
_PUNCT_RANK = ('！', '?', '？', '!', '。', '.', '；', ';', '：', ':', '、', '，', ',')


def _PUNCT_PRIORITY(run):
    """Return the single most emphatic punctuation mark present in ``run``."""
    for mark in _PUNCT_RANK:
        if mark in run:
            return mark
    return run[0]

_ABBREV_MAP = {
    'Mr.': '先生', 'Mrs.': '女士', 'Dr.': '博士',
    'vs.': '对', 'etc.': '等等', 'e.g.': '例如', 'i.e.': '也就是',
    'PS:': '附注：', 'P.S.': '附注：',
}
_ABBREV_RE = re.compile('|'.join(re.escape(k) for k in
                                 sorted(_ABBREV_MAP, key=len, reverse=True)))

_DATE_RE = re.compile(r'(?<!\d)(\d{4})[/-](\d{1,2})[/-](\d{1,2})(?!\d)')
_TIME_RE = re.compile(r'(?<![\d:])(\d{1,2}):([0-5]\d)(?![\d:])')
_TEMP_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:°|℃|℉)([CF])?')
_PCT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_LONG_NUM_RE = re.compile(r'(?<![\d.])(\d[\d\- ]{3,}\d)(?![\d.])')
_YEAR_RE = re.compile(r'(?<!\d)(\d{4})\s*年')
_CURRENCY_RE = re.compile(
    r'([¥￥$€£₩])\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*(日元|美元|欧元|英镑|韩元|元)')
_ORDINAL_RE = re.compile(r'(第)(\d+(?:\.\d+)?)(章|节|页|条|款|部分|讲|集)?')

_CURRENCY_NAMES = {
    '¥': '元', '￥': '元', '元': '元', '$': '美元', '美元': '美元',
    '€': '欧元', '欧元': '欧元', '£': '英镑', '英镑': '英镑',
    '₩': '韩元', '韩元': '韩元', '日元': '日元',
}

_UNIT_MAP = {
    'km': '公里', 'kg': '公斤', 'cm': '厘米', 'mm': '毫米', 'ml': '毫升',
    'GB': 'G字节', 'MB': '兆字节', 'KB': '千字节',
    'min': '分钟', 'h': '小时', 's': '秒', 'm': '米', 'g': '克',
}
# Longest-first alternation in ONE regex, so 'min'/'km'/'kg' can never be
# pre-empted by the shorter 'm'/'k'/'g' the way sequential re.sub calls allowed.
_UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(' +
                      '|'.join(re.escape(u) for u in
                               sorted(_UNIT_MAP, key=len, reverse=True)) +
                      r')(?![a-zA-Z])')
# Unit tokens that may legitimately follow a long digit run (checked as a
# lookahead string, not a single character).
_NUM_UNIT_SUFFIXES = ('公里', '元', '年', '月', '日', '个', '人', '米', '克',
                      '吨', '秒', '分', '时', '℃', '℉', '°', '%',
                      'km', 'cm', 'mm', 'ml', 'kg', 'GB', 'MB', 'KB',
                      'min', 'g', 's', 'h')

_CURRENCY_SYMBOLS = '¥￥$€£₩'
# Tokens that, when they follow a number, mean a later rule will actually speak
# the digits (units, currency names, percent, temperature, decimal point). Only
# those -- _NUM_UNIT_SUFFIXES is a wider lookahead used to classify long digit
# runs, and includes tails like '人'/'个' that no rule converts, so reusing it
# here would strip the separators and leave bare digits behind.
_NUM_TAIL_TOKENS = (tuple(_UNIT_MAP) + tuple(_CURRENCY_NAMES) +
                    ('.', '%', '°', '℃', '℉'))

# Western thousands separators. Every numeric rule below matches
# `\d+(?:\.\d+)?`, which stops dead at a comma -- '¥1,000' used to normalize to
# '一元,000' ("one yuan, zero zero zero"), i.e. worse than leaving it alone.
# Groups of exactly three digits are an unambiguous enough signal to strip; a
# comma-separated list of 3-digit numbers ('100,200,300') is the rare loser and
# gets read as a single magnitude.
_THOUSANDS_RE = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+)(?![\d,])')

_ROMAN_MAP = {
    'Ⅰ': '一', 'Ⅱ': '二', 'Ⅲ': '三', 'Ⅳ': '四', 'Ⅴ': '五',
    'Ⅵ': '六', 'Ⅶ': '七', 'Ⅷ': '八', 'Ⅸ': '九', 'Ⅹ': '十',
    'Ⅺ': '十一', 'Ⅻ': '十二',
    'II': '二', 'III': '三', 'IV': '四', 'VI': '六', 'VII': '七',
    'VIII': '八', 'IX': '九', 'XI': '十一', 'XII': '十二',
}
_ROMAN_FULL_RE = re.compile(r'[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]')
# Half-width romans are only converted when at least two characters long.
# Single 'I'/'V'/'X'/'L' are ordinary English words or initials ("I have...",
# "V for victory", "X-ray") and must never be spoken as 一/五/十.
# Hyphens are excluded on both sides so "X-ray"/"II-A" stay intact.
_ROMAN_HALF_RE = re.compile(r'(?<![a-zA-Z0-9-])(?:III|VIII|XII|VII|II|IV|VI|IX|XI)(?![a-zA-Z0-9-])')

_SLANG_MAP = {
    'yyds': '永远的神', 'emo': '情绪低落', 'u1s1': '有一说一',
    'xswl': '笑死我了', 'awsl': '啊我死了', 'dbq': '对不起',
    'pyq': '朋友圈', 'ssfd': '瑟瑟发抖', 'gkd': '搞快点',
    'kdl': '磕到了', 'szd': '是真的', 'dddd': '懂的都懂',
}
# Case-insensitive single pass replaces the previous 24 separate re.sub calls
# (one per upper/lower spelling).
_SLANG_RE = re.compile(r'(?<![a-zA-Z0-9])(' +
                       '|'.join(re.escape(k) for k in
                                sorted(_SLANG_MAP, key=len, reverse=True)) +
                       r')(?![a-zA-Z0-9])', re.IGNORECASE)

_ABBR_MAP = {
    'AI': '人工智能', 'ML': '机器学习', 'DL': '深度学习', 'NLP': '自然语言处理',
    'API': '接口', 'SDK': '开发套件', 'UI': '界面', 'UX': '用户体验',
    'CPU': '处理器', 'GPU': '图形处理器', 'RAM': '内存', 'ROM': '只读存储器',
    'OS': '操作系统', 'PC': '个人电脑', 'APP': '应用', 'URL': '网址',
    'HTTP': '超文本传输协议', 'HTTPS': '安全超文本传输协议',
    'Wi-Fi': '无线网络', 'WiFi': '无线网络', 'WIFI': '无线网络',
    'GPS': '全球定位系统', 'VPN': '虚拟私人网络',
    'ID': '编号', 'IP': '网络地址', 'DNS': '域名系统',
    'CEO': '首席执行官', 'CTO': '首席技术官', 'CFO': '首席财务官',
    'VIP': '贵宾', 'DIY': '自己动手做', 'OK': '好的', 'LOL': '哈哈',
    'PDF': 'PDF文档', 'PPT': '幻灯片', 'ETA': '预计到达时间',
    'P.S.': '附注', 'PS': '附注', 'FAQ': '常见问题', 'FYI': '供参考',
    'ASAP': '尽快', 'TBD': '待定', 'BFF': '最好的朋友',
    'IoT': '物联网', 'IOT': '物联网', 'AR': '增强现实', 'VR': '虚拟现实',
}
# 'HTTPS' must be tried before 'HTTP', 'Wi-Fi' before 'ID', etc.
_ABBR_RE = re.compile(r'(?<![a-zA-Z])(' +
                      '|'.join(re.escape(k) for k in
                               sorted(_ABBR_MAP, key=len, reverse=True)) +
                      r')(?![a-zA-Z])')

# Chinese digit mapping
_CN_DIGITS = '零一二三四五六七八九'
_CN_UNITS = {1: '十', 2: '百', 3: '千', 4: '万', 8: '亿'}


def _clean_text(text):
    """Normalize text: remove control chars, normalize numbers/dates, apply pronunciation dict."""
    # Remove NULL and C0 controls except \t (0x09), \n (0x0a), \r (0x0d)
    text = _CONTROL_CHARS_RE.sub('', text)
    # Replace known emojis with spoken descriptions (single pass), then drop
    # whatever pictographs/modifiers remain rather than shipping them upstream.
    text = _EMOJI_RE.sub(lambda m: _EMOJI_MAP[m.group(0)], text)
    text = _RESIDUAL_EMOJI_RE.sub('', text)
    # Fold '...'/'。。。' to '…' so the collapse below can't reduce a deliberate
    # ellipsis to a single full stop.
    text = _ELLIPSIS_RE.sub('…', text)
    # Emoji substitution inserts '，' around each description, which can abut
    # existing punctuation ('好的😊。' -> '好的，微笑，。'). Collapse those runs.
    text = _PUNCT_RUN_RE.sub(lambda m: _PUNCT_PRIORITY(m.group(0)), text)
    # Collapse multiple blank lines but keep paragraph structure
    text = _BLANK_LINES_RE.sub('\n\n', text)
    # Collapse horizontal whitespace per line
    text = _HSPACE_RE.sub(' ', text)
    text = _LEADING_SPACE_RE.sub('', text)
    text = text.strip()
    # Text normalization for better TTS (configurable)
    if TEXT_NORMALIZE:
        text = _normalize_text(text)
    # Apply custom pronunciation dictionary
    text = _apply_pronunciation_dict(text)
    return text


def _digits_to_chinese(digits):
    """Read a digit string out one digit at a time (years, phone numbers, IDs)."""
    return ''.join(_CN_DIGITS[int(d)] for d in digits if d.isdigit())


def _num_to_chinese(n):
    """Convert an integer or float to Chinese characters."""
    # Handle floating point numbers
    if isinstance(n, float):
        if math.isnan(n) or math.isinf(n):
            return ''
        negative = n < 0
        # Fixed-point formatting: str()/repr() switch to scientific notation for
        # large or tiny values ('1e+21'), which fed 'e'/'+' into _CN_DIGITS.
        s = f'{abs(n):.6f}'.rstrip('0').rstrip('.')
        if '.' in s:
            int_str, dec_str = s.split('.', 1)
            out = _num_to_chinese(int(int_str)) + '点' + _digits_to_chinese(dec_str)
        else:
            out = _num_to_chinese(int(s or '0'))
        return ('负' + out) if negative else out
    n = int(n)
    if n < 0:
        return '负' + _num_to_chinese(-n)
    # Beyond 亿亿 the unit names below stop making sense; read it out digit by
    # digit rather than emitting nonsense like '一亿亿'.
    if n >= 10 ** 16:
        return _digits_to_chinese(str(n))
    if n < 10:
        return _CN_DIGITS[n]
    if n < 100:
        t, r = divmod(n, 10)
        return ('' if t == 1 else _CN_DIGITS[t]) + '十' + (_CN_DIGITS[r] if r else '')
    if n < 1000:
        h, r = divmod(n, 100)
        return _CN_DIGITS[h] + '百' + ('零' + _num_to_chinese(r) if 0 < r < 10 else _num_to_chinese(r) if r else '')
    if n < 10000:
        t, r = divmod(n, 1000)
        return _CN_DIGITS[t] + '千' + ('零' + _num_to_chinese(r) if 0 < r < 100 else _num_to_chinese(r) if r else '')
    if n < 100000000:
        w, r = divmod(n, 10000)
        return _num_to_chinese(w) + '万' + ('零' + _num_to_chinese(r) if 0 < r < 1000 else _num_to_chinese(r) if r else '')
    y, r = divmod(n, 100000000)
    return _num_to_chinese(y) + '亿' + (_num_to_chinese(r) if r else '')


def _safe_float(s):
    """Parse a numeric string, returning None instead of raising."""
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(v) or math.isinf(v)) else v


def _num_str_to_chinese(num_str):
    """Convert a decimal string to Chinese, preserving the written precision."""
    if '.' in num_str:
        int_part, dec_part = num_str.split('.', 1)
        head = _num_to_chinese(int(int_part or '0'))
        dec_part = dec_part.rstrip('0')
        # '1.50' reads as 一点五; '1.0' reads as just 一
        return head + ('点' + _digits_to_chinese(dec_part) if dec_part else '')
    return _num_to_chinese(int(num_str))


def _date_repl(m):
    """2024-01-15 -> 二零二四年1月15日 (year spoken digit by digit)."""
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return m.group(0)  # not a date, leave for the number rules
    # The year is converted here rather than left as digits for the later
    # _YEAR_RE pass, which would otherwise re-process this output.
    return f'{_digits_to_chinese(y)}年{_num_to_chinese(mo)}月{_num_to_chinese(d)}日'


def _time_repl(m):
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23:
        return m.group(0)
    return _num_to_chinese(h) + '点' + (_num_to_chinese(mi) + '分' if mi else '')


def _temp_repl(m):
    val = _safe_float(m.group(1))
    if val is None:
        return m.group(0)
    unit = '华氏度' if (m.group(2) == 'F' or '℉' in m.group(0)) else '摄氏度'
    return _num_str_to_chinese(m.group(1)) + unit


def _pct_repl(m):
    if _safe_float(m.group(1)) is None:
        return m.group(0)
    return '百分之' + _num_str_to_chinese(m.group(1))


def _thousands_repl(m):
    """Resolve a comma-grouped number like '1,234' or '1,400,000,000'.

    Grouping is itself a signal: nobody writes a phone number or an ID with
    thousands separators, so these are quantities and must never fall through to
    the digit-by-digit reading in :func:`_long_number_repl`. When a later rule
    owns the digits (a currency symbol before, or a unit/percent/decimal after)
    we only drop the separators and let that rule speak; otherwise we convert
    here, which also takes the number out of _LONG_NUM_RE's reach.
    """
    raw = m.group(1)
    digits = raw.replace(',', '')
    head = m.string[:m.start()].rstrip()
    tail = m.string[m.end():m.end() + 4].lstrip()
    if (head and head[-1] in _CURRENCY_SYMBOLS) or tail.startswith(_NUM_TAIL_TOKENS):
        return digits
    try:
        return _num_to_chinese(int(digits))
    except (ValueError, OverflowError):
        return raw


def _long_number_repl(m):
    """Phone/ID/serial numbers are read digit by digit; quantities as numbers."""
    raw = m.group(1)
    num_str = raw.replace('-', '').replace(' ', '')
    if not num_str.isdigit():
        return raw
    tail = m.string[m.end():m.end() + 4]
    read_digit = False
    if len(num_str) == 11 and num_str.startswith('1'):
        read_digit = True                       # mobile number
    elif 15 <= len(num_str) <= 19:
        read_digit = True                       # ID / bank card
    elif '-' in raw or ' ' in raw:
        read_digit = True                       # grouped digits: an identifier
    elif len(num_str) == 4 and tail.startswith('年'):
        read_digit = True                       # year
    elif len(num_str) >= 5:
        # '12345号' is a serial number; '12345km' is a quantity; bare digits
        # default to being read out one by one.
        read_digit = tail.startswith('号') or not tail.startswith(_NUM_UNIT_SUFFIXES)
    if read_digit:
        return _digits_to_chinese(num_str)
    try:
        return _num_to_chinese(int(num_str))
    except (ValueError, OverflowError):
        return raw


def _year_repl(m):
    return _digits_to_chinese(m.group(1)) + '年'


def _currency_repl(m):
    symbol = m.group(1) or m.group(4)
    amount_str = m.group(2) or m.group(3)
    if _safe_float(amount_str) is None:
        return m.group(0)
    name = _CURRENCY_NAMES.get(symbol)
    if not name:
        return m.group(0)
    return _num_str_to_chinese(amount_str) + name


def _unit_repl(m):
    if _safe_float(m.group(1)) is None:
        return m.group(0)
    return _num_str_to_chinese(m.group(1)) + _UNIT_MAP[m.group(2)]


def _ordinal_repl(m):
    prefix, num, suffix = m.group(1), m.group(2), m.group(3) or ''
    return f'{prefix}{_num_str_to_chinese(num)}{suffix}'


def _normalize_text(text):
    """Normalize numbers, dates, and common patterns for better TTS.

    Rule order matters: each pass must not re-process the output of an earlier
    one. Dates convert their own year (so _YEAR_RE cannot double-convert it),
    and the digit-run rule runs before the plain year rule.
    """
    # Expand common abbreviations (single pass, longest match first)
    text = _ABBREV_RE.sub(lambda m: _ABBREV_MAP[m.group(0)], text)
    # Thousands separators first: every rule below matches `\d+(?:\.\d+)?` and
    # would otherwise stop at the comma, normalizing only the leading group.
    text = _THOUSANDS_RE.sub(_thousands_repl, text)
    # Date: 2024-01-01 or 2024/01/01
    text = _DATE_RE.sub(_date_repl, text)
    # Time: 14:30 -> 十四点三十分
    text = _TIME_RE.sub(_time_repl, text)
    # Temperature: 36.5°C -> 三十六点五摄氏度
    text = _TEMP_RE.sub(_temp_repl, text)
    # Percentage: 50% -> 百分之五十
    text = _PCT_RE.sub(_pct_repl, text)
    # Currency before the digit-run rule, so ¥12345 is money and not a serial number
    text = _CURRENCY_RE.sub(_currency_repl, text)
    # Units before the digit-run rule, for the same reason (12345km)
    text = _UNIT_RE.sub(_unit_repl, text)
    # Ordinals before the digit-run rule (第12345章)
    text = _ORDINAL_RE.sub(_ordinal_repl, text)
    # Long digit runs: phone numbers, IDs, serial numbers
    text = _LONG_NUM_RE.sub(_long_number_repl, text)
    # Remaining 4-digit years: 2024年 -> 二零二四年
    text = _YEAR_RE.sub(_year_repl, text)
    # Roman numerals
    text = _ROMAN_FULL_RE.sub(lambda m: _ROMAN_MAP.get(m.group(0), m.group(0)), text)
    text = _ROMAN_HALF_RE.sub(lambda m: _ROMAN_MAP.get(m.group(0), m.group(0)), text)
    # Internet slang (case-insensitive, single pass)
    text = _SLANG_RE.sub(lambda m: _SLANG_MAP[m.group(1).lower()], text)
    # English abbreviations (single pass, longest match first)
    text = _ABBR_RE.sub(lambda m: _ABBR_MAP[m.group(1)], text)
    return text


_pron_cache = {'src': None, 're': None, 'map': None}
_pron_cache_lock = threading.Lock()


def _pronunciation_pattern(pdict):
    """Compile (and memoize) a longest-match-first pattern for the custom dict.

    Longest-first matters: with {'AI': 'A I', 'AIGC': '...'}, plain sequential
    str.replace() would rewrite the 'AI' inside 'AIGC' first. A single regex
    pass also prevents one replacement's output from being re-substituted by a
    later entry.
    """
    entries = {k: v for k, v in pdict.items()
               if isinstance(k, str) and isinstance(v, str) and k and v}
    if not entries:
        return None, None
    key = tuple(sorted(entries.items()))
    with _pron_cache_lock:
        if _pron_cache['src'] == key:
            return _pron_cache['re'], _pron_cache['map']
    pattern = re.compile('|'.join(re.escape(k) for k in
                                  sorted(entries, key=len, reverse=True)))
    with _pron_cache_lock:
        _pron_cache['src'], _pron_cache['re'], _pron_cache['map'] = key, pattern, entries
    return pattern, entries


def _apply_pronunciation_dict(text):
    """Replace words in text according to custom pronunciation dictionary."""
    pdict = load_config().get('pronunciation_dict')
    if not isinstance(pdict, dict) or not pdict:
        return text
    pattern, entries = _pronunciation_pattern(pdict)
    if pattern is None:
        return text
    return pattern.sub(lambda m: entries[m.group(0)], text)


def dispatch(provider, text, voice, pct, style=None, volume='+0%', pitch='+0Hz'):
    """Route to the correct TTS provider and return (audio_bytes, error, actual_provider, actual_voice).
    When FALLBACK_TO_EDGE is enabled and a non-edge provider fails,
    automatically retry with Edge TTS using a default Chinese voice."""
    text = _clean_text(text)
    if not text:
        return None, 'Text is empty after cleaning', None, None

    audio, err = _dispatch_impl(provider, text, voice, pct, style=style, volume=volume, pitch=pitch)
    if audio:
        return audio, None, provider, voice

    # Send error webhook notification
    _send_webhook('error', {
        'provider': provider,
        'voice': voice,
        'error': str(err),
        'text_length': len(text),
    })

    # Fallback to Edge TTS if enabled and primary provider is not edge
    if FALLBACK_TO_EDGE and provider != 'edge':
        fallback_voice = FALLBACK_VOICE
        log.warning("Fallback to Edge TTS: provider=%s voice=%s error=%s",
                    provider, voice, err)
        fb_audio, fb_err = _dispatch_impl('edge', text, fallback_voice, pct,
                                          style=style, volume=volume, pitch=pitch)
        if fb_audio:
            return fb_audio, None, 'edge', fallback_voice
        return None, f'Primary: {err}; Fallback(Edge): {fb_err}', None, None
    return None, err, None, None


def _dispatch_impl(provider, text, voice, pct, style=None, volume='+0%', pitch='+0Hz'):
    """Internal dispatch without fallback. Chunks long text and joins the segments."""
    # Whole-text cache hit short-circuits chunking entirely
    whole_key = _cache_key(provider, text, voice, pct, style, volume, pitch)
    cached = _cache_get(whole_key)
    with _metrics_lock:
        _metrics['cache_lookups_total'] += 1
        if cached is not None:
            _metrics['cache_hits_total'] += 1
    if cached is not None:
        log.info("Cache hit: provider=%s voice=%s chars=%d", provider, voice, len(text))
        return cached, None

    chunks = _split_text_chunks(text)
    if len(chunks) == 1:
        # whole_key is this chunk's key and we just missed on it — don't probe twice
        return _dispatch_single(provider, chunks[0], voice, pct, style=style,
                                volume=volume, pitch=pitch, use_cache=False)

    log.info("Long text (%d chars) split into %d chunks", len(text), len(chunks))
    segments = []
    for i, chunk in enumerate(chunks):
        audio, err = _dispatch_single(provider, chunk, voice, pct,
                                      style=style, volume=volume, pitch=pitch)
        if not audio:
            return None, f'Chunk {i+1}/{len(chunks)} failed: {err}'
        segments.append(audio)
    merged = _concat_mp3(segments)
    if not merged:
        return None, 'Audio concatenation produced no data'
    _cache_put(whole_key, merged)
    return merged, None


def _dispatch_single(provider, text, voice, pct, style=None, volume='+0%',
                     pitch='+0Hz', use_cache=True):
    """Dispatch a single chunk to the correct TTS provider."""
    cache_key = _cache_key(provider, text, voice, pct, style, volume, pitch)
    if use_cache:
        cached = _cache_get(cache_key)
        with _metrics_lock:
            _metrics['cache_lookups_total'] += 1
            if cached is not None:
                _metrics['cache_hits_total'] += 1
        if cached is not None:
            log.info("Cache hit: provider=%s voice=%s chars=%d", provider, voice, len(text))
            return cached, None
    if provider == 'edge':
        audio, err = synthesize_edge(text, voice, _pct_to_rate(pct), style=style, volume=volume, pitch=pitch)
    elif provider == 'tencent':
        audio, err = synthesize_tencent(text, voice, max(-2, min(6, pct / 50)))
    elif provider == 'doubao':
        audio, err = synthesize_doubao(text, voice, max(0.2, min(3.0, 1.0 + pct / 100)))
    elif provider == 'xiaomi':
        audio, err = synthesize_xiaomi(text, voice, max(0.2, min(3.0, 1.0 + pct / 100)))
    elif provider == 'fishaudio':
        audio, err = synthesize_fishaudio(text, voice)
    else:
        return None, f'Unknown provider: {provider}'
    if audio:
        _cache_put(cache_key, audio)
    return audio, err


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route('/health')
@gzipped
def health():
    config = load_config()
    uptime_sec = time.time() - _metrics.get('_start_time', time.time())
    return jsonify({
        'status': 'ok', 'version': __version__,
        'timestamp': datetime.now().isoformat(),
        'uptime_seconds': int(uptime_sec),
        'python_version': platform.python_version(),
        'providers': ALL_PROVIDERS,
        'active_provider': config.get('provider', 'edge'),
        'cache': _cache_info(),
        'admin_protected': bool(ADMIN_TOKEN),
        'ffmpeg_available': _FFMPEG_AVAILABLE,
        'ssml_enabled': ALLOW_SSML,
        'fallback_enabled': FALLBACK_TO_EDGE,
        'rate_limit_rpm': RATE_LIMIT_RPM,
        'request_timeout': REQUEST_TIMEOUT,
        'pronunciation_dict_size': len(config.get('pronunciation_dict', {})),
    })


@app.route('/livez')
def livez():
    """Kubernetes liveness probe - lightweight, always returns OK if process is alive."""
    return Response('ok', status=200, mimetype='text/plain')


@app.route('/readyz')
def readyz():
    """Kubernetes readiness probe - checks if the service can handle requests."""
    issues = []
    config = load_config()
    provider = config.get('provider', 'edge')
    # Check basic config sanity
    if provider not in ALL_PROVIDERS:
        issues.append(f'unknown provider: {provider}')
    # Check API keys for non-edge providers
    if provider == 'doubao' and not config.get('access_token'):
        issues.append('doubao: missing access_token')
    if provider == 'tencent' and not config.get('tencent_secret_id'):
        issues.append('tencent: missing secret_id')
    if provider == 'fishaudio' and not config.get('fishaudio_api_key'):
        issues.append('fishaudio: missing api_key')
    # Check cache directory writable
    try:
        parent = os.path.dirname(CONFIG_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
    except OSError as e:
        issues.append(f'config dir not writable: {e}')
    if issues:
        return Response(
            json.dumps({'ready': False, 'issues': issues}, ensure_ascii=False),
            status=503, mimetype='application/json'
        )
    return Response(
        json.dumps({'ready': True, 'provider': provider}),
        status=200, mimetype='application/json'
    )


@app.route('/metrics')
@gzipped
def metrics():
    """Prometheus-compatible metrics endpoint."""
    with _metrics_lock:
        total = _metrics['requests_total']
        success = _metrics['requests_success']
        failed = _metrics['requests_failed']
        chars = _metrics['chars_total']
        cache_hits = _metrics['cache_hits_total']
        cache_lookups = _metrics['cache_lookups_total']
        rts = list(_metrics['response_time_ms'])
    # Hits and lookups are both counted per cache probe, so the ratio stays in [0,1].
    # (Dividing per-chunk hits by per-request counts could exceed 1.0.)
    cache_hit_rate = cache_hits / cache_lookups if cache_lookups > 0 else 0.0
    avg_rt = sum(rts) / len(rts) if rts else 0.0
    p50_rt = p95_rt = p99_rt = 0.0
    if rts:
        sorted_rts = sorted(rts)
        n = len(sorted_rts)

        def _pct(q):
            # Nearest-rank percentile; index is clamped so q=1.0 and n=1 are safe
            return sorted_rts[min(n - 1, max(0, int(math.ceil(q * n)) - 1))]

        p50_rt, p95_rt, p99_rt = _pct(0.50), _pct(0.95), _pct(0.99)
    out = [
        '# HELP tts_requests_total Total number of TTS requests',
        '# TYPE tts_requests_total counter',
        f'tts_requests_total {total}',
        '# HELP tts_requests_success Number of successful TTS requests',
        '# TYPE tts_requests_success counter',
        f'tts_requests_success {success}',
        '# HELP tts_requests_failed Number of failed TTS requests',
        '# TYPE tts_requests_failed counter',
        f'tts_requests_failed {failed}',
        '# HELP tts_chars_total Total number of characters synthesized',
        '# TYPE tts_chars_total counter',
        f'tts_chars_total {chars}',
        '# HELP tts_cache_hits_total Total number of cache hits',
        '# TYPE tts_cache_hits_total counter',
        f'tts_cache_hits_total {cache_hits}',
        '# HELP tts_cache_hit_ratio Cache hit ratio (0.0-1.0)',
        '# TYPE tts_cache_hit_ratio gauge',
        f'tts_cache_hit_ratio {cache_hit_rate:.4f}',
        '# HELP tts_response_time_ms_avg Average response time in milliseconds',
        '# TYPE tts_response_time_ms_avg gauge',
        f'tts_response_time_ms_avg {avg_rt:.2f}',
        '# HELP tts_response_time_ms_p50 50th percentile response time in milliseconds',
        '# TYPE tts_response_time_ms_p50 gauge',
        f'tts_response_time_ms_p50 {p50_rt:.2f}',
        '# HELP tts_response_time_ms_p95 95th percentile response time in milliseconds',
        '# TYPE tts_response_time_ms_p95 gauge',
        f'tts_response_time_ms_p95 {p95_rt:.2f}',
        '# HELP tts_response_time_ms_p99 99th percentile response time in milliseconds',
        '# TYPE tts_response_time_ms_p99 gauge',
        f'tts_response_time_ms_p99 {p99_rt:.2f}',
    ]
    return Response('\n'.join(out) + '\n', mimetype='text/plain')


@app.route('/speech/stream', methods=['POST'])
def speech_stream():
    try:
        # Rate limiting
        client_ip = request.remote_addr or 'unknown'
        if _check_rate_limit(client_ip):
            return Response('Rate limit exceeded', status=429,
                            headers={'Retry-After': '60'})
        # API key check
        key_err = _check_api_key()
        if key_err:
            return key_err

        data = request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        voice = _sanitize_voice(data.get('voice'))
        style = str(data.get('style', '')).strip() or None
        volume = str(data.get('volume', '+0%')).strip()
        pitch = str(data.get('pitch', '+0Hz')).strip()
        if not text:
            return _error_response('Missing text', 400)
        if not voice:
            return _error_response('Missing voice', 400)
        if len(text) > MAX_TEXT_LENGTH:
            return _error_response(f'Text too long (max {MAX_TEXT_LENGTH})', 400)

        # Resolve voice aliases
        voice = _resolve_voice_alias(voice)

        provider = resolve_provider(voice)
        if not provider:
            return _error_response(f'Unknown voice format: {voice}', 400)

        request._tts_provider = provider
        request._tts_voice = voice
        request._tts_chars = len(text)

        quota_err = _check_daily_quota(client_ip, len(text))
        if quota_err:
            return quota_err

        pct = parse_rate(data.get('rate', '0%'))
        audio, error, actual_provider, actual_voice = dispatch(provider, text, voice, pct, style=style, volume=volume, pitch=pitch)

        if audio:
            # Headers report the provider/voice that actually produced the audio,
            # with X-TTS-Requested-Provider preserving what the client asked for.
            final_provider = actual_provider or provider
            final_voice = actual_voice or voice
            update_stats(len(text), final_provider, voice=final_voice)
            log.info("TTS OK: provider=%s voice=%s style=%s chars=%d size=%d",
                     final_provider, final_voice, style, len(text), len(audio))
            return Response(audio, mimetype='audio/mpeg',
                            headers=_tts_headers(audio, provider, voice,
                                                 final_provider, final_voice, len(text)))
        log.warning("TTS failed: provider=%s voice=%s error=%s", provider, voice, error)
        return _error_response(f'TTS failed: {error}', 500, 'synthesis_error')
    except Exception as e:
        log.error("speech_stream error: %s", e, exc_info=True)
        return _error_response(f'Error: {e}', 500, 'server_error')


@app.route('/speech/stream/chunked', methods=['POST'])
def speech_stream_chunked():
    """True streaming TTS: returns audio chunks as they are generated (Edge TTS only).
    Other providers fall back to full response."""
    try:
        client_ip = request.remote_addr or 'unknown'
        if _check_rate_limit(client_ip):
            return Response('Rate limit exceeded', status=429, headers={'Retry-After': '60'})
        key_err = _check_api_key()
        if key_err:
            return key_err
        data = request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        voice = _sanitize_voice(data.get('voice'))
        if not text or not voice:
            return _error_response('Missing text or voice', 400)
        if len(text) > MAX_TEXT_LENGTH:
            return _error_response(f'Text too long (max {MAX_TEXT_LENGTH})', 400)
        voice = _resolve_voice_alias(voice)
        provider = resolve_provider(voice)
        if not provider:
            return _error_response(f'Unknown voice: {voice}', 400)
        request._tts_provider = provider
        request._tts_voice = voice
        request._tts_chars = len(text)
        # This endpoint charges the daily quota like every other synthesis route
        quota_err = _check_daily_quota(client_ip, len(text))
        if quota_err:
            return quota_err
        pct = parse_rate(data.get('rate', '0%'))
        if provider == 'edge':
            rate = _pct_to_rate(pct)
            clean = _clean_text(text)
            if not clean:
                return _error_response('Text is empty after cleaning', 400)
            chars = len(clean)

            def generate():
                """Yield audio chunks as edge-tts produces them (real streaming).

                Stats are recorded here rather than in after_request because the
                generator body runs after the response has already been returned.
                """
                sent = 0
                try:
                    for chunk_data in _edge_stream_iter(clean, voice, rate):
                        sent += len(chunk_data)
                        yield chunk_data
                except Exception as e:
                    log.error('Chunked stream error: %s', e)
                finally:
                    if sent:
                        update_stats(chars, provider, voice=voice)
            # Do not set Transfer-Encoding by hand: the WSGI server owns the
            # framing and a manual header desynchronizes the response body.
            return Response(generate(), mimetype='audio/mpeg', headers={
                'X-TTS-Provider': provider,
                'X-TTS-Voice': voice,
                'X-TTS-Chars': str(chars),
                'Cache-Control': 'no-store',
            })
        audio, error, actual_provider, actual_voice = dispatch(provider, text, voice, pct)
        if audio:
            final_provider = actual_provider or provider
            final_voice = actual_voice or voice
            update_stats(len(text), final_provider, voice=final_voice)
            return Response(audio, mimetype='audio/mpeg',
                            headers=_tts_headers(audio, provider, voice,
                                                 final_provider, final_voice, len(text)))
        return _error_response(f'TTS failed: {error}', 502, 'synthesis_error')
    except Exception as e:
        log.error('Chunked TTS error: %s', e)
        return _error_response(str(e), 500, 'server_error')


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    err = _check_admin()
    if err:
        return err
    config = load_config()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for key in ['provider', 'default_voice', 'tencent_voice',
                    'edge_voice', 'xiaomi_voice', 'fishaudio_voice', 'cluster',
                    'fishaudio_reference_id']:
            if key in data:
                config[key] = data[key]
        for key in ['appid', 'access_token', 'tencent_secret_id',
                    'tencent_secret_key', 'xiaomi_api_key', 'fishaudio_api_key']:
            if key in data and '***' not in str(data[key]):
                config[key] = data[key]
        # Validate voice selection
        for vkey in ['default_voice', 'tencent_voice', 'edge_voice', 'xiaomi_voice', 'fishaudio_voice']:
            v = data.get(vkey)
            if v and v not in _ALL_VOICE_IDS:
                log.warning("Unknown voice selected: %s=%s", vkey, v)
        save_config(config)
        log.info("Config updated")
        return jsonify({'status': 'ok'})

    # GET: return config with masked secrets + provider status
    safe = config.copy()
    for key in ['access_token', 'tencent_secret_key']:
        if safe.get(key):
            safe[key] = '***'
    for key, show in [('xiaomi_api_key', 6), ('appid', 3)]:
        val = safe.get(key, '')
        if len(val) > show + 3:
            safe[key] = f"{val[:show]}***{val[-3:]}"
    sid = safe.get('tencent_secret_id', '')
    if len(sid) > 10:
        safe['tencent_secret_id'] = f"{sid[:6]}***{sid[-4:]}"
    safe['provider_status'] = {
        'edge': {'ready': True, 'note': '免费使用'},
        'doubao': {'ready': bool(config.get('appid') and config.get('access_token')),
                   'note': '已配置' if config.get('appid') and config.get('access_token') else '未配置'},
        'tencent': {'ready': bool(config.get('tencent_secret_id') and config.get('tencent_secret_key')),
                    'note': '已配置' if config.get('tencent_secret_id') and config.get('tencent_secret_key') else '未配置'},
        'xiaomi': {'ready': bool(config.get('xiaomi_api_key')),
                   'note': '已配置' if config.get('xiaomi_api_key') else '未配置'},
        'fishaudio': {'ready': bool(config.get('fishaudio_api_key')),
                      'note': '已配置' if config.get('fishaudio_api_key') else '未配置'},
    }
    return jsonify(safe)


@app.route('/api/config/test', methods=['POST'])
def api_config_test():
    """Test current provider config by synthesizing a short sample."""
    err = _check_admin()
    if err:
        return err
    config = load_config()
    provider = config.get('provider', 'edge')
    result = {'provider': provider, 'ok': True, 'error': None}
    voice = config.get(f'{provider}_voice') or config.get('default_voice')
    if not voice:
        voice_map = {'edge': EDGE_VOICES[0]['id'], 'doubao': DOUBAO_VOICES[0]['id'],
                     'tencent': TENCENT_VOICES[0]['id'], 'xiaomi': XIAOMI_VOICES[0]['id'],
                     'fishaudio': FISH_AUDIO_VOICES[0]['id']}
        voice = voice_map.get(provider, EDGE_VOICES[0]['id'])
    audio, error, actual_provider, actual_voice = dispatch(provider, '你好，配置测试。', voice, 0)
    if audio:
        result['audio_size'] = len(audio)
        result['voice'] = actual_voice or voice
        result['actual_provider'] = actual_provider or provider
    else:
        result['ok'] = False
        result['error'] = error
    return jsonify(result)


@app.route('/api/config/export', methods=['GET'])
def api_config_export():
    """Export full configuration as JSON download."""
    err = _check_admin()
    if err:
        return err
    config = load_config()
    config['_version'] = __version__
    config['_exported_at'] = datetime.now().isoformat()
    return Response(
        json.dumps(config, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename="tts-config.json"'}
    )


@app.route('/api/config/import', methods=['POST'])
def api_config_import():
    """Import configuration from JSON."""
    err = _check_admin()
    if err:
        return err
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return _error_response('No JSON data', 400, 'invalid_request_error')
    data.pop('_version', None)
    data.pop('_exported_at', None)
    config = DEFAULT_CONFIG.copy()
    unknown = []
    for k, v in data.items():
        if k in config:
            config[k] = v
        else:
            unknown.append(k)
    if unknown:
        log.warning('Ignoring unknown config keys on import: %s', ', '.join(sorted(unknown)))
    save_config(config)
    log.info('Configuration imported')
    return jsonify({'status': 'ok', 'message': 'Configuration imported successfully',
                    'ignored_keys': sorted(unknown)})


@app.route('/api/audit', methods=['GET'])
def api_audit():
    """Return recent TTS request audit log.
    Optional: ?limit=50 (default 50, max AUDIT_LOG_SIZE)"""
    err = _check_admin()
    if err:
        return err
    limit = _int_arg('limit', 50, minimum=1, maximum=AUDIT_LOG_SIZE)
    with _audit_lock:
        # A deque is not sliceable; snapshot then slice. `total` is read here too
        # so it can't disagree with `records` from a concurrent append.
        snapshot = list(_audit_log)
    records = snapshot[-limit:]
    return jsonify({'records': records, 'count': len(records), 'total': len(snapshot)})


@app.route('/api/info', methods=['GET'])
def api_info():
    """Complete system info dashboard - combines health, config, stats, and cache."""
    config = load_config()
    stats = _read_json(STATS_FILE, {})
    uptime_sec = time.time() - _metrics.get('_start_time', time.time())
    with _metrics_lock:
        total_req = _metrics['requests_total']
        success_req = _metrics['requests_success']
        failed_req = _metrics['requests_failed']
        total_chars = _metrics['chars_total']
        cache_hits = _metrics['cache_hits_total']
    return jsonify({
        'version': __version__,
        'uptime_seconds': int(uptime_sec),
        'python_version': platform.python_version(),
        'config': {
            'provider': config.get('provider', 'edge'),
            'ssml_enabled': ALLOW_SSML,
            'fallback_enabled': FALLBACK_TO_EDGE,
            'rate_limit_rpm': RATE_LIMIT_RPM,
            'request_timeout': REQUEST_TIMEOUT,
            'max_text_length': MAX_TEXT_LENGTH,
            'chunk_size': CHUNK_SIZE,
            'pronunciation_dict_size': len(config.get('pronunciation_dict', {})),
        },
        'metrics': {
            'requests_total': total_req,
            'requests_success': success_req,
            'requests_failed': failed_req,
            'success_rate': round(success_req / total_req * 100, 1) if total_req > 0 else 0,
            'chars_total': total_chars,
            'cache_hits_total': cache_hits,
        },
        'cache': _cache_info(),
        'providers': ALL_PROVIDERS,
        'provider_status': {
            'edge': {'configured': True, 'note': '免费，无需配置'},
            'doubao': {'configured': bool(config.get('access_token')), 'note': '需要access_token'},
            'tencent': {'configured': bool(config.get('tencent_secret_id')), 'note': '需要secret_id+key'},
            'xiaomi': {'configured': bool(config.get('xiaomi_api_key')), 'note': '需要api_key'},
            'fishaudio': {'configured': bool(config.get('fishaudio_api_key')), 'note': '需要api_key'},
        },
        'admin_protected': bool(ADMIN_TOKEN),
        'ffmpeg_available': _FFMPEG_AVAILABLE,
    })


@app.route('/api/openapi.json', methods=['GET'])
def api_openapi():
    """Minimal OpenAPI 3.0 spec for the TTS API."""
    spec = {
        'openapi': '3.0.0',
        'info': {'title': 'Legado TTS Server', 'version': __version__,
                 'description': '开源阅读 TTS 聚合服务 API'},
        'paths': {
            '/speech/stream': {
                'post': {'summary': 'Legado TTS 合成', 'tags': ['TTS'],
                         'requestBody': {'content': {'application/json': {'schema': {
                             'type': 'object',
                             'properties': {
                                 'text': {'type': 'string', 'description': '合成文本'},
                                 'voice': {'type': 'string', 'description': '音色ID或别名'},
                                 'rate': {'type': 'string', 'description': '语速百分比', 'default': '0%'},
                             },
                             'required': ['text', 'voice']
                         }}}},
                         'responses': {'200': {'description': '音频流', 'content': {'audio/mpeg': {}}}}}
            },
            '/v1/audio/speech': {
                'post': {'summary': 'OpenAI 兼容 TTS', 'tags': ['TTS'],
                         'requestBody': {'content': {'application/json': {'schema': {
                             'type': 'object',
                             'properties': {
                                 'model': {'type': 'string', 'default': 'tts-1'},
                                 'input': {'type': 'string'},
                                 'voice': {'type': 'string'},
                                 'speed': {'type': 'number', 'default': 1.0},
                                 'response_format': {'type': 'string', 'enum': ['mp3', 'wav', 'ogg', 'aac', 'flac', 'pcm', 'opus']},
                                 'stream_format': {'type': 'string', 'enum': ['audio', 'sse']},
                                 'instructions': {'type': 'string', 'description': 'Style hint (Edge only)'},
                             },
                             'required': ['input', 'voice']
                         }}}},
                         'responses': {'200': {'description': '音频数据'}}}
            },
            '/health': {'get': {'summary': '健康检查', 'tags': ['系统'],
                                'responses': {'200': {'description': '服务状态'}}}},
            '/metrics': {'get': {'summary': 'Prometheus 指标', 'tags': ['系统'],
                                 'responses': {'200': {'description': 'Prometheus text format'}}}},
            '/api/info': {'get': {'summary': '系统信息总览', 'tags': ['系统'],
                                  'responses': {'200': {'description': '完整系统状态'}}}},
        }
    }
    return jsonify(spec)


# SSE event subscribers
_sse_subscribers = []  # list of queue.Queue
_sse_lock = threading.Lock()
# Each subscriber pins a worker thread for the life of the connection, so the
# count has to be bounded or a handful of open connections starves the pool.
SSE_MAX_SUBSCRIBERS = int(os.environ.get('SSE_MAX_SUBSCRIBERS', '20'))


def _sse_publish(event_type, data):
    """Publish an event to all SSE subscribers."""
    with _sse_lock:
        if not _sse_subscribers:
            return
        msg = json.dumps({'type': event_type, **data}, ensure_ascii=False)
        dead = []
        for q in _sse_subscribers:
            try:
                q.put_nowait(msg)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)


@app.route('/api/events')
def api_events():
    """SSE stream of real-time TTS events.

    Admin-only: each event carries the client IP, voice, character count and
    request id — the same records /api/audit serves, and that route is already
    admin-gated. A live push feed of it shouldn't be anonymously readable.
    """
    err = _check_admin()
    if err:
        return err
    q = queue.Queue(maxsize=100)
    with _sse_lock:
        if len(_sse_subscribers) >= SSE_MAX_SUBSCRIBERS:
            return _error_response(
                f'Too many event subscribers (max {SSE_MAX_SUBSCRIBERS})',
                503, 'server_error', headers={'Retry-After': '30'})
        _sse_subscribers.append(q)

    def stream():
        try:
            yield 'data: {"type":"connected"}\n\n'
            while True:
                try:
                    msg = q.get(timeout=30)
                    if msg is None:  # shutdown signal
                        break
                    yield f'data: {msg}\n\n'
                except queue.Empty:
                    yield ': keepalive\n\n'  # SSE comment as heartbeat
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in _sse_subscribers:
                    _sse_subscribers.remove(q)

    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/pronunciation', methods=['GET', 'POST', 'DELETE'])
def api_pronunciation():
    """Manage custom pronunciation dictionary.

    GET  - List all entries
    POST - Add/update entries: {"entries": {"word": "replacement", ...}}
    DELETE - Delete entries: {"words": ["word1", "word2"]}
    """
    err = _check_admin()
    if err:
        return err
    config = load_config()
    pdict = config.get('pronunciation_dict', {})
    if not isinstance(pdict, dict):
        pdict = {}

    if request.method == 'GET':
        return jsonify({'entries': pdict, 'count': len(pdict)})

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        entries = data.get('entries', {})
        if not isinstance(entries, dict):
            return _error_response('entries must be a dict', 400)
        for word, replacement in entries.items():
            if isinstance(word, str) and isinstance(replacement, str) and word:
                pdict[word] = replacement
        config['pronunciation_dict'] = pdict
        save_config(config)
        return jsonify({'status': 'ok', 'count': len(pdict)})

    if request.method == 'DELETE':
        data = request.get_json(silent=True) or {}
        words = data.get('words', [])
        if not isinstance(words, list):
            return _error_response('words must be a list', 400)
        for w in words:
            pdict.pop(w, None)
        config['pronunciation_dict'] = pdict
        save_config(config)
        return jsonify({'status': 'ok', 'count': len(pdict)})

    return _error_response('Method not allowed', 405, 'method_not_allowed')


@app.route('/api/favorites', methods=['GET', 'POST', 'DELETE'])
def api_favorites():
    """Manage voice favorites. GET=list, POST=add, DELETE=remove."""
    err = _check_admin()
    if err:
        return err
    config = load_config()
    favs = config.get('voice_favorites', [])
    if not isinstance(favs, list):
        favs = []
    if request.method == 'GET':
        return jsonify({'favorites': favs, 'count': len(favs)})
    data = request.get_json(silent=True) or {}
    voice_id = str(data.get('voice', '')).strip()
    if not voice_id:
        return _error_response('Missing voice parameter', 400)
    if request.method == 'POST':
        if voice_id not in favs:
            favs.append(voice_id)
            if len(favs) > 50:
                favs = favs[-50:]
        config['voice_favorites'] = favs
        save_config(config)
        return jsonify({'status': 'ok', 'favorites': favs, 'count': len(favs)})
    # DELETE
    if voice_id in favs:
        favs.remove(voice_id)
    config['voice_favorites'] = favs
    save_config(config)
    return jsonify({'status': 'ok', 'favorites': favs, 'count': len(favs)})


@app.route('/api/stats', methods=['GET', 'DELETE'])
def api_stats():
    if request.method == 'DELETE':
        err = _check_admin()
        if err:
            return err
        _write_json(STATS_FILE, {p: _new_provider_stats() for p in ALL_PROVIDERS})
        log.info("Stats reset")
        return jsonify({'status': 'ok', 'message': '统计已重置'})
    return jsonify(load_stats())


@app.route('/api/stats/summary', methods=['GET'])
def api_stats_summary():
    """High-level stats summary with top voices per provider."""
    stats = load_stats()
    summary = []
    for prov in ALL_PROVIDERS:
        ps = stats.get(prov, {})
        top_voices = []
        voice_stats = ps.get('voice_stats', {})
        if voice_stats:
            sorted_v = sorted(voice_stats.items(), key=lambda x: x[1].get('requests', 0), reverse=True)
            top_voices = [{'voice': v, 'requests': s.get('requests', 0), 'chars': s.get('chars', 0)} for v, s in sorted_v[:5]]
        summary.append({
            'provider': prov,
            'total_requests': ps.get('total_requests', 0),
            'total_chars': ps.get('total_chars', 0),
            'top_voices': top_voices,
        })
    return jsonify({'providers': summary})


@app.route('/api/voices', methods=['GET'])
def api_voices():
    p = request.args.get('provider', 'edge')
    return jsonify({
        'tencent': TENCENT_VOICES, 'doubao': DOUBAO_VOICES,
        'xiaomi': XIAOMI_VOICES, 'edge': EDGE_VOICES,
        'fishaudio': FISH_AUDIO_VOICES,
    }.get(p, EDGE_VOICES))


@app.route('/api/voices/all', methods=['GET'])
def api_voices_all():
    """Return all voices grouped by provider."""
    return jsonify({
        'edge': EDGE_VOICES,
        'doubao': DOUBAO_VOICES,
        'tencent': TENCENT_VOICES,
        'xiaomi': XIAOMI_VOICES,
        'fishaudio': FISH_AUDIO_VOICES,
    })


@app.route('/api/voices/edge/live', methods=['GET'])
def api_voices_edge_live():
    """Fetch live Edge TTS voice list from Microsoft.
    Optional query: ?locale=zh-CN to filter by language."""
    try:
        locale_filter = request.args.get('locale', '').strip()
        voices = _edge_live_voices()
        if locale_filter:
            voices = [v for v in voices if v.get('Locale', '').startswith(locale_filter)]
        result = [{
            'id': v.get('ShortName', ''),
            'name': v.get('FriendlyName', v.get('ShortName', '')),
            'locale': v.get('Locale', ''),
            'gender': v.get('Gender', ''),
        } for v in voices]
        return jsonify(result)
    except Exception as e:
        log.error("Failed to list Edge voices: %s", e)
        return jsonify({'error': str(e)}), 500


@app.route('/api/voices/search', methods=['GET'])
def api_voices_search():
    """Search voices by keyword across all providers.
    ?q=keyword  - search in voice name or ID
    ?provider=edge  - optional filter by provider"""
    q = request.args.get('q', '').strip().lower()
    provider = request.args.get('provider', '').strip().lower()
    if not q:
        return _error_response('Missing search query (q parameter)', 400)
    all_voices = []
    catalogs = {'edge': EDGE_VOICES, 'doubao': DOUBAO_VOICES, 'tencent': TENCENT_VOICES,
                'xiaomi': XIAOMI_VOICES, 'fishaudio': FISH_AUDIO_VOICES}
    for prov, voices in catalogs.items():
        if provider and prov != provider:
            continue
        for v in voices:
            if q in v['id'].lower() or q in v['name'].lower():
                all_voices.append({**v, 'provider': prov})
    return jsonify({'results': all_voices, 'count': len(all_voices)})


@app.route('/api/tts/preview', methods=['POST'])
def api_tts_preview():
    """Preview a voice with a fixed short text. For quick voice comparison.
    POST {"voice": "zh-CN-XiaoxiaoNeural", "provider": "edge"}
    Returns: {"audio": "base64", "size": 1234, "duration_estimate_ms": 2000}"""
    # This route performs real synthesis, so it is rate limited like the other
    # TTS endpoints rather than being a free, unmetered path.
    client_ip = request.remote_addr or 'unknown'
    if _check_rate_limit(client_ip):
        return _error_response('Rate limit exceeded', 429, 'rate_limit_error')
    key_err = _check_api_key()
    if key_err:
        return key_err
    data = request.get_json(silent=True) or {}
    voice = _sanitize_voice(data.get('voice'))
    if not voice:
        return _error_response('Missing voice', 400)
    # Resolve aliases
    voice = _resolve_voice_alias(voice)
    provider = data.get('provider', '').strip().lower()
    if provider and provider not in ALL_PROVIDERS:
        return _error_response(f'Unknown provider: {provider}', 400)
    if not provider:
        provider = resolve_provider(voice)
    if not provider:
        return _error_response(f'Cannot determine provider for voice: {voice}', 400)
    preview_text = '你好，我是您的语音助手，很高兴认识您。'
    audio, error, actual_provider, actual_voice = dispatch(provider, preview_text, voice, 0)
    if audio:
        final_provider = actual_provider or provider
        final_voice = actual_voice or voice
        update_stats(len(preview_text), final_provider, voice=final_voice)
        # Rough duration estimate: MP3 at ~48kbps
        duration_ms = int(len(audio) / 48 * 1000 / 8) if len(audio) > 0 else 0
        return jsonify({
            'audio': base64.b64encode(audio).decode(),
            'size': len(audio),
            'voice': final_voice,
            'provider': final_provider,
            'duration_estimate_ms': duration_ms,
        })
    return _error_response(f'Preview failed: {error}', 500)


@app.route('/api/cache/stats', methods=['GET'])
def api_cache_stats():
    return jsonify(_cache_info())


@app.route('/api/cache/clear', methods=['DELETE'])
def api_cache_clear():
    err = _check_admin()
    if err:
        return err
    _cache_clear()
    log.info('Audio cache cleared')
    return jsonify({'status': 'ok'})


# ──────────────────────────────────────────────
# OpenAI-compatible TTS API
# ──────────────────────────────────────────────

@app.route('/v1/audio/speech', methods=['POST'])
def openai_speech():
    """OpenAI-compatible TTS endpoint.

    POST /v1/audio/speech
    {
        "model": "tts-1",               // ignored, provider auto-detected from voice
        "input": "Hello world.",
        "voice": "zh-CN-XiaoxiaoNeural",
        "response_format": "mp3",        // mp3|wav|ogg|aac|flac|pcm|opus (non-mp3 requires ffmpeg)
        "speed": 1.0,                    // 0.25-4.0
        "stream_format": "audio",        // audio (default) | sse (Server-Sent Events streaming)
        "instructions": "speak slowly"   // optional: prepended to text as style hint (Edge only)
    }
    """
    try:
        client_ip = request.remote_addr or 'unknown'
        if _check_rate_limit(client_ip):
            return _error_response('Rate limit exceeded', 429, 'rate_limit_error',
                                   headers={'Retry-After': '60'})
        key_err = _check_api_key()
        if key_err:
            return key_err

        data = request.get_json(silent=True) or {}
        text = str(data.get('input', '')).strip()
        voice = _sanitize_voice(data.get('voice'))
        resp_format = str(data.get('response_format', 'mp3')).strip().lower()
        if resp_format not in _FORMAT_MIME:
            resp_format = 'mp3'
        stream_format = str(data.get('stream_format', 'audio')).strip().lower()
        instructions = str(data.get('instructions', '')).strip()
        try:
            speed = float(data.get('speed', 1.0))
        except (ValueError, TypeError):
            speed = 1.0
        speed = max(0.25, min(4.0, speed))

        if not text:
            return _error_response('Missing input', 400, 'invalid_request_error')
        if not voice:
            return _error_response('Missing voice', 400, 'invalid_request_error')
        if len(text) > MAX_TEXT_LENGTH:
            return _error_response(f'Input too long (max {MAX_TEXT_LENGTH})',
                                   400, 'invalid_request_error')

        # Try to resolve voice by name if not a known ID
        voice = _resolve_voice_alias(voice)

        provider = resolve_provider(voice)
        if not provider:
            return _error_response(f'Unknown voice: {voice}', 400, 'invalid_request_error')

        request._tts_provider = provider
        request._tts_voice = voice
        request._tts_chars = len(text)

        # Check daily quota
        quota_err = _check_daily_quota(client_ip, len(text))
        if quota_err:
            return quota_err

        # Apply instructions as style hint (currently Edge TTS only)
        style = None
        if instructions and provider == 'edge':
            style = instructions[:100]  # cap length to avoid abuse

        # Convert speed (0.25-4.0) to percentage (-75% to +300%)
        pct = (speed - 1.0) * 100

        # SSE streaming mode (Edge TTS only; fallback to buffered for other providers)
        if stream_format == 'sse' and provider == 'edge':
            clean_text = _clean_text(text)
            if not clean_text:
                return _error_response('Text is empty after cleaning', 400, 'invalid_request_error')

            def _sse_generate():
                sent = 0
                try:
                    # Chunks arrive incrementally from upstream; each is forwarded as
                    # soon as it lands rather than after the whole synthesis completes.
                    for chunk in _edge_stream_iter(clean_text, voice, rate=_pct_to_rate(pct),
                                                   style=style):
                        sent += len(chunk)
                        b64 = base64.b64encode(chunk).decode()
                        yield f'data: {json.dumps({"type": "audio_chunk", "data": b64})}\n\n'
                    yield f'data: {json.dumps({"type": "done"})}\n\n'
                except Exception as e:
                    log.error('SSE stream error: %s', e)
                    yield f'data: {json.dumps({"type": "error", "message": str(e)})}\n\n'
                finally:
                    if sent:
                        update_stats(len(clean_text), provider, voice=voice)

            return Response(_sse_generate(), mimetype='text/event-stream',
                            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
                                     'X-TTS-Provider': provider, 'X-TTS-Voice': voice})

        audio, error, actual_provider, actual_voice = dispatch(provider, text, voice, pct, style=style)

        if audio:
            final_provider = actual_provider or provider
            final_voice = actual_voice or voice
            update_stats(len(text), final_provider, voice=final_voice)
            # actual_format may differ from resp_format if ffmpeg is unavailable
            # or the conversion failed; report what we really produced.
            audio, actual_format = _convert_audio(audio, resp_format)
            mime = _FORMAT_MIME.get(actual_format, 'audio/mpeg')
            log.info("OpenAI TTS OK: provider=%s voice=%s chars=%d size=%d fmt=%s",
                     final_provider, final_voice, len(text), len(audio), actual_format)
            return Response(audio, mimetype=mime, headers={
                'Content-Length': str(len(audio)),
                'Content-Type': mime,
                'X-TTS-Provider': final_provider,
                'X-TTS-Voice': final_voice,
                'X-TTS-Format': actual_format,
            })
        return _error_response(f'TTS failed: {error}', 500, 'server_error')
    except Exception as e:
        log.error("openai_speech error: %s", e, exc_info=True)
        return _error_response(str(e), 500, 'server_error')


@app.route('/v1/models', methods=['GET'])
def openai_models():
    """OpenAI-compatible models list."""
    return jsonify({
        'object': 'list',
        'data': [
            {'id': 'tts-1', 'object': 'model', 'owned_by': 'legado-tts-server'},
            {'id': 'tts-1-hd', 'object': 'model', 'owned_by': 'legado-tts-server'},
        ]
    })


@app.route('/api/legado/config', methods=['GET'])
def api_legado_config():
    """Generate Legado-compatible TTS engine configuration JSON.

    Query params:
        voice  - voice ID (default: current config)
        server - server URL (default: auto-detect from request)
    """
    config = load_config()
    voice = request.args.get('voice', config.get('default_voice', 'zh-CN-XiaoxiaoNeural')).strip()
    server = request.args.get('server', '').strip()
    if not server:
        # Auto-detect from request
        server = request.host_url.rstrip('/')
    rate = request.args.get('rate', '+0%').strip()
    legado_url = (server + '/speech/stream,{"method":"POST","body":{"text":"{{speakText}}","voice":"' +
                  voice + '","rate":"{{String(speakSpeed)}}' + rate + '"},"headers":{"Content-Type":"application/json"}}')
    legado_cfg = {
        "concurrentRate": "0",
        "contentType": "audio/mpeg",
        "name": f"TTS-{voice.split('-')[-1]}",
        "url": legado_url
    }
    return jsonify(legado_cfg)


@app.route('/api/legado/subscribe', methods=['GET'])
def api_legado_subscribe():
    """Generate a Legado subscription URL (base64-encoded config JSON).

    Usage: Import this URL directly into Legado as a speech engine.
    """
    config = load_config()
    voice = request.args.get('voice', config.get('default_voice', 'zh-CN-XiaoxiaoNeural')).strip()
    server = request.args.get('server', '').strip()
    if not server:
        server = request.host_url.rstrip('/')
    rate = request.args.get('rate', '+0%').strip()
    legado_cfg = {
        "concurrentRate": "0",
        "contentType": "audio/mpeg",
        "name": f"TTS-{voice.split('-')[-1]}",
        "url": (server + '/speech/stream,{"method":"POST","body":{"text":"{{speakText}}","voice":"' +
                voice + '","rate":"{{String(speakSpeed)}}' + rate + '"},"headers":{"Content-Type":"application/json"}}')
    }
    encoded = base64.b64encode(json.dumps(legado_cfg, ensure_ascii=False).encode()).decode()
    subscribe_url = f"{server}/api/legado/subscribe?voice={voice}&auto=true"
    if request.args.get('auto') == 'true':
        return Response(encoded, mimetype='text/plain')
    return jsonify({'url': subscribe_url, 'config': legado_cfg, 'encoded': encoded})


@app.route('/')
@gzipped
def index():
    config = load_config()
    def _mask(val, prefix=3, suffix=3):
        if len(val) > prefix + suffix:
            return f"{val[:prefix]}***{val[-suffix:]}"
        return val
    return render_template_string(HTML_TEMPLATE,
        server_ip=request.host.split(':')[0],
        provider=config.get('provider', 'edge'),
        has_token=bool(config.get('access_token')),
        has_tencent_key=bool(config.get('tencent_secret_key')),
        has_xiaomi_key=bool(config.get('xiaomi_api_key')),
        default_voice=config.get('default_voice'),
        tencent_voice=config.get('tencent_voice'),
        edge_voice=config.get('edge_voice'),
        xiaomi_voice=config.get('xiaomi_voice'),
        appid=_mask(config.get('appid', '')),
        tencent_secret_id=_mask(config.get('tencent_secret_id', ''), 6, 4),
        xiaomi_api_key=_mask(config.get('xiaomi_api_key', ''), 6, 4),
        fishaudio_api_key=_mask(config.get('fishaudio_api_key', ''), 6, 4),
        fishaudio_reference_id=config.get('fishaudio_reference_id', ''),
        admin_protected=bool(ADMIN_TOKEN),
    )


# ──────────────────────────────────────────────
# HTML Template
# ──────────────────────────────────────────────

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TTS 服务</title>
    <style>
        :root{--primary:#007bff;--bg:#f7f8fa;--card:#fff;--text:#333;--border:#ddd;--danger:#dc3545;--success:#28a745}
        [data-theme=dark]{--primary:#4dabf7;--bg:#1a1a2e;--card:#16213e;--text:#e0e0e0;--border:#2a2a4a;--danger:#ff6b6b;--success:#51cf66}
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
        .container{max-width:800px;margin:20px auto;padding:0 15px}
        .card{background:var(--card);border-radius:12px;padding:24px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,.08)}
        h1{text-align:center;font-size:28px;margin-bottom:16px}
        h2{font-size:20px;margin-bottom:16px}
        .row{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
        .row h2{margin:0}
        .provider-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}
        .provider-btn{padding:12px;border:2px solid var(--border);border-radius:8px;cursor:pointer;text-align:center;background:var(--card);transition:all .2s}
        .provider-btn:hover{border-color:#aaa}
        .provider-btn.active{border-color:var(--primary);background:#e7f1ff;box-shadow:0 0 0 2px rgba(0,123,255,.2)}
        [data-theme=dark] .provider-btn.active{background:#1a365d}
        .provider-btn strong{display:block;font-size:16px}
        .badge{display:inline-block;padding:3px 10px;border-radius:12px;font-size:12px;font-weight:500;margin-top:6px}
        .badge-ok{background:#d4edda;color:#155724}
        .badge-err{background:#f8d7da;color:#721c24}
        .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px}
        .stat{text-align:center}
        .stat-val{font-size:26px;font-weight:700;color:var(--primary)}
        .stat-lbl{font-size:14px;color:#666}
        .field{margin-bottom:16px}
        .field label{display:block;margin-bottom:6px;font-weight:500}
        .field input,.field select{width:100%;padding:10px 12px;border:1px solid var(--border);border-radius:6px;font-size:14px}
        .btn{padding:10px 20px;border:none;border-radius:6px;cursor:pointer;font-size:14px;color:#fff;transition:background .2s}
        .btn-primary{background:var(--primary)}.btn-primary:hover{background:#0056b3}
        .btn-success{background:var(--success)}.btn-success:hover{background:#218838}
        .btn-danger{background:var(--danger)}.btn-danger:hover{background:#c82333}
        .btn:disabled{background:#999;cursor:not-allowed}
        .code{background:#2d2d2d;color:#f8f8f2;padding:16px;border-radius:8px;font-family:"SF Mono","Fira Code",monospace;font-size:13px;white-space:pre-wrap;word-break:break-all}
        .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);padding:12px 24px;background:rgba(0,0,0,.8);color:#fff;border-radius:8px;display:none;z-index:1000}
        @media(max-width:600px){.provider-grid{grid-template-columns:repeat(2,1fr)}.container{padding:0 10px;margin:10px auto}.card{padding:16px}h1{font-size:22px}}
    </style>
</head>
<body>
<div class="container">
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-bottom:8px">{% if admin_protected %}<button onclick="setAdminToken()" style="background:var(--card);border:1px solid var(--border);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:14px" id="token-btn" title="设置管理令牌">🔑 令牌</button>{% endif %}<button onclick="toggleTheme()" style="background:var(--card);border:1px solid var(--border);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:14px" id="theme-btn">🌙 暗色</button></div>
    <div class="card">
        <h2>服务商选择</h2>
        <div class="provider-grid">
            <div class="provider-btn" data-provider="edge" onclick="setProvider('edge')"><strong>Edge</strong><div class="badge badge-ok">免费</div></div>
            <div class="provider-btn" data-provider="doubao" onclick="setProvider('doubao')"><strong>火山引擎</strong><div class="badge" id="doubao-status"></div></div>
            <div class="provider-btn" data-provider="tencent" onclick="setProvider('tencent')"><strong>腾讯云</strong><div class="badge" id="tencent-status"></div></div>
            <div class="provider-btn" data-provider="xiaomi" onclick="setProvider('xiaomi')"><strong>小米MiMo</strong><div class="badge" id="xiaomi-status"></div></div>
            <div class="provider-btn" data-provider="fishaudio" onclick="setProvider('fishaudio')"><strong>Fish Audio</strong><div class="badge" id="fishaudio-status"></div></div>
        </div>
    </div>
    <div class="card">
        <div class="row"><h2>使用统计</h2><button class="btn btn-danger" style="font-size:12px;padding:6px 14px" onclick="resetStats()">重置统计</button></div>
        <div class="stats">
            <div class="stat"><div class="stat-val" id="total-chars">-</div><div class="stat-lbl">总字符</div></div>
            <div class="stat"><div class="stat-val" id="total-requests">-</div><div class="stat-lbl">总请求</div></div>
            <div class="stat"><div class="stat-val" id="today-chars">-</div><div class="stat-lbl">今日字符</div></div>
            <div class="stat"><div class="stat-val" id="today-requests">-</div><div class="stat-lbl">今日请求</div></div>
        </div>
    </div>
    <div class="card">
        <div class="row"><h2>开源阅读配置</h2><div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap"><label for="voice-search" style="font-weight:500">音色</label><input id="voice-search" placeholder="搜索音色..." oninput="filterVoices()" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;font-size:14px;width:120px"><select id="voice-select" onchange="updateLegadoConfig()" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;font-size:14px"></select><button class="btn" onclick="previewVoice()" style="padding:4px 10px;font-size:12px">▶ 试听</button><button class="btn" onclick="showAllVoices()" style="padding:4px 10px;font-size:12px">🎵 全部试听</button></div></div>
        <p style="color:#666;margin-bottom:12px">复制以下配置到开源阅读的朗读引擎，即可使用上方选择的音色。</p>
        <div class="code" id="legado-config"></div>
        <div class="card" style="margin-top:16px"><h2>音色对比</h2><p style="font-size:13px;color:#666;margin:0 0 8px">输入文本，对比两个音色的效果</p><textarea id="compare-text" placeholder="输入对比文本..." style="width:100%;height:60px;padding:8px;border:1px solid var(--border);border-radius:6px;font-size:14px;resize:vertical;box-sizing:border-box">今天天气真好，我们一起出去散步吧。</textarea><div style="display:flex;gap:12px;margin-top:8px;align-items:center;flex-wrap:wrap"><div style="flex:1;min-width:200px"><label style="font-size:12px;color:#666">音色 A</label><select id="compare-a" style="width:100%;padding:6px;border:1px solid var(--border);border-radius:6px;font-size:14px;margin-top:2px"></select><audio id="audio-a" controls style="width:100%;margin-top:6px"></audio></div><div style="flex:1;min-width:200px"><label style="font-size:12px;color:#666">音色 B</label><select id="compare-b" style="width:100%;padding:6px;border:1px solid var(--border);border-radius:6px;font-size:14px;margin-top:2px"></select><audio id="audio-b" controls style="width:100%;margin-top:6px"></audio></div><button class="btn" id="compare-btn" onclick="compareVoices()" style="padding:6px 16px;align-self:flex-end">对比播放</button></div></div>
        <div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">
            <button class="btn btn-primary" onclick="copyConfig()">复制配置</button>
            <button class="btn" onclick="copySubscribeUrl()">复制订阅链接</button>
        </div>
    </div>
    <div class="card">
        <h2>系统状态</h2>
        <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px;">
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">总合成字符</div>
                <div style="font-size:20px; font-weight:600" id="total-chars-all">0</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">总请求数</div>
                <div style="font-size:20px; font-weight:600" id="total-requests-all">0</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">缓存命中率</div>
                <div style="font-size:20px; font-weight:600" id="cache-hit-rate">0%</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">P95响应时间</div>
                <div style="font-size:20px; font-weight:600" id="response-time-p95">0ms</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">缓存条目</div>
                <div style="font-size:20px; font-weight:600" id="cache-count">0</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">缓存内存</div>
                <div style="font-size:20px; font-weight:600" id="cache-memory">0MB</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">FFmpeg支持</div>
                <div style="font-size:20px; font-weight:600; color: var(--success)" id="ffmpeg-status">✓</div>
            </div>
            <div style="background:#f8f9fa; padding:12px; border-radius:8px">
                <div style="color:#666; font-size:12px; margin-bottom:4px">Admin保护</div>
                <div style="font-size:20px; font-weight:600" id="admin-status">✗</div>
            </div>
        </div>
    </div>
    <div class="card">
        <h2>API 设置</h2>
        <div id="doubao-settings" style="display:none"><div class="field"><label>App ID</label><input type="text" id="appid" value="{{ appid }}"></div><div class="field"><label>Access Token</label><input type="password" id="access-token" placeholder=""></div></div>
        <div id="tencent-settings" style="display:none"><div class="field"><label>SecretId</label><input type="text" id="tencent-secret-id" value="{{ tencent_secret_id }}"></div><div class="field"><label>SecretKey</label><input type="password" id="tencent-secret-key" placeholder=""></div></div>
        <div id="xiaomi-settings" style="display:none"><div class="field"><label>API Key</label><input type="password" id="xiaomi-api-key" value="{{ xiaomi_api_key }}" placeholder="请输入小米MiMo API Key"></div></div>
        <div id="fishaudio-settings" style="display:none"><div class="field"><label>API Key</label><input type="password" id="fishaudio-api-key" value="{{ fishaudio_api_key }}" placeholder="请输入Fish Audio API Key"></div><div class="field"><label>自定义音色Reference ID</label><input type="text" id="fishaudio-reference-id" value="{{ fishaudio_reference_id }}" placeholder="可选，留空使用预设音色"></div></div>
        <div style="display:flex;gap:8px;align-items:center">
            <button class="btn btn-primary" id="save-btn" onclick="saveConfig()" style="display:none">保存设置</button>
            <button class="btn btn-success" id="test-cfg-btn" onclick="testConfig()" style="display:none">测试连接</button>
            <button class="btn" onclick="exportConfig()" style="font-size:12px">导出配置</button>
            <label class="btn" style="font-size:12px;cursor:pointer">导入配置<input type="file" accept=".json" onchange="importConfig(event)" style="display:none"></label>
            <span id="api-note" style="color:#666;font-size:12px;display:none"></span>
        </div>
    </div>
    <div class="card">
        <h2>测试</h2>
        <div class="field"><input type="text" id="test-text" value="你好，这是一段测试语音。" style="margin-bottom:12px"></div>
        <button class="btn btn-primary" id="test-btn" onclick="testTTS()">播放测试</button>
        <audio id="audio-player" style="margin-top:12px;width:100%"></audio>
    </div>
</div>
<div class="toast" id="toast"></div>
<script>
document.addEventListener('DOMContentLoaded',()=>{
    const IP='{{ server_ip }}',cur='{{ provider }}';
    const dv={doubao:'{{ default_voice }}',tencent:'{{ tencent_voice }}',edge:'{{ edge_voice }}',xiaomi:'{{ xiaomi_voice }}'};
    let stats={},prov=cur,_allVoiceOptions=[];
    const $=id=>document.getElementById(id);

    // ── Authenticated fetch ──
    // When ADMIN_TOKEN is set, every admin route requires a bearer token. The UI
    // keeps it in localStorage and injects it here, so no individual call site
    // has to remember. api() also surfaces a 401 as a prompt for the token
    // rather than letting the whole page silently fail.
    const AUTH_REQUIRED={{ 'true' if admin_protected else 'false' }};
    const getToken=()=>localStorage.getItem('tts-admin-token')||'';
    window.setAdminToken=()=>{const t=prompt('请输入管理令牌 (ADMIN_TOKEN)',getToken());if(t===null)return;localStorage.setItem('tts-admin-token',t.trim());toast('令牌已保存，正在刷新...');setTimeout(()=>location.reload(),800)};
    let _authPrompted=false;
    const api=async(url,opts)=>{
        opts=opts||{};
        const t=getToken();
        if(t){opts.headers=Object.assign({},opts.headers,{'Authorization':'Bearer '+t})}
        const r=await fetch(url,opts);
        if(r.status===401&&!_authPrompted){_authPrompted=true;toast('需要管理令牌');setAdminToken()}
        return r;
    };
    if(AUTH_REQUIRED&&!getToken()){setTimeout(()=>{toast('本服务已启用令牌保护，请设置管理令牌');setAdminToken()},500)}

    // Every <audio> element owns at most one blob URL; setting a new one revokes
    // the old, so repeated previews/comparisons don't leak objects for the
    // lifetime of the page.
    const setAudioBlob=(el,blob)=>{if(el._u)URL.revokeObjectURL(el._u);el._u=URL.createObjectURL(blob);el.src=el._u};
    // Errors come back as standardized JSON; fall back to raw text.
    const _errText=async r=>{try{const d=await r.clone().json();return (d.error&&d.error.message)||r.statusText}catch(e){try{return await r.text()}catch(e2){return r.statusText}}};
    // Theme toggle
    window.toggleTheme=()=>{const d=document.documentElement;const dark=d.getAttribute('data-theme')==='dark';d.setAttribute('data-theme',dark?'light':'dark');$('theme-btn').textContent=dark?'🌙 暗色':'☀️ 亮色';localStorage.setItem('tts-theme',dark?'light':'dark')};
    const saved=localStorage.getItem('tts-theme');if(saved==='dark'){document.documentElement.setAttribute('data-theme','dark');$('theme-btn').textContent='☀️ 亮色'}
    const setStatus=(el,ok)=>{el.textContent=ok?'已配置':'未配置';el.className='badge '+(ok?'badge-ok':'badge-err')};

    const loadStats=async()=>{try{const r=await api('/api/stats');stats=await r.json();showStats()}catch(e){}};
    const showStats=()=>{const d=stats[prov]||{};$('total-chars').textContent=(d.total_chars||0).toLocaleString();$('total-requests').textContent=(d.total_requests||0).toLocaleString();const t=new Date().toISOString().split('T')[0];const td=(d.history||[]).find(h=>h.date===t)||{};$('today-chars').textContent=(td.chars||0).toLocaleString();$('today-requests').textContent=(td.requests||0).toLocaleString();};

    const loadSystemStatus=async()=>{
        try{
            const r=await api('/health');
            const h=await r.json();
            // 更新系统状态
            const cache=h.cache||{};
            $('cache-count').textContent=cache.count||0;
            $('cache-memory').textContent=((cache.bytes||0)/(1024*1024)).toFixed(1)+'MB';
            $('ffmpeg-status').textContent=h.ffmpeg_available?'✓':'✗';
            $('ffmpeg-status').style.color=h.ffmpeg_available?'var(--success)':'var(--danger)';
            $('admin-status').textContent=h.admin_protected?'✓':'✗';
            $('admin-status').style.color=h.admin_protected?'var(--success)':'var(--danger)';
            // 加载metrics
            const mr=await api('/metrics');
            const mt=await mr.text();
            let totalChars=0,totalRequests=0,cacheHitRate=0,rtP95=0;
            for(const line of mt.split('\\n')){
                if(line.startsWith('tts_chars_total ')) totalChars=parseInt(line.split(' ')[1])||0;
                if(line.startsWith('tts_requests_total ')) totalRequests=parseInt(line.split(' ')[1])||0;
                if(line.startsWith('tts_cache_hit_ratio ')) cacheHitRate=parseFloat(line.split(' ')[1])||0;
                if(line.startsWith('tts_response_time_ms_p95 ')) rtP95=parseFloat(line.split(' ')[1])||0;
            }
            $('total-chars-all').textContent=totalChars.toLocaleString();
            $('total-requests-all').textContent=totalRequests.toLocaleString();
            $('cache-hit-rate').textContent=(cacheHitRate*100).toFixed(1)+'%';
            $('response-time-p95').textContent=Math.round(rtP95)+'ms';
        }catch(e){}
    };

    const loadVoices=async p=>{try{const r=await api('/api/voices?provider='+p);const vs=await r.json();_allVoiceOptions=vs;const sel=$('voice-select');sel.innerHTML='';vs.forEach(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=v.name;if(v.id===dv[p])o.selected=true;sel.appendChild(o)});if($('voice-search'))$('voice-search').value='';// also fill compare selects
const fillSel=id=>{const s=$(id);if(!s)return;s.innerHTML='';vs.forEach(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=v.name;s.appendChild(o)})};fillSel('compare-a');fillSel('compare-b');if(vs.length>1&&$('compare-b'))$('compare-b').selectedIndex=1;updateLegadoConfig()}catch(e){console.warn('loadVoices error:',e)}};

    window.updateLegadoConfig=async()=>{const v=$('voice-select').value;if(!v)return;try{const r=await api('/api/legado/config?voice='+encodeURIComponent(v));const d=await r.json();$('legado-config').textContent=JSON.stringify(d,null,2)}catch(e){console.warn('updateLegadoConfig error:',e)}};

    window.copyConfig=async()=>{const t=$('legado-config').textContent;try{await navigator.clipboard.writeText(t);toast('已复制到剪切板')}catch(e){const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0';document.body.appendChild(a);a.select();try{document.execCommand('copy');toast('已复制到剪切板')}catch(_){toast('复制失败')}document.body.removeChild(a)}};
    window.copySubscribeUrl=async()=>{const v=$('voice-select').value;if(!v){toast('请先选择音色');return}try{const r=await api('/api/legado/subscribe?voice='+encodeURIComponent(v));const d=await r.json();const u=d.url||window.location.origin+'/api/legado/subscribe?voice='+encodeURIComponent(v)+'&auto=true';await navigator.clipboard.writeText(u);toast('已复制订阅链接')}catch(e){toast('复制失败')}};

    // View-only provider switch: no server write. Splitting this out means the
    // initial setProvider(cur) on page load no longer POSTs /api/config, and
    // re-clicking the already-active provider is a no-op.
    const applyProvider=p=>{prov=p;document.querySelectorAll('.provider-btn').forEach(b=>b.classList.toggle('active',b.dataset.provider===p));$('doubao-settings').style.display=p==='doubao'?'block':'none';$('tencent-settings').style.display=p==='tencent'?'block':'none';$('xiaomi-settings').style.display=p==='xiaomi'?'block':'none';$('fishaudio-settings').style.display=p==='fishaudio'?'block':'none';if(p==='edge'){$('save-btn').style.display='none';$('test-cfg-btn').style.display='none';$('api-note').textContent='Edge TTS 免费使用，无需配置。';$('api-note').style.display='inline'}else{$('save-btn').style.display='inline-block';$('test-cfg-btn').style.display='inline-block';$('api-note').style.display='none'}showStats();loadVoices(p)};
    let _savedProvider=cur;
    window.setProvider=async p=>{
        if(p===prov)return;
        applyProvider(p);
        if(p===_savedProvider)return;
        try{
            const r=await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:p})});
            if(r.ok){_savedProvider=p}else{toast('切换服务商失败')}
        }catch(e){toast('切换服务商失败: '+e.message)}
    };

    window.saveConfig=async()=>{const b=$('save-btn');b.disabled=true;b.textContent='保存中...';try{let d={provider:prov};const v=$('voice-select').value;if(prov==='doubao'){d.appid=$('appid').value;d.access_token=$('access-token').value||'***';d.default_voice=v}else if(prov==='tencent'){d.tencent_secret_id=$('tencent-secret-id').value;d.tencent_secret_key=$('tencent-secret-key').value||'***';d.tencent_voice=v}else if(prov==='xiaomi'){d.xiaomi_api_key=$('xiaomi-api-key').value||'***';d.xiaomi_voice=v}else if(prov==='fishaudio'){d.fishaudio_api_key=$('fishaudio-api-key').value||'***';d.fishaudio_voice=v;d.fishaudio_reference_id=$('fishaudio-reference-id').value}await api('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});toast('设置已保存');setTimeout(()=>location.reload(),1000)}finally{b.disabled=false;b.textContent='保存设置'}};

    window.testTTS=async()=>{const b=$('test-btn');b.disabled=true;b.textContent='合成中...';try{const r=await api('/speech/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('test-text').value,voice:$('voice-select').value,rate:'0%'})});if(r.ok){const p=$('audio-player');setAudioBlob(p,await r.blob());p.play()}else toast('TTS失败: '+await _errText(r))}catch(e){toast('请求错误: '+e.message)}finally{b.disabled=false;b.textContent='播放测试'}};

    window.previewVoice=async()=>{const v=$('voice-select').value;if(!v){toast('请先选择音色');return}try{const r=await api('/speech/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'你好，我是您的朗读助手，很高兴认识您。',voice:v,rate:'0%'})});if(r.ok){const p=$('audio-player');setAudioBlob(p,await r.blob());p.play()}else toast('试听失败: '+await _errText(r))}catch(e){toast('试听失败: '+e.message)}};
    window.compareVoices=async()=>{const b=$('compare-btn');const text=$('compare-text').value;const va=$('compare-a').value;const vb=$('compare-b').value;if(!text||!va||!vb){toast('请填写文本并选择两个音色');return}if(b)b.disabled=true;const synthesize=async voice=>{const r=await api('/speech/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,voice,rate:'0%'})});if(!r.ok)throw new Error('TTS failed');return await r.blob()};try{const [ba,bb]=await Promise.all([synthesize(va),synthesize(vb)]);setAudioBlob($('audio-a'),ba);setAudioBlob($('audio-b'),bb);toast('对比音频已加载')}catch(e){toast('对比失败: '+e.message)}finally{if(b)b.disabled=false}};
    window.filterVoices=()=>{const q=$('voice-search').value.toLowerCase();const sel=$('voice-select');const cur=sel.value;sel.innerHTML='';_allVoiceOptions.filter(v=>!q||v.name.toLowerCase().includes(q)||v.id.toLowerCase().includes(q)).forEach(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=v.name;if(v.id===cur)o.selected=true;sel.appendChild(o)});updateLegadoConfig()};

    window.testConfig=async()=>{const b=$('test-cfg-btn');b.disabled=true;b.textContent='测试中...';try{const r=await api('/api/config/test',{method:'POST'});const d=await r.json();toast(d.ok?'✅ 连接成功！'+(d.audio_size||0)+'字节':'❌ 失败: '+(d.error||'未知'))}catch(e){toast('请求错误: '+e.message)}finally{b.disabled=false;b.textContent='测试连接'}};

    window.resetStats=async()=>{if(!confirm('确定要重置所有统计数据吗？'))return;await api('/api/stats',{method:'DELETE'});toast('统计已重置');loadStats()};
    window.exportConfig=async()=>{try{const r=await api('/api/config/export');const b=await r.blob();const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download='tts-config.json';a.click();URL.revokeObjectURL(u);toast('配置已导出')}catch(e){toast('导出失败')}};
    window.importConfig=async(e)=>{const f=e.target.files[0];if(!f)return;try{const t=await f.text();const d=JSON.parse(t);const r=await api('/api/config/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});if(r.ok){toast('配置已导入，刷新页面...');setTimeout(()=>location.reload(),1000)}else toast('导入失败')}catch(e){toast('导入失败: '+e.message)}};

    function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',3000)}

    applyProvider(cur);loadStats();loadSystemStatus();
    // 每30秒刷新系统状态
    setInterval(loadSystemStatus,30000);
    api('/api/config').then(r=>r.json()).then(c=>{if(c.provider_status){const s=c.provider_status;setStatus($('doubao-status'),s.doubao?.ready);setStatus($('tencent-status'),s.tencent?.ready);setStatus($('xiaomi-status'),s.xiaomi?.ready);setStatus($('fishaudio-status'),s.fishaudio?.ready)}}).catch(()=>{});
    // SSE real-time activity feed
    try{const _t=getToken();const es=new EventSource('/api/events'+(_t?'?token='+encodeURIComponent(_t):''));const feed=document.createElement('div');feed.id='live-feed';feed.style.cssText='max-height:200px;overflow-y:auto;font-family:monospace;font-size:12px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:8px;margin-top:12px';const h=document.createElement('h3');h.textContent='📡 实时活动';h.style.cssText='margin:0 0 8px';feed.prepend(h);const main=document.querySelector('.container');if(main)main.appendChild(feed);es.onmessage=e=>{try{const d=JSON.parse(e.data);if(d.type==='tts_request'){const line=document.createElement('div');const ok=d.status>=200&&d.status<300;line.style.color=ok?'#4caf50':'#f44336';line.textContent=`[${d.ts?.split('T')[1]?.split('.')[0]||''}] ${d.provider||'?'} ${d.voice||''} ${d.chars}字 ${d.ms}ms ${d.status}`;feed.appendChild(line);if(feed.children.length>52)feed.removeChild(feed.children[1]);feed.scrollTop=feed.scrollHeight}}catch(ex){}};es.onerror=()=>{}}catch(ex){}

    // 全部音色试听功能
    window.showAllVoices=()=>{
        // 创建模态框
        const modal=document.createElement('div');
        modal.id='voice-modal';
        modal.style.cssText='position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999;padding:20px';
        modal.onclick=(e)=>{if(e.target===modal)modal.remove()};
        // 内容容器
        const content=document.createElement('div');
        content.style.cssText='background:var(--card);border-radius:12px;width:100%;max-width:900px;max-height:80vh;overflow:auto;padding:20px';
        // 头部
        const header=document.createElement('div');
        header.style.cssText='display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border)';
        header.innerHTML='<h2 style="margin:0">所有音色试听</h2><button style="background:none;border:none;font-size:24px;cursor:pointer;color:var(--text)">×</button>';
        header.querySelector('button').onclick=()=>modal.remove();
        content.appendChild(header);
        // 搜索框
        const search=document.createElement('input');
        search.placeholder='搜索音色...';
        search.style.cssText='width:100%;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:14px;margin-bottom:16px';
        content.appendChild(search);
        // 音色网格
        const grid=document.createElement('div');
        grid.style.cssText='display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px';
        // 播放音频的全局元素
        let currentAudio=null;
        // 生成所有音色卡片
        _allVoiceOptions.forEach(v=>{
            const card=document.createElement('div');
            card.style.cssText='border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;justify-content:space-between;align-items:center';
            card.innerHTML=`<span style="font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${v.name}</span><button class="btn" style="padding:4px 8px;font-size:12px">▶ 试听</button>`;
            const btn=card.querySelector('button');
            btn.onclick=async()=>{
                btn.disabled=true;
                btn.textContent='⏳ 加载中...';
                try{
                    if(currentAudio){
                        currentAudio.pause();
                        currentAudio.src='';
                    }
                    const r=await api('/speech/stream',{
                        method:'POST',
                        headers:{'Content-Type':'application/json'},
                        body:JSON.stringify({text:'你好，我是这个音色的朗读效果。',voice:v.id,rate:'0%'})
                    });
                    if(r.ok){
                        const bl=await r.blob();
                        const audio=new Audio(URL.createObjectURL(bl));
                        currentAudio=audio;
                        audio.play();
                        btn.textContent='⏸ 播放中';
                        audio.onended=()=>{btn.textContent='▶ 试听'};
                    }else toast('试听失败');
                }catch(e){toast('试听失败: '+e.message)}finally{
                    btn.disabled=false;
                }
            };
            grid.appendChild(card);
        });
        // 搜索过滤
        search.oninput=()=>{
            const q=search.value.toLowerCase();
            Array.from(grid.children).forEach((card,i)=>{
                const v=_allVoiceOptions[i];
                card.style.display=q&&!v.name.toLowerCase().includes(q)&&!v.id.toLowerCase().includes(q)?'none':'flex';
            });
        };
        content.appendChild(grid);
        modal.appendChild(content);
        document.body.appendChild(modal);
    };
});
</script>
<!-- 模态框样式已经内联在JS里 -->
</body>
</html>"""


@app.route('/api/speech/batch', methods=['POST'])
@gzipped
def batch_speech():
    """Batch TTS endpoint. Synthesize multiple texts in one request.

    POST /api/speech/batch
    {
        "voice": "zh-CN-XiaoxiaoNeural",  // required
        "rate": "0%",                     // optional: +/- percentage
        "texts": ["text1", "text2", ...],   // required: array of texts
        "response_format": "mp3"           // optional: mp3/wav/ogg
    }

    Returns:
    {
        "results": [
            {"text": "text1", "audio": "base64_audio_data", "error": null},
            {"text": "text2", "audio": null, "error": "error message"}
        ]
    }
    """
    try:
        client_ip = request.remote_addr or 'unknown'
        if _check_rate_limit(client_ip):
            return _error_response('Rate limit exceeded', 429, 'rate_limit_error',
                                   headers={'Retry-After': '60'})
        key_err = _check_api_key()
        if key_err:
            return key_err

        data = request.get_json(silent=True) or {}
        texts = data.get('texts', [])
        if not isinstance(texts, list) or len(texts) == 0:
            return _error_response('Missing or invalid texts array', 400, 'invalid_request_error')
        if len(texts) > BATCH_MAX_TEXTS:
            return _error_response(f'Maximum {BATCH_MAX_TEXTS} texts per batch request',
                                   400, 'invalid_request_error')
        for t in texts:
            if isinstance(t, str) and len(t) > MAX_TEXT_LENGTH:
                return _error_response(f'Text too long: "{str(t)[:50]}..."',
                                       400, 'invalid_request_error')

        voice = _sanitize_voice(data.get('voice'))
        if not voice:
            return _error_response('Missing voice', 400, 'invalid_request_error')
        rate = str(data.get('rate', '0%')).strip()
        resp_format = str(data.get('response_format', 'mp3')).strip().lower()
        if resp_format not in _FORMAT_MIME:
            resp_format = 'mp3'

        # Try to resolve voice by name if not a known ID
        voice = _resolve_voice_alias(voice)

        provider = resolve_provider(voice)
        if not provider:
            return _error_response(f'Unknown voice: {voice}', 400, 'invalid_request_error')

        # Check daily quota (estimate total chars)
        total_input_chars = sum(len(str(t or '')) for t in texts)
        quota_err = _check_daily_quota(client_ip, total_input_chars)
        if quota_err:
            return quota_err

        pct = parse_rate(rate)
        results = []
        # Chars synthesized, keyed by the (provider, voice) that actually did the
        # work — a mid-batch fallback must not be attributed to the requested
        # provider, and earlier successes must not be attributed to the fallback.
        chars_by_target = {}

        for text in texts:
            if not isinstance(text, str):
                results.append({'text': text, 'audio': None,
                                'error': 'Text is None' if text is None else 'Text must be a string'})
                continue
            text = text.strip()
            if not text:
                results.append({'text': text, 'audio': None, 'error': 'Empty text'})
                continue
            try:
                audio, error, actual_provider, actual_voice = dispatch(provider, text, voice, pct)
                if audio:
                    target = (actual_provider or provider, actual_voice or voice)
                    chars_by_target[target] = chars_by_target.get(target, 0) + len(text)
                    audio, actual_format = _convert_audio(audio, resp_format)
                    results.append({'text': text, 'audio': base64.b64encode(audio).decode('utf-8'),
                                    'error': None, 'provider': actual_provider or provider,
                                    'voice': actual_voice or voice, 'format': actual_format})
                else:
                    results.append({'text': text, 'audio': None, 'error': error})
            except Exception as e:
                log.error("batch item failed: %s", e, exc_info=True)
                results.append({'text': text, 'audio': None, 'error': str(e)})

        for (stats_provider, stats_voice), nchars in chars_by_target.items():
            update_stats(nchars, stats_provider, voice=stats_voice)

        return jsonify({'results': results})
    except Exception as e:
        log.error("batch_speech error: %s", e, exc_info=True)
        return _error_response(str(e), 500, 'server_error')


_shutdown_done = False


def _graceful_shutdown():
    """Flush pending state and clean up on shutdown. Idempotent."""
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    log.info("Shutting down gracefully...")
    # Note: stats are written synchronously by update_stats() on every request,
    # so there is nothing to flush here. Previously this stamped a
    # '_last_shutdown' key into STATS_FILE, which polluted the provider-keyed
    # structure that /api/stats and load_stats() iterate over.
    # Close SSE subscribers
    with _sse_lock:
        for q in _sse_subscribers:
            try:
                q.put(None)
            except Exception:
                pass
        _sse_subscribers.clear()
    # Flush edge executor
    _shutdown_edge_executor()
    log.info("Shutdown complete.")

atexit.register(_graceful_shutdown)


def _signal_handler(sig, frame):
    log.info("Received signal %s, shutting down...", sig)
    _graceful_shutdown()
    raise SystemExit(0)


# Install handlers unconditionally so SSE subscribers are released under
# gunicorn too, but never clobber a handler the WSGI server already set.
for _sig in (signal.SIGTERM, signal.SIGINT):
    try:
        if signal.getsignal(_sig) in (signal.SIG_DFL, signal.default_int_handler):
            signal.signal(_sig, _signal_handler)
    except (ValueError, OSError, AttributeError):
        pass  # not the main thread, or platform lacks the signal


if __name__ == '__main__':
    os.makedirs(os.path.dirname(CONFIG_FILE) or '.', exist_ok=True)
    port = int(os.environ.get('PORT', '80'))
    log.info("Legado TTS Server v%s starting on port %d", __version__, port)
    log.info("Config: %s | Stats: %s", CONFIG_FILE, STATS_FILE)
    log.info("FFmpeg: %s | Admin: %s", _FFMPEG_AVAILABLE, bool(ADMIN_TOKEN))
    log.info("Rate limit: %d RPM | Cache: %d entries", RATE_LIMIT_RPM, AUDIO_CACHE_SIZE)
    app.run(host='0.0.0.0', port=port, threaded=True)
