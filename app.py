#!/usr/bin/env python3
"""TTS服务 - 支持 Edge、火山引擎、腾讯云"""

import os, json, base64, uuid, hmac, hashlib, time, asyncio, io
from datetime import datetime
from flask import Flask, request, Response, render_template_string, jsonify
import requests
import edge_tts

app = Flask(__name__)
CONFIG_FILE = '/opt/doubao-tts/config.json'
STATS_FILE = '/opt/doubao-tts/stats.json'

DEFAULT_CONFIG = {
    'provider': 'edge', 'appid': '', 'access_token': '',
    'default_voice': 'zh-CN-XiaoxiaoNeural', 'cluster': 'volcano_tts',
    'tencent_secret_id': '', 'tencent_secret_key': '',
    'tencent_voice': '501002', 'tencent_region': 'ap-guangzhou',
    'edge_voice': 'zh-CN-XiaoxiaoNeural'
}

DOUBAO_VOICES = [
    {"id": "zh_female_cancan_mars_bigtts", "name": "知性灿灿"}, {"id": "zh_female_shuangkuaisisi_moon_bigtts", "name": "爽快思思"},
    {"id": "zh_female_tiexinnvsheng_mars_bigtts", "name": "贴心女生"}, {"id": "zh_female_jitangmeimei_mars_bigtts", "name": "鸡汤妹妹"},
    {"id": "zh_female_mengyatou_mars_bigtts", "name": "萌丫头"}, {"id": "zh_male_shaonianzixin_moon_bigtts", "name": "少年梓辛"},
    {"id": "zh_male_wennuanahu_moon_bigtts", "name": "温暖阿虎"}, {"id": "zh_male_jieshuonansheng_mars_bigtts", "name": "磁性解说"},
]
TENCENT_VOICES = [
    {"id": "501002", "name": "智菊 - 阅读女声"}, {"id": "501000", "name": "智斌 - 阅读男声"},
    {"id": "501001", "name": "智兰 - 资讯女声"}, {"id": "501003", "name": "智宇 - 阅读男声"},
    {"id": "601009", "name": "爱小芊 - 多情感女声"}, {"id": "601008", "name": "爱小豪 - 多情感男声"},
    {"id": "601010", "name": "爱小娇 - 多情感女声"},
]
EDGE_VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓 - 女声"}, {"id": "zh-CN-YunxiNeural", "name": "云希 - 男声"},
    {"id": "zh-CN-YunjianNeural", "name": "云健 - 男声"}, {"id": "zh-CN-XiaoyiNeural", "name": "晓伊 - 女声"},
    {"id": "zh-CN-YunyangNeural", "name": "云扬 - 新闻"}, {"id": "zh-CN-XiaochenNeural", "name": "晓辰 - 女声"},
    {"id": "zh-CN-XiaohanNeural", "name": "晓涵 - 女声"}, {"id": "zh-CN-XiaomengNeural", "name": "晓梦 - 女声"},
    {"id": "zh-CN-XiaomoNeural", "name": "晓墨 - 女声"}, {"id": "zh-CN-XiaoqiuNeural", "name": "晓秋 - 女声"},
    {"id": "zh-CN-XiaoruiNeural", "name": "晓睿 - 女声"}, {"id": "zh-CN-XiaoshuangNeural", "name": "晓双 - 童声"},
    {"id": "zh-CN-XiaoxuanNeural", "name": "晓萱 - 女声"}, {"id": "zh-CN-XiaoyanNeural", "name": "晓颜 - 女声"},
    {"id": "zh-CN-XiaoyouNeural", "name": "晓悠 - 童声"}, {"id": "zh-CN-YunfengNeural", "name": "云枫 - 男声"},
    {"id": "zh-CN-YunhaoNeural", "name": "云皓 - 男声"}, {"id": "zh-CN-YunxiaNeural", "name": "云夏 - 男声"},
    {"id": "zh-CN-YunyeNeural", "name": "云野 - 男声"}, {"id": "zh-CN-YunzeNeural", "name": "云泽 - 男声"},
]

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
            # Ensure all default keys exist
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg: cfg[k] = v
            return cfg
    return DEFAULT_CONFIG.copy()

def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=2)

def load_stats():
    if not os.path.exists(STATS_FILE):
        return {p: {'total_chars':0,'total_requests':0,'history':[]} for p in ['doubao', 'tencent', 'edge']}
    with open(STATS_FILE, 'r') as f:
        data = json.load(f)
        # Compatibility for old formats
        if 'doubao' not in data or isinstance(data.get('doubao'), int):
             data = {p: {'total_chars':0,'total_requests':0,'history':[]} for p in ['doubao', 'tencent', 'edge']}
        if 'edge' not in data: # Add edge if missing
            data['edge'] = {'total_chars':0,'total_requests':0,'history':[]}
        return data

def save_stats(stats):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, 'w') as f: json.dump(stats, f, indent=2)

def update_stats(chars, provider):
    if not provider: return
    stats = load_stats()
    p_stats = stats.get(provider, {'total_chars':0,'total_requests':0,'history':[]})
    p_stats['total_chars'] += chars
    p_stats['total_requests'] += 1
    today = datetime.now().strftime('%Y-%m-%d')
    day_entry = next((d for d in p_stats['history'] if d['date'] == today), None)
    if day_entry:
        day_entry['chars'] += chars
        day_entry['requests'] += 1
    else:
        p_stats['history'].append({'date': today, 'chars': chars, 'requests': 1})
    p_stats['history'] = p_stats['history'][-30:] # Keep last 30 days
    stats[provider] = p_stats
    save_stats(stats)

def synthesize_doubao(text, voice, speed_ratio=1.0):
    config = load_config()
    if not all(k in config for k in ['appid', 'access_token']) or not config['appid'] or not config['access_token']:
        return None, "未配置火山引擎AppID或Token"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer; {config['access_token']}"}
    payload = {"app": {"appid": config["appid"], "token": "placeholder", "cluster": config.get("cluster", "volcano_tts")},
               "user": {"uid": "legado_user"}, "audio": {"voice_type": voice, "encoding": "mp3", "speed_ratio": speed_ratio},
               "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"}}
    try:
        resp = requests.post("https://openspeech.bytedance.com/api/v1/tts", headers=headers, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") != 3000: return None, f"火山引擎API错误: {result.get('message', 'Unknown')}"
        return base64.b64decode(result.get("data", "")), None
    except Exception as e: return None, str(e)

def tencent_sign(secret_key, date, service, string_to_sign):
    def hmac_sha256(key, msg): return hmac.new(key, msg.encode('utf-8'), hashlib.sha256).digest()
    secret_date = hmac_sha256(('TC3' + secret_key).encode('utf-8'), date)
    secret_service = hmac_sha256(secret_date, service)
    secret_signing = hmac_sha256(secret_service, 'tc3_request')
    return hmac.new(secret_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

def synthesize_tencent(text, voice, speed=0):
    config = load_config()
    secret_id, secret_key = config.get('tencent_secret_id',''), config.get('tencent_secret_key','')
    if not secret_id or not secret_key: return None, "未配置腾讯云SecretId或SecretKey"
    service, host, action, version = 'tts', 'tts.tencentcloudapi.com', 'TextToVoice', '2019-08-23'
    region, algorithm = config.get('tencent_region','ap-guangzhou'), 'TC3-HMAC-SHA256'
    timestamp = int(time.time())
    date = datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d')
    payload = json.dumps({"Text": text, "SessionId": str(uuid.uuid4()), "VoiceType": int(voice), "Codec": "mp3", "SampleRate": 16000, "Speed": speed})
    ct = 'application/json; charset=utf-8'
    canonical_headers = f'content-type:{ct}\nhost:{host}\nx-tc-action:{action.lower()}\n'
    signed_headers = 'content-type;host;x-tc-action'
    hashed_payload = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    canonical_request = f'POST\n/\n\n{canonical_headers}\n{signed_headers}\n{hashed_payload}'
    credential_scope = f'{date}/{service}/tc3_request'
    hashed_canonical = hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    string_to_sign = f'{algorithm}\n{timestamp}\n{credential_scope}\n{hashed_canonical}'
    signature = tencent_sign(secret_key, date, service, string_to_sign)
    authorization = f'{algorithm} Credential={secret_id}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}'
    headers = {'Authorization': authorization, 'Content-Type': ct, 'Host': host, 'X-TC-Action': action, 'X-TC-Timestamp': str(timestamp), 'X-TC-Version': version, 'X-TC-Region': region}
    try:
        resp = requests.post(f'https://{host}', headers=headers, data=payload, timeout=30)
        result = resp.json()
        if 'Response' in result and 'Audio' in result['Response']: return base64.b64decode(result['Response']['Audio']), None
        return None, f"腾讯云错误: {result.get('Response',{}).get('Error',{}).get('Message',str(result))}"
    except Exception as e: return None, str(e)

def synthesize_edge(text, voice, rate='+0%'):
    async def _synthesize():
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        audio_data = io.BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_data.write(chunk["data"])
        return audio_data.getvalue()
    try:
        # This is a synchronous wrapper around an async function
        return asyncio.run(_synthesize()), None
    except Exception as e:
        return None, str(e)

@app.route('/speech/stream', methods=['POST'])
def speech_stream():
    try:
        data = request.json
        text = data.get('text', '')
        voice = data.get('voice', '')
        if not text or not voice: return Response('Missing text or voice', status=400)

        rate = data.get('rate', '0%')
        pct = 0
        try:
            pct = float(rate.replace('%','').replace('+',''))
        except: pass
        
        provider = ''
        audio, error = None, 'Invalid voice'

        if 'Neural' in voice:
            provider = 'edge'
            edge_rate = f'+{int(pct)}%' if pct >= 0 else f'{int(pct)}%'
            audio, error = synthesize_edge(text, voice, edge_rate)
        elif voice.isdigit():
            provider = 'tencent'
            speed_val = max(-2, min(6, pct / 50))
            audio, error = synthesize_tencent(text, voice, speed_val)
        elif voice.startswith('zh_'):
            provider = 'doubao'
            speed_ratio = max(0.2, min(3.0, 1.0 + pct / 100))
            audio, error = synthesize_doubao(text, voice, speed_ratio)

        if audio:
            update_stats(len(text), provider)
            return Response(audio, mimetype='audio/mp3')
        return Response(f'TTS failed: {error}', status=500)
    except Exception as e:
        return Response(f'Error: {e}', status=500)

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    config = load_config()
    if request.method == 'POST':
        data = request.json
        # Only update keys that are present in the request
        for key in ['provider', 'appid', 'default_voice', 'tencent_secret_id', 'tencent_voice', 'edge_voice']:
             if key in data: config[key] = data[key]
        # Handle sensitive keys separately to avoid overwriting with '***'
        if 'access_token' in data and '***' not in data['access_token']:
            config['access_token'] = data['access_token']
        if 'tencent_secret_key' in data and '***' not in data['tencent_secret_key']:
            config['tencent_secret_key'] = data['tencent_secret_key']
        save_config(config)
        return jsonify({'status': 'ok'})
    else: # GET request
        # Mask sensitive info for display
        safe_config = config.copy()
        if safe_config.get('access_token'): safe_config['access_token'] = '***'
        if safe_config.get('tencent_secret_key'): safe_config['tencent_secret_key'] = '***'
        appid = safe_config.get('appid', '')
        if len(appid) > 6: safe_config['appid'] = f"{appid[:3]}***{appid[-3:]}"
        sid = safe_config.get('tencent_secret_id', '')
        if len(sid) > 10: safe_config['tencent_secret_id'] = f"{sid[:6]}***{sid[-4:]}"
        return jsonify(safe_config)

@app.route('/api/stats', methods=['GET'])
def api_stats():
    return jsonify(load_stats())

@app.route('/api/voices', methods=['GET'])
def api_voices():
    provider = request.args.get('provider')
    if provider == 'tencent': return jsonify(TENCENT_VOICES)
    if provider == 'doubao': return jsonify(DOUBAO_VOICES)
    return jsonify(EDGE_VOICES) # Default to Edge

@app.route('/')
def index():
    config = load_config()
    # Mask sensitive data before passing to template
    appid = config.get('appid', '')
    if len(appid) > 6: appid = f"{appid[:3]}***{appid[-3:]}"
    sid = config.get('tencent_secret_id', '')
    if len(sid) > 10: sid = f"{sid[:6]}***{sid[-4:]}"
    
    return render_template_string(HTML_TEMPLATE,
        server_ip=request.host.split(':')[0],
        provider=config.get('provider', 'edge'),
        has_token=bool(config.get('access_token')),
        has_tencent_key=bool(config.get('tencent_secret_key')),
        default_voice=config.get('default_voice'),
        tencent_voice=config.get('tencent_voice'),
        edge_voice=config.get('edge_voice'),
        appid=appid,
        tencent_secret_id=sid
    )

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>TTS 服务</title>
    <style>
        :root { --primary-color: #007bff; --card-bg: #fff; --body-bg: #f7f8fa; --text-color: #333; --border-color: #ddd; }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; background-color: var(--body-bg); color: var(--text-color); line-height: 1.6; }
        .container { max-width: 800px; margin: 20px auto; padding: 0 15px; }
        .card { background: var(--card-bg); border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); }
        h1, h2 { margin-bottom: 16px; font-weight: 600; }
        h1 { text-align: center; font-size: 28px; }
        h2 { font-size: 20px; }
        .provider-select { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 20px; }
        .provider-btn { padding: 12px; border: 2px solid var(--border-color); border-radius: 8px; cursor: pointer; text-align: center; background: var(--card-bg); transition: all 0.2s ease; }
        .provider-btn.active { border-color: var(--primary-color); background: #e7f1ff; box-shadow: 0 0 0 2px rgba(0,123,255,0.2); }
        .provider-btn strong { display: block; font-size: 16px; }
        .status { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; margin-top: 6px; }
        .status-ok { background: #d4edda; color: #155724; }
        .status-error { background: #f8d7da; color: #721c24; }
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 16px; }
        .stat-item { text-align: center; }
        .stat-value { font-size: 26px; font-weight: 700; color: var(--primary-color); }
        .stat-label { font-size: 14px; color: #666; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; margin-bottom: 6px; font-weight: 500; }
        .form-group input, .form-group select { width: 100%; padding: 10px 12px; border: 1px solid var(--border-color); border-radius: 6px; font-size: 14px; }
        .btn { padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; background: var(--primary-color); color: #fff; }
        .code-block { background: #2d2d2d; color: #f8f8f2; padding: 16px; border-radius: 8px; font-family: "SF Mono", "Fira Code", monospace; font-size: 13px; white-space: pre-wrap; word-break: break-all; }
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); padding: 12px 24px; background: rgba(0,0,0,0.8); color: #fff; border-radius: 8px; display: none; z-index: 1000; }
    </style>
</head>
<body>
<div class="container">
    <h1>TTS 服务</h1>
    <div class="card">
        <h2>服务商选择</h2>
        <div class="provider-select">
            <div class="provider-btn" data-provider="edge" onclick="setProvider('edge')">
                <strong>Edge</strong>
                <div class="status status-ok">免费</div>
            </div>
            <div class="provider-btn" data-provider="doubao" onclick="setProvider('doubao')">
                <strong>火山引擎</strong>
                <div class="status" id="doubao-status"></div>
            </div>
            <div class="provider-btn" data-provider="tencent" onclick="setProvider('tencent')">
                <strong>腾讯云</strong>
                <div class="status" id="tencent-status"></div>
            </div>
        </div>
    </div>
    <div class="card">
        <h2>使用统计</h2>
        <div class="stat-grid">
            <div class="stat-item"><div class="stat-value" id="total-chars">-</div><div class="stat-label">总字符</div></div>
            <div class="stat-item"><div class="stat-value" id="total-requests">-</div><div class="stat-label">总请求</div></div>
            <div class="stat-item"><div class="stat-value" id="today-chars">-</div><div class="stat-label">今日字符</div></div>
            <div class="stat-item"><div class="stat-value" id="today-requests">-</div><div class="stat-label">今日请求</div></div>
        </div>
    </div>
    <div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
            <h2 style="margin:0">开源阅读配置</h2>
            <div style="display:flex;align-items:center;gap:8px">
                <label for="voice-select" style="font-weight:500">音色选择</label>
                <select id="voice-select" onchange="updateLegadoConfig()" style="padding:6px 12px;border:1px solid var(--border-color);border-radius:6px;font-size:14px;"></select>
            </div>
        </div>
        <p style="color:#666;margin-bottom:12px">复制以下配置到开源阅读的朗读引擎，即可使用上方选择的音色。</p>
        <div class="code-block" id="legado-config"></div>
    </div>
    <div class="card">
        <h2>API 设置</h2>
        <div id="doubao-settings" style="display:none;">
            <div class="form-group"><label>App ID</label><input type="text" id="appid" value="{{ appid }}"></div>
            <div class="form-group"><label>Access Token</label><input type="password" id="access-token" placeholder=""></div>
        </div>
        <div id="tencent-settings" style="display:none;">
            <div class="form-group"><label>SecretId</label><input type="text" id="tencent-secret-id" value="{{ tencent_secret_id }}"></div>
            <div class="form-group"><label>SecretKey</label><input type="password" id="tencent-secret-key" placeholder=""></div>
        </div>
        <div id="api-settings-footer">
            <button class="btn" onclick="saveConfig()" style="display:none;">保存设置</button>
            <p id="api-note" style="color:#666;font-size:12px;margin-top:12px;display:none;"></p>
        </div>
    </div>
    <div class="card">
        <h2>测试</h2>
        <div class="form-group"><input type="text" id="test-text" value="你好，这是一段测试语音。" style="margin-bottom:12px"></div>
        <button class="btn" onclick="testTTS()">播放测试</button>
        <audio id="audio-player" style="margin-top:12px;width:100%;"></audio>
    </div>
</div>
<div class="toast" id="toast"></div>
"""

HTML_TEMPLATE_JS = """
<script>
document.addEventListener('DOMContentLoaded', () => {
    const serverIp = '{{ server_ip }}';
    let currentProvider = '{{ provider }}';
    const defaultVoices = { doubao: '{{ default_voice }}', tencent: '{{ tencent_voice }}', edge: '{{ edge_voice }}' };
    let allStats = {};

    const elements = {
        doubaoStatus: document.getElementById('doubao-status'),
        tencentStatus: document.getElementById('tencent-status'),
        voiceSelect: document.getElementById('voice-select'),
        legadoConfig: document.getElementById('legado-config'),
        doubaoSettings: document.getElementById('doubao-settings'),
        tencentSettings: document.getElementById('tencent-settings'),
        apiSettingsFooter: document.getElementById('api-settings-footer'),
        saveButton: document.querySelector('#api-settings-footer button'),
        apiNote: document.getElementById('api-note'),
        accessTokenInput: document.getElementById('access-token'),
        tencentKeyInput: document.getElementById('tencent-secret-key'),
    };
    
    function setConfigStatus(hasDoubao, hasTencent) {
        elements.doubaoStatus.textContent = hasDoubao ? '已配置' : '未配置';
        elements.doubaoStatus.className = 'status ' + (hasDoubao ? 'status-ok' : 'status-error');
        elements.tencentStatus.textContent = hasTencent ? '已配置' : '未配置';
        elements.tencentStatus.className = 'status ' + (hasTencent ? 'status-ok' : 'status-error');
        elements.accessTokenInput.placeholder = hasDoubao ? '已配置，留空保持不变' : '请输入 Access Token';
        elements.tencentKeyInput.placeholder = hasTencent ? '已配置，留空保持不变' : '请输入 SecretKey';
    }

    async function loadStats() {
        try {
            const res = await fetch('/api/stats');
            allStats = await res.json();
            updateStatsDisplay();
        } catch (e) { console.error("Error loading stats:", e); }
    }

    function updateStatsDisplay() {
        const data = allStats[currentProvider] || {total_chars:0, total_requests:0, history:[]};
        ['total-chars', 'total-requests', 'today-chars', 'today-requests'].forEach(id => {
            document.getElementById(id).textContent = '0';
        });
        document.getElementById('total-chars').textContent = data.total_chars.toLocaleString();
        document.getElementById('total-requests').textContent = data.total_requests.toLocaleString();
        const today = new Date().toISOString().split('T')[0];
        const todayData = data.history.find(d => d.date === today) || {chars:0, requests:0};
        document.getElementById('today-chars').textContent = todayData.chars.toLocaleString();
        document.getElementById('today-requests').textContent = todayData.requests.toLocaleString();
    }

    async function populateVoiceSelect(provider) {
        try {
            const res = await fetch(`/api/voices?provider=${provider}`);
            const voices = await res.json();
            elements.voiceSelect.innerHTML = '';
            voices.forEach(v => {
                const opt = document.createElement('option');
                opt.value = v.id;
                opt.textContent = v.name;
                if (v.id === defaultVoices[provider]) {
                    opt.selected = true;
                }
                elements.voiceSelect.appendChild(opt);
            });
            window.updateLegadoConfig();
        } catch (e) { console.error(`Error populating voices for ${provider}:`, e); }
    }

    window.updateLegadoConfig = function() {
        const voice = elements.voiceSelect.value;
        if (!voice) return;
        const lb = String.fromCharCode(123,123), rb = String.fromCharCode(125,125);
        const config = "名称: TTS服务\\nurl: http://" + serverIp + "/speech/stream,{\\"method\\":\\"POST\\",\\"body\\":{\\"text\\":\\"" + lb + "speakText" + rb + "\\",\\"voice\\":\\"" + voice + "\\",\\"rate\\":\\"" + lb + "String(speakSpeed)" + rb + "%\\"},\\"headers\\":{\\"Content-Type\\":\\"application/json\\"}}\\nContent-Type: audio/mp3\\n并发率: 0";
        elements.legadoConfig.textContent = config;
    }

    window.setProvider = function(provider) {
        currentProvider = provider;
        document.querySelectorAll('.provider-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.provider === provider));
        
        elements.doubaoSettings.style.display = provider === 'doubao' ? 'block' : 'none';
        elements.tencentSettings.style.display = provider === 'tencent' ? 'block' : 'none';
        
        if (provider === 'edge') {
            elements.saveButton.style.display = 'none';
            elements.apiNote.textContent = 'Edge TTS 免费使用，无需配置。';
            elements.apiNote.style.display = 'block';
        } else {
            elements.saveButton.style.display = 'block';
            elements.apiNote.style.display = 'none';
        }
        
        updateStatsDisplay();
        populateVoiceSelect(provider);
        fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider})});
    }

    window.saveConfig = async function() {
        let data = { provider: currentProvider };
        let newDefault = elements.voiceSelect.value;
        if (currentProvider === 'doubao') {
            data.appid = document.getElementById('appid').value;
            data.access_token = elements.accessTokenInput.value || '***';
            data.default_voice = newDefault;
        } else if (currentProvider === 'tencent') {
            data.tencent_secret_id = document.getElementById('tencent-secret-id').value;
            data.tencent_secret_key = elements.tencentKeyInput.value || '***';
            data.tencent_voice = newDefault;
        }
        await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
        showToast('设置已保存');
        setTimeout(() => location.reload(), 1000);
    }

    window.testTTS = async function() {
        const text = document.getElementById('test-text').value;
        const voice = elements.voiceSelect.value;
        try {
            const res = await fetch('/speech/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text, voice, rate:'0%'})});
            if (res.ok) {
                const blob = await res.blob();
                document.getElementById('audio-player').src = URL.createObjectURL(blob);
                document.getElementById('audio-player').play();
            } else { showToast(`TTS失败: ${await res.text()}`); }
        } catch (e) { showToast(`请求错误: ${e.message}`); }
    }

    function showToast(msg) {
        const t = document.getElementById('toast');
        t.textContent = msg;
        t.style.display = 'block';
        setTimeout(() => t.style.display = 'none', 3000);
    }

    // Initial Load
    setConfigStatus({{ 'true' if has_token else 'false' }}, {{ 'true' if has_tencent_key else 'false' }});
    loadStats();
    window.setProvider(currentProvider);
});
</script>
</body>
</html>
"""

if __name__ == '__main__':
    # Append JS to main template
    final_template = HTML_TEMPLATE + HTML_TEMPLATE_JS
    
    # Overwrite the global template variable before running the app
    globals()['HTML_TEMPLATE'] = final_template
    
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    app.run(host='0.0.0.0', port=80, threaded=True)

