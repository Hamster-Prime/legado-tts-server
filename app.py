#!/usr/bin/env python3
"""TTS服务 - 支持火山引擎、腾讯云、Edge TTS"""

import os, json, base64, uuid, hmac, hashlib, time, asyncio, io
from datetime import datetime
from flask import Flask, request, Response, render_template_string, jsonify
import requests
import edge_tts

app = Flask(__name__)
CONFIG_FILE = '/opt/doubao-tts/config.json'
STATS_FILE = '/opt/doubao-tts/stats.json'

DEFAULT_CONFIG = {
    'provider': 'doubao', 'appid': '', 'access_token': '',
    'default_voice': 'zh_female_cancan_mars_bigtts', 'cluster': 'volcano_tts',
    'tencent_secret_id': '', 'tencent_secret_key': '',
    'tencent_voice': '501002', 'tencent_region': 'ap-guangzhou',
    'edge_voice': 'zh-CN-XiaoxiaoNeural'
}

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
]

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg: cfg[k] = v
            return cfg
    return DEFAULT_CONFIG.copy()

def save_config(config):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, 'w') as f: json.dump(config, f, indent=2)

def load_stats():
    if os.path.exists(STATS_FILE):
        with open(STATS_FILE, 'r') as f:
            data = json.load(f)
            if 'doubao' not in data:
                data = {'doubao': {'total_chars': data.get('total_chars',0), 'total_requests': data.get('total_requests',0), 'history': data.get('history',[])}, 'tencent': {'total_chars':0,'total_requests':0,'history':[]}, 'edge': {'total_chars':0,'total_requests':0,'history':[]}}
            if 'edge' not in data:
                data['edge'] = {'total_chars':0,'total_requests':0,'history':[]}
            return data
    return {'doubao': {'total_chars':0,'total_requests':0,'history':[]}, 'tencent': {'total_chars':0,'total_requests':0,'history':[]}, 'edge': {'total_chars':0,'total_requests':0,'history':[]}}

def save_stats(stats):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    with open(STATS_FILE, 'w') as f: json.dump(stats, f, indent=2)

def update_stats(chars, provider='doubao'):
    stats = load_stats()
    p = stats.get(provider, {'total_chars':0,'total_requests':0,'history':[]})
    p['total_chars'] += chars
    p['total_requests'] += 1
    today = datetime.now().strftime('%Y-%m-%d')
    found = False
    for d in p['history']:
        if d['date'] == today: d['chars'] += chars; d['requests'] += 1; found = True; break
    if not found: p['history'].append({'date': today, 'chars': chars, 'requests': 1})
    p['history'] = p['history'][-30:]
    stats[provider] = p
    save_stats(stats)

def synthesize_doubao(text, voice, speed_ratio=1.0):
    config = load_config()
    if not config.get('appid') or not config.get('access_token'):
        return None, "未配置豆包appid或token"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer; {config['access_token']}"}
    payload = {"app": {"appid": config["appid"], "token": "placeholder", "cluster": config.get("cluster", "volcano_tts")},
               "user": {"uid": "legado_user"}, "audio": {"voice_type": voice, "encoding": "mp3", "speed_ratio": speed_ratio, "rate": 24000},
               "request": {"reqid": str(uuid.uuid4()), "text": text, "operation": "query"}}
    try:
        resp = requests.post("https://openspeech.bytedance.com/api/v1/tts", headers=headers, json=payload, timeout=30)
        result = resp.json()
        if result.get("code") != 3000: return None, f"豆包API错误: {result.get('message', 'Unknown')}"
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
        return asyncio.run(_synthesize()), None
    except Exception as e:
        return None, str(e)

@app.route('/speech/stream', methods=['POST'])
def speech_stream():
    try:
        data = request.json
        text = data.get('text', '')
        if not text: return Response('No text', status=400)
        config = load_config()
        provider = config.get('provider', 'doubao')
        rate = data.get('rate', '0%')
        speed_ratio, speed_val = 1.0, 0
        if rate:
            try:
                pct = float(rate.replace('%','').replace('+',''))
                speed_ratio = max(0.2, min(3.0, 1.0 + pct/100))
                speed_val = max(-2, min(6, pct/50))
            except: pass
        if provider == 'tencent':
            voice = data.get('voice', config.get('tencent_voice', '501002'))
            audio, error = synthesize_tencent(text, voice, speed_val)
        elif provider == 'edge':
            voice = data.get('voice', config.get('edge_voice', 'zh-CN-XiaoxiaoNeural'))
            edge_rate = f'+{int(pct)}%' if pct >= 0 else f'{int(pct)}%'
            audio, error = synthesize_edge(text, voice, edge_rate)
        else:
            voice = data.get('voice', config.get('default_voice'))
            audio, error = synthesize_doubao(text, voice, speed_ratio)
        if audio:
            update_stats(len(text), provider)
            return Response(audio, mimetype='audio/mp3')
        return Response(f'TTS failed: {error}', status=500)
    except Exception as e: return Response(f'Error: {e}', status=500)

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    if request.method == 'GET':
        config = load_config()
        if config.get('access_token'): config['access_token'] = '********'
        if config.get('tencent_secret_key'): config['tencent_secret_key'] = '********'
        appid = config.get('appid', '')
        if len(appid) > 6: config['appid'] = appid[:3] + '*'*(len(appid)-6) + appid[-3:]
        sid = config.get('tencent_secret_id', '')
        if len(sid) > 10: config['tencent_secret_id'] = sid[:6] + '*'*(len(sid)-10) + sid[-4:]
        return jsonify(config)
    else:
        data = request.json
        config = load_config()
        if data.get('provider'): config['provider'] = data['provider']
        if data.get('appid') and '*' not in data['appid']: config['appid'] = data['appid']
        if data.get('access_token') and '*' not in data['access_token']: config['access_token'] = data['access_token']
        if data.get('default_voice'): config['default_voice'] = data['default_voice']
        if data.get('tencent_secret_id') and '*' not in data['tencent_secret_id']: config['tencent_secret_id'] = data['tencent_secret_id']
        if data.get('tencent_secret_key') and '*' not in data['tencent_secret_key']: config['tencent_secret_key'] = data['tencent_secret_key']
        if data.get('tencent_voice'): config['tencent_voice'] = data['tencent_voice']
        if data.get('edge_voice'): config['edge_voice'] = data['edge_voice']
        save_config(config)
        return jsonify({'status': 'ok'})

@app.route('/api/stats', methods=['GET'])
def api_stats():
    return jsonify(load_stats())

@app.route('/api/voices', methods=['GET'])
def api_voices():
    provider = request.args.get('provider', 'doubao')
    if provider == 'tencent': return jsonify(TENCENT_VOICES)
    elif provider == 'edge': return jsonify(EDGE_VOICES)
    return jsonify(DOUBAO_VOICES)

@app.route('/')
def index():
    config = load_config()
    server_ip = request.host.split(':')[0]
    appid = config.get('appid', '')
    if len(appid) > 6: appid = appid[:3] + '*'*(len(appid)-6) + appid[-3:]
    sid = config.get('tencent_secret_id', '')
    if len(sid) > 10: sid = sid[:6] + '*'*(len(sid)-10) + sid[-4:]
    return render_template_string(HTML_TEMPLATE,
        server_ip=server_ip, provider=config.get('provider','doubao'),
        has_doubao=bool(config.get('appid') and config.get('access_token')),
        has_tencent=bool(config.get('tencent_secret_id') and config.get('tencent_secret_key')),
        has_token=bool(config.get('access_token')), has_tencent_key=bool(config.get('tencent_secret_key')),
        default_voice=config.get('default_voice',''), tencent_voice=config.get('tencent_voice','501002'),
        edge_voice=config.get('edge_voice','zh-CN-XiaoxiaoNeural'),
        appid=appid, tencent_secret_id=sid)

HTML_TEMPLATE = '''<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TTS服务</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f5}
.container{max-width:900px;margin:0 auto;padding:20px}
.card{background:#fff;border-radius:12px;padding:24px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.card h2{color:#333;margin-bottom:16px;font-size:18px}
.stat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:16px}
.stat-item{text-align:center;padding:16px;background:#f8f9fa;border-radius:8px}
.stat-value{font-size:28px;font-weight:bold;color:#007bff}
.stat-label{color:#666;font-size:14px;margin-top:4px}
.form-group{margin-bottom:16px}
.form-group label{display:block;margin-bottom:6px;color:#333;font-weight:500}
.form-group input,.form-group select{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px}
.btn{padding:10px 20px;border:none;border-radius:6px;cursor:pointer;font-size:14px}
.btn-primary{background:#007bff;color:#fff}
.code-block{background:#2d2d2d;color:#f8f8f2;padding:16px;border-radius:8px;font-family:monospace;font-size:12px;overflow-x:auto;white-space:pre-wrap;word-break:break-all}
.status{display:inline-block;padding:4px 12px;border-radius:20px;font-size:12px}
.status-ok{background:#d4edda;color:#155724}
.status-error{background:#f8d7da;color:#721c24}
.toast{position:fixed;top:20px;right:20px;padding:12px 24px;background:#333;color:#fff;border-radius:6px;display:none;z-index:999}
.provider-select{display:flex;gap:12px;margin-bottom:16px}
.provider-btn{flex:1;padding:12px;border:2px solid #ddd;border-radius:8px;cursor:pointer;text-align:center;background:#fff}
.provider-btn.active{border-color:#007bff;background:#e7f1ff}
</style></head>
<body><div class="container">
<h1 style="margin:20px 0;color:#333">TTS服务</h1>
<div class="card">
<h2>服务商选择</h2>
<div class="provider-select">
<div class="provider-btn {% if provider == 'edge' %}active{% endif %}" data-provider="edge" onclick="setProvider('edge')">
<strong>Edge</strong><br>
<span class="status status-ok">免费</span>
</div>
<div class="provider-btn {% if provider == 'doubao' %}active{% endif %}" data-provider="doubao" onclick="setProvider('doubao')">
<strong>火山引擎</strong><br>
<span class="status {% if has_doubao %}status-ok{% else %}status-error{% endif %}">{% if has_doubao %}已配置{% else %}未配置{% endif %}</span>
</div>
<div class="provider-btn {% if provider == 'tencent' %}active{% endif %}" data-provider="tencent" onclick="setProvider('tencent')">
<strong>腾讯云</strong><br>
<span class="status {% if has_tencent %}status-ok{% else %}status-error{% endif %}">{% if has_tencent %}已配置{% else %}未配置{% endif %}</span>
</div>
</div>
</div>
<div class="card">
<h2>使用统计</h2>
<div class="stat-grid">
<div class="stat-item"><div class="stat-value" id="total-chars">-</div><div class="stat-label">总字符数</div></div>
<div class="stat-item"><div class="stat-value" id="total-requests">-</div><div class="stat-label">总请求数</div></div>
<div class="stat-item"><div class="stat-value" id="today-chars">-</div><div class="stat-label">今日字符</div></div>
<div class="stat-item"><div class="stat-value" id="today-requests">-</div><div class="stat-label">今日请求</div></div>
</div>
</div>
<div class="card">
<h2>开源阅读配置</h2>
<p style="color:#666;margin-bottom:12px">复制以下配置到开源阅读的朗读引擎：</p>
<div class="code-block" id="legado-config"></div>
</div>
<div class="card">
<h2>API设置</h2>
<div id="doubao-settings" style="display:{% if provider == 'doubao' %}block{% else %}none{% endif %}">
<div class="form-group"><label>App ID</label><input type="text" id="appid" value="{{ appid }}" placeholder="火山引擎AppID"></div>
<div class="form-group"><label>Access Token</label><input type="password" id="access-token" placeholder="{% if has_token %}已配置，留空保持不变{% else %}火山引擎Access Token{% endif %}"></div>
<div class="form-group"><label>默认音色</label><select id="doubao-voice"></select></div>
</div>
<div id="tencent-settings" style="display:{% if provider == 'tencent' %}block{% else %}none{% endif %}">
<div class="form-group"><label>SecretId</label><input type="text" id="tencent-secret-id" value="{{ tencent_secret_id }}" placeholder="腾讯云SecretId"></div>
<div class="form-group"><label>SecretKey</label><input type="password" id="tencent-secret-key" placeholder="{% if has_tencent_key %}已配置，留空保持不变{% else %}腾讯云SecretKey{% endif %}"></div>
<div class="form-group"><label>默认音色</label><select id="tencent-voice"></select></div>
</div>
<div id="edge-settings" style="display:{% if provider == 'edge' %}block{% else %}none{% endif %}">
<div class="form-group"><label>默认音色</label><select id="edge-voice"></select></div>
<p style="color:#666;font-size:12px;margin-top:8px">Edge TTS 免费使用，无需配置API密钥</p>
</div>
<button class="btn btn-primary" onclick="saveConfig()">保存设置</button>
</div>
<div class="card">
<h2>测试</h2>
<div class="form-group"><input type="text" id="test-text" value="你好，这是一段测试语音。" style="margin-bottom:12px"></div>
<button class="btn btn-primary" onclick="testTTS()">播放测试</button>
<audio id="audio-player" style="margin-left:12px"></audio>
</div>
</div>
<div class="toast" id="toast"></div>
<script>
const serverIp = '{{ server_ip }}';
let currentProvider = '{{ provider }}';
const defaultDoubaoVoice = '{{ default_voice }}';
const defaultTencentVoice = '{{ tencent_voice }}';
const defaultEdgeVoice = '{{ edge_voice }}';
let allStats = {};

async function loadStats() {
    const res = await fetch('/api/stats');
    allStats = await res.json();
    updateStatsDisplay();
}

function updateStatsDisplay() {
    const data = allStats[currentProvider] || {total_chars:0,total_requests:0,history:[]};
    document.getElementById('total-chars').textContent = data.total_chars.toLocaleString();
    document.getElementById('total-requests').textContent = data.total_requests.toLocaleString();
    const today = new Date().toISOString().split('T')[0];
    const todayData = data.history.find(d => d.date === today) || {chars:0,requests:0};
    document.getElementById('today-chars').textContent = todayData.chars.toLocaleString();
    document.getElementById('today-requests').textContent = todayData.requests.toLocaleString();
}

async function loadVoices(provider, selectId, defaultVoice) {
    const res = await fetch('/api/voices?provider=' + provider);
    const voices = await res.json();
    const select = document.getElementById(selectId);
    select.innerHTML = '';
    voices.forEach(v => {
        const opt = document.createElement('option');
        opt.value = v.id; opt.textContent = v.name;
        if (v.id === defaultVoice) opt.selected = true;
        select.appendChild(opt);
    });
}

function updateLegadoConfig() {
    const voice = currentProvider === 'tencent' ? document.getElementById('tencent-voice').value : currentProvider === 'edge' ? document.getElementById('edge-voice').value : document.getElementById('doubao-voice').value;
    const lb = String.fromCharCode(123,123), rb = String.fromCharCode(125,125);
    const config = "名称: TTS服务\\nurl: http://" + serverIp + "/speech/stream,{\\\"method\\\":\\\"POST\\\",\\\"body\\\":{\\\"text\\\":\\\"" + lb + "speakText" + rb + "\\\",\\\"voice\\\":\\\"" + voice + "\\\",\\\"rate\\\":\\\"" + lb + "String(speakSpeed)" + rb + "%\\\"},\\\"headers\\\":{\\\"Content-Type\\\":\\\"application/json\\\"}}\\nContent-Type: audio/mp3\\n并发率: 0";
    document.getElementById('legado-config').textContent = config;
}

function setProvider(provider) {
    currentProvider = provider;
    document.querySelectorAll('.provider-btn').forEach(btn => btn.classList.toggle('active', btn.dataset.provider === provider));
    document.getElementById('doubao-settings').style.display = provider === 'doubao' ? 'block' : 'none';
    document.getElementById('tencent-settings').style.display = provider === 'tencent' ? 'block' : 'none';
    document.getElementById('edge-settings').style.display = provider === 'edge' ? 'block' : 'none';
    updateStatsDisplay();
    updateLegadoConfig();
    fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider})});
}

async function saveConfig() {
    let data;
    if (currentProvider === 'tencent') {
        data = {tencent_secret_id: document.getElementById('tencent-secret-id').value, tencent_secret_key: document.getElementById('tencent-secret-key').value || '***', tencent_voice: document.getElementById('tencent-voice').value};
    } else if (currentProvider === 'edge') {
        data = {edge_voice: document.getElementById('edge-voice').value};
    } else {
        data = {appid: document.getElementById('appid').value, access_token: document.getElementById('access-token').value || '***', default_voice: document.getElementById('doubao-voice').value};
    }
    await fetch('/api/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
    showToast('设置已保存'); location.reload();
}

async function testTTS() {
    const text = document.getElementById('test-text').value;
    const voice = currentProvider === 'tencent' ? document.getElementById('tencent-voice').value : currentProvider === 'edge' ? document.getElementById('edge-voice').value : document.getElementById('doubao-voice').value;
    const res = await fetch('/speech/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({text, voice, rate:'0%'})});
    if (res.ok) {
        const blob = await res.blob();
        document.getElementById('audio-player').src = URL.createObjectURL(blob);
        document.getElementById('audio-player').play();
    } else { showToast('TTS失败: ' + await res.text()); }
}

function showToast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.style.display = 'block'; setTimeout(() => t.style.display = 'none', 3000); }

loadStats(); loadVoices('doubao','doubao-voice',defaultDoubaoVoice); loadVoices('tencent','tencent-voice',defaultTencentVoice); loadVoices('edge','edge-voice',defaultEdgeVoice);
document.getElementById('doubao-voice').addEventListener('change', updateLegadoConfig);
document.getElementById('tencent-voice').addEventListener('change', updateLegadoConfig);
document.getElementById('edge-voice').addEventListener('change', updateLegadoConfig);
setTimeout(updateLegadoConfig, 500); setInterval(loadStats, 30000);
</script></body></html>'''

if __name__ == '__main__':
    os.makedirs('/opt/doubao-tts', exist_ok=True)
    app.run(host='0.0.0.0', port=80, threaded=True)
