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
try:
    import fcntl
except ImportError:
    fcntl = None
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, Response, render_template_string, jsonify
from flask_cors import CORS
import requests
import edge_tts

__version__ = '1.2.0'

# ──────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────
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
CORS(app, resources={
    r"/api/*": {"origins": "*"},
    r"/speech/*": {"origins": "*"},
    r"/health": {"origins": "*"},
})

# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────
CONFIG_FILE = os.environ.get('CONFIG_FILE', '/opt/doubao-tts/config.json')
STATS_FILE = os.environ.get('STATS_FILE', '/opt/doubao-tts/stats.json')
MAX_TEXT_LENGTH = int(os.environ.get('MAX_TEXT_LENGTH', '5000'))
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')

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
    # Chinese
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
    # English
    {"id": "en-US-JennyNeural", "name": "Jenny - English F"},
    {"id": "en-US-GuyNeural", "name": "Guy - English M"},
    {"id": "en-US-AriaNeural", "name": "Aria - English F"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia - British F"},
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

ALL_PROVIDERS = ['doubao', 'tencent', 'edge', 'xiaomi']
_ALL_VOICE_IDS = set()
for _voices in [EDGE_VOICES, DOUBAO_VOICES, TENCENT_VOICES, XIAOMI_VOICES]:
    for _v in _voices:
        _ALL_VOICE_IDS.add(_v['id'])

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
    os.makedirs(os.path.dirname(path), exist_ok=True)
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

def load_config():
    cfg = _read_json(CONFIG_FILE, DEFAULT_CONFIG.copy())
    for k, v in DEFAULT_CONFIG.items():
        if k not in cfg:
            cfg[k] = v
    return cfg


def save_config(config):
    _write_json(CONFIG_FILE, config)


# ──────────────────────────────────────────────
# Stats (file-locked)
# ──────────────────────────────────────────────

_empty_provider_stats = {'total_chars': 0, 'total_requests': 0, 'history': []}


def load_stats():
    data = _read_json(STATS_FILE, {})
    if not isinstance(data, dict):
        data = {}
    for p in ALL_PROVIDERS:
        if p not in data or not isinstance(data[p], dict):
            data[p] = dict(_empty_provider_stats)
        for k in _empty_provider_stats:
            if k not in data[p]:
                data[p][k] = _empty_provider_stats[k]
    return data


def _apply_stats_update(stats, chars, provider):
    ps = stats.get(provider, dict(_empty_provider_stats))
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
    stats[provider] = ps
    return stats


def _update_stats_with_retry(chars, provider, retries=3, delay=0.1):
    for attempt in range(1, retries + 1):
        try:
            stats = load_stats()
            _write_json(STATS_FILE, _apply_stats_update(stats, chars, provider))
            return
        except OSError as e:
            if attempt == retries:
                raise
            log.warning("Stats update retry %d/%d after error: %s", attempt, retries, e)
            time.sleep(delay * attempt)


def update_stats(chars, provider):
    if not provider:
        return
    try:
        os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
        if fcntl is None:
            _update_stats_with_retry(chars, provider)
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
                stats = _apply_stats_update(stats, chars, provider)
                f.seek(0)
                f.truncate()
                json.dump(stats, f, indent=2, ensure_ascii=False)
            finally:
                try:
                    fcntl.flock(f, fcntl.LOCK_UN)
                except OSError:
                    pass
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
            headers=headers, json=payload, timeout=30)
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
        resp = _http_session.post(f'https://{host}', headers=hdrs, data=payload, timeout=30)
        result = resp.json()
        if 'Response' in result and 'Audio' in result['Response']:
            return base64.b64decode(result['Response']['Audio']), None
        err = result.get('Response', {}).get('Error', {})
        return None, f"腾讯云错误: {err.get('Message', str(result))}"
    try:
        return _retry(_do)
    except Exception as e:
        return None, str(e)


def synthesize_edge(text, voice, rate='+0%'):
    async def _synth():
        comm = edge_tts.Communicate(text, voice, rate=rate)
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
            fut = _edge_executor.submit(asyncio.run, _synth())
            return fut.result(timeout=30), None
        return asyncio.run(_synth()), None
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
            headers=headers, json=payload, timeout=30)
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
    if 'Neural' in voice:
        return 'edge'
    if voice.isdigit() and 1 <= int(voice) <= 999999:
        return 'tencent'
    if voice.startswith('zh_'):
        return 'doubao'
    if voice in ('mimo_default', 'default_zh', 'default_eh') or voice.startswith('mimo_'):
        return 'xiaomi'
    return None


def parse_rate(rate_str):
    """Parse rate string ('+50%', '-20%') to float percentage."""
    try:
        return float(str(rate_str).replace('%', '').replace('+', '').strip())
    except (ValueError, TypeError):
        return 0.0


def dispatch(provider, text, voice, pct):
    """Route to the correct TTS provider and return (audio_bytes, error)."""
    if provider == 'edge':
        rate = f'+{int(pct)}%' if pct >= 0 else f'{int(pct)}%'
        return synthesize_edge(text, voice, rate)
    if provider == 'tencent':
        return synthesize_tencent(text, voice, max(-2, min(6, pct / 50)))
    if provider == 'doubao':
        return synthesize_doubao(text, voice, max(0.2, min(3.0, 1.0 + pct / 100)))
    if provider == 'xiaomi':
        return synthesize_xiaomi(text, voice, max(0.2, min(3.0, 1.0 + pct / 100)))
    return None, f'Unknown provider: {provider}'


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': __version__,
                    'timestamp': datetime.now().isoformat()})


@app.route('/speech/stream', methods=['POST'])
def speech_stream():
    try:
        data = request.get_json(silent=True) or {}
        text = str(data.get('text', '')).strip()
        voice = str(data.get('voice', '')).strip()
        if not text:
            return Response('Missing text', status=400)
        if not voice:
            return Response('Missing voice', status=400)
        if len(text) > MAX_TEXT_LENGTH:
            return Response(f'Text too long (max {MAX_TEXT_LENGTH})', status=400)

        provider = resolve_provider(voice)
        if not provider:
            return Response(f'Unknown voice format: {voice}', status=400)

        pct = parse_rate(data.get('rate', '0%'))
        audio, error = dispatch(provider, text, voice, pct)

        if audio:
            update_stats(len(text), provider)
            log.info("TTS OK: provider=%s voice=%s chars=%d size=%d",
                     provider, voice, len(text), len(audio))
            return Response(audio, mimetype='audio/mpeg', headers={
                'Content-Length': str(len(audio)),
                'X-TTS-Provider': provider,
                'X-TTS-Voice': voice,
                'Cache-Control': 'no-store',
            })
        log.warning("TTS failed: provider=%s voice=%s error=%s", provider, voice, error)
        return Response(f'TTS failed: {error}', status=500)
    except Exception as e:
        log.error("speech_stream error: %s", e, exc_info=True)
        return Response(f'Error: {e}', status=500)


@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    config = load_config()
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        for key in ['provider', 'default_voice', 'tencent_voice',
                    'edge_voice', 'xiaomi_voice', 'cluster']:
            if key in data:
                config[key] = data[key]
        for key in ['appid', 'access_token', 'tencent_secret_id',
                    'tencent_secret_key', 'xiaomi_api_key']:
            if key in data and '***' not in str(data[key]):
                config[key] = data[key]
        # Validate voice selection
        for vkey in ['default_voice', 'tencent_voice', 'edge_voice', 'xiaomi_voice']:
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
    }
    return jsonify(safe)


@app.route('/api/config/test', methods=['POST'])
def api_config_test():
    """Test current provider config by synthesizing a short sample."""
    config = load_config()
    provider = config.get('provider', 'edge')
    result = {'provider': provider, 'ok': True, 'error': None}
    voice = config.get(f'{provider}_voice') or config.get('default_voice')
    if not voice:
        voice_map = {'edge': EDGE_VOICES[0]['id'], 'doubao': DOUBAO_VOICES[0]['id'],
                     'tencent': TENCENT_VOICES[0]['id'], 'xiaomi': XIAOMI_VOICES[0]['id']}
        voice = voice_map.get(provider, EDGE_VOICES[0]['id'])
    audio, error = dispatch(provider, '你好，配置测试。', voice, 0)
    if audio:
        result['audio_size'] = len(audio)
        result['voice'] = voice
    else:
        result['ok'] = False
        result['error'] = error
    return jsonify(result)


@app.route('/api/stats', methods=['GET', 'DELETE'])
def api_stats():
    if request.method == 'DELETE':
        _write_json(STATS_FILE, {p: dict(_empty_provider_stats) for p in ALL_PROVIDERS})
        log.info("Stats reset")
        return jsonify({'status': 'ok', 'message': '统计已重置'})
    return jsonify(load_stats())


@app.route('/api/voices', methods=['GET'])
def api_voices():
    p = request.args.get('provider', 'edge')
    return jsonify({
        'tencent': TENCENT_VOICES, 'doubao': DOUBAO_VOICES,
        'xiaomi': XIAOMI_VOICES, 'edge': EDGE_VOICES,
    }.get(p, EDGE_VOICES))


@app.route('/')
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
        *{box-sizing:border-box;margin:0;padding:0}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;background:var(--bg);color:var(--text);line-height:1.6}
        .container{max-width:800px;margin:20px auto;padding:0 15px}
        .card{background:var(--card);border-radius:12px;padding:24px;margin-bottom:20px;box-shadow:0 4px 12px rgba(0,0,0,.08)}
        h1{text-align:center;font-size:28px;margin-bottom:16px}
        h2{font-size:20px;margin-bottom:16px}
        .row{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
        .row h2{margin:0}
        .provider-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
        .provider-btn{padding:12px;border:2px solid var(--border);border-radius:8px;cursor:pointer;text-align:center;background:var(--card);transition:all .2s}
        .provider-btn:hover{border-color:#aaa}
        .provider-btn.active{border-color:var(--primary);background:#e7f1ff;box-shadow:0 0 0 2px rgba(0,123,255,.2)}
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
    <div class="card">
        <h2>服务商选择</h2>
        <div class="provider-grid">
            <div class="provider-btn" data-provider="edge" onclick="setProvider('edge')"><strong>Edge</strong><div class="badge badge-ok">免费</div></div>
            <div class="provider-btn" data-provider="doubao" onclick="setProvider('doubao')"><strong>火山引擎</strong><div class="badge" id="doubao-status"></div></div>
            <div class="provider-btn" data-provider="tencent" onclick="setProvider('tencent')"><strong>腾讯云</strong><div class="badge" id="tencent-status"></div></div>
            <div class="provider-btn" data-provider="xiaomi" onclick="setProvider('xiaomi')"><strong>小米MiMo</strong><div class="badge" id="xiaomi-status"></div></div>
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
        <div class="row"><h2>开源阅读配置</h2><div style="display:flex;align-items:center;gap:8px"><label for="voice-select" style="font-weight:500">音色</label><select id="voice-select" onchange="updateLegadoConfig()" style="padding:6px 12px;border:1px solid var(--border);border-radius:6px;font-size:14px"></select></div></div>
        <p style="color:#666;margin-bottom:12px">复制以下配置到开源阅读的朗读引擎，即可使用上方选择的音色。</p>
        <div class="code" id="legado-config"></div>
        <button class="btn btn-primary" onclick="copyConfig()" style="margin-top:12px">复制配置</button>
    </div>
    <div class="card">
        <h2>API 设置</h2>
        <div id="doubao-settings" style="display:none"><div class="field"><label>App ID</label><input type="text" id="appid" value="{{ appid }}"></div><div class="field"><label>Access Token</label><input type="password" id="access-token" placeholder=""></div></div>
        <div id="tencent-settings" style="display:none"><div class="field"><label>SecretId</label><input type="text" id="tencent-secret-id" value="{{ tencent_secret_id }}"></div><div class="field"><label>SecretKey</label><input type="password" id="tencent-secret-key" placeholder=""></div></div>
        <div id="xiaomi-settings" style="display:none"><div class="field"><label>API Key</label><input type="password" id="xiaomi-api-key" value="{{ xiaomi_api_key }}" placeholder="请输入小米MiMo API Key"></div></div>
        <div style="display:flex;gap:8px;align-items:center">
            <button class="btn btn-primary" id="save-btn" onclick="saveConfig()" style="display:none">保存设置</button>
            <button class="btn btn-success" id="test-cfg-btn" onclick="testConfig()" style="display:none">测试连接</button>
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
    let stats={},prov=cur;
    const $=id=>document.getElementById(id);
    const setStatus=(el,ok)=>{el.textContent=ok?'已配置':'未配置';el.className='badge '+(ok?'badge-ok':'badge-err')};

    const loadStats=async()=>{try{const r=await fetch('/api/stats');stats=await r.json();showStats()}catch(e){}};
    const showStats=()=>{const d=stats[prov]||{};$('total-chars').textContent=(d.total_chars||0).toLocaleString();$('total-requests').textContent=(d.total_requests||0).toLocaleString();const t=new Date().toISOString().split('T')[0];const td=(d.history||[]).find(h=>h.date===t)||{};$('today-chars').textContent=(td.chars||0).toLocaleString();$('today-requests').textContent=(td.requests||0).toLocaleString()};

    const loadVoices=async p=>{try{const r=await fetch('/api/voices?provider='+p);const vs=await r.json();const sel=$('voice-select');sel.innerHTML='';vs.forEach(v=>{const o=document.createElement('option');o.value=v.id;o.textContent=v.name;if(v.id===dv[p])o.selected=true;sel.appendChild(o)});updateLegadoConfig()}catch(e){}};

    window.updateLegadoConfig=()=>{const v=$('voice-select').value;if(!v)return;const q='"',lb='{{',rb='}}';$('legado-config').textContent='{\\n  "concurrentRate": "0",\\n  "contentType": "audio/mpeg",\\n  "name": "TTS服务",\\n  "url": "http://'+IP+'/speech/stream,{'+q+'method'+q+':'+q+'POST'+q+','+q+'body'+q+':{'+q+'text'+q+':'+q+lb+'speakText'+rb+q+','+q+'voice'+q+':'+q+v+q+','+q+'rate'+q+':'+q+lb+'String(speakSpeed)'+rb+'%'+q+'},'+q+'headers'+q+':{'+q+'Content-Type'+q+':'+q+'application/json'+q+'}}"\\n}'};

    window.copyConfig=async()=>{const t=$('legado-config').textContent;try{await navigator.clipboard.writeText(t);toast('已复制到剪切板')}catch(e){const a=document.createElement('textarea');a.value=t;a.style.cssText='position:fixed;opacity:0';document.body.appendChild(a);a.select();try{document.execCommand('copy');toast('已复制到剪切板')}catch(_){toast('复制失败')}document.body.removeChild(a)}};

    window.setProvider=p=>{prov=p;document.querySelectorAll('.provider-btn').forEach(b=>b.classList.toggle('active',b.dataset.provider===p));$('doubao-settings').style.display=p==='doubao'?'block':'none';$('tencent-settings').style.display=p==='tencent'?'block':'none';$('xiaomi-settings').style.display=p==='xiaomi'?'block':'none';if(p==='edge'){$('save-btn').style.display='none';$('test-cfg-btn').style.display='none';$('api-note').textContent='Edge TTS 免费使用，无需配置。';$('api-note').style.display='inline'}else{$('save-btn').style.display='inline-block';$('test-cfg-btn').style.display='inline-block';$('api-note').style.display='none'}showStats();loadVoices(p);fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({provider:p})})};

    window.saveConfig=async()=>{const b=$('save-btn');b.disabled=true;b.textContent='保存中...';try{let d={provider:prov};const v=$('voice-select').value;if(prov==='doubao'){d.appid=$('appid').value;d.access_token=$('access-token').value||'***';d.default_voice=v}else if(prov==='tencent'){d.tencent_secret_id=$('tencent-secret-id').value;d.tencent_secret_key=$('tencent-secret-key').value||'***';d.tencent_voice=v}else if(prov==='xiaomi'){d.xiaomi_api_key=$('xiaomi-api-key').value||'***';d.xiaomi_voice=v}await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});toast('设置已保存');setTimeout(()=>location.reload(),1000)}finally{b.disabled=false;b.textContent='保存设置'}};

    window.testTTS=async()=>{const b=$('test-btn');b.disabled=true;b.textContent='合成中...';try{const r=await fetch('/speech/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:$('test-text').value,voice:$('voice-select').value,rate:'0%'})});if(r.ok){const bl=await r.blob();const p=$('audio-player');if(p._u)URL.revokeObjectURL(p._u);p._u=URL.createObjectURL(bl);p.src=p._u;p.play()}else toast('TTS失败: '+await r.text())}catch(e){toast('请求错误: '+e.message)}finally{b.disabled=false;b.textContent='播放测试'}};

    window.testConfig=async()=>{const b=$('test-cfg-btn');b.disabled=true;b.textContent='测试中...';try{const r=await fetch('/api/config/test',{method:'POST'});const d=await r.json();toast(d.ok?'✅ 连接成功！'+(d.audio_size||0)+'字节':'❌ 失败: '+(d.error||'未知'))}catch(e){toast('请求错误: '+e.message)}finally{b.disabled=false;b.textContent='测试连接'}};

    window.resetStats=async()=>{if(!confirm('确定要重置所有统计数据吗？'))return;await fetch('/api/stats',{method:'DELETE'});toast('统计已重置');loadStats()};

    function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';setTimeout(()=>t.style.display='none',3000)}

    setProvider(cur);loadStats();
    fetch('/api/config').then(r=>r.json()).then(c=>{if(c.provider_status){const s=c.provider_status;setStatus($('doubao-status'),s.doubao?.ready);setStatus($('tencent-status'),s.tencent?.ready);setStatus($('xiaomi-status'),s.xiaomi?.ready)}}).catch(()=>{});
});
</script>
</body>
</html>"""


if __name__ == '__main__':
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    port = int(os.environ.get('PORT', '80'))
    log.info("Legado TTS Server v%s starting on port %d", __version__, port)
    log.info("Config: %s | Stats: %s", CONFIG_FILE, STATS_FILE)
    app.run(host='0.0.0.0', port=port, threaded=True)
