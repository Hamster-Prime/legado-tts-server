# 📖 Legado TTS Server

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Version](https://img.shields.io/badge/version-1.7.0-green.svg)

**为开源阅读 (Legado) 量身打造的聚合语音合成服务**

</div>

---

## ✨ 特性

- **🎯 多源聚合**：Edge TTS (免费)、火山引擎、腾讯云、小米MiMo、Fish Audio
- **🧠 智能路由**：单一接口，根据音色参数自动分发
- **🔗 OpenAI 兼容**：`/v1/audio/speech` + `/v1/models`，支持 MP3/WAV/OGG 输出
- **📦 长文本分块**：自动按句子边界拆分，逐段合成再拼接
- **🔊 SSML 支持**：Edge TTS 原生 SSML 1.0 语法（可通过 `ALLOW_SSML` 关闭）
- **📦 批量合成**：`/api/speech/batch` 一次请求合成最多 20 段文本
- **💾 智能缓存**：双限制 LRU（条目数 + 内存大小），避免重复合成
- **🛡️ 安全保护**：ADMIN_TOKEN 管理接口认证 + IP 滑动窗口限流
- **📊 Prometheus 监控**：`/metrics` 导出请求数、字符数、缓存命中率、P95 响应时间
- **⚡ Gzip 压缩**：所有 HTML/JSON/TEXT 响应自动压缩
- **🔗 Legado 订阅**：`/api/legado/subscribe` 一键生成订阅链接
- **💻 Web 管理界面**：配置、测试、统计、实时系统状态面板（每 30 秒刷新）
- **🎧 多格式输出**：MP3 / WAV / OGG（需 FFmpeg）
- **🔄 自动故障转移**：主 Provider 失败时自动 fallback 到 Edge TTS
- **📖 发音词典**：自定义词语发音替换，解决生僻字/专有名词误读
- **📡 实时监控**：SSE 事件流 + 审计日志，WebUI 实时活动面板
- **🔍 音色别名**：支持 OpenAI 音色名称、中文名称、自定义别名
- **💾 配置导出/导入**：一键备份和迁移配置

## 🎧 支持音色

| 服务商 | 音色数 | 说明 |
|--------|--------|------|
| Edge TTS | 36+ | 免费，中/英/日/韩/粤语/台湾腔 |
| 火山引擎 | 8 | 灿灿、思思、贴心女生等 |
| 腾讯云 | 7 | 智菊、智斌、智兰等 |
| 小米 MiMo | 3 | 风格控制、方言、歌声合成 |
| Fish Audio | 5 | 高质量多语言 + 声音克隆 |

---

## 🛠 安装

```bash
git clone https://github.com/Hamster-Prime/legado-tts-server.git
cd legado-tts-server
pip install -r requirements.txt
python3 app.py
```

## 🚀 部署

### Docker
```bash
docker build -t legado-tts .
docker run -d --name legado-tts -p 80:80 -v tts-data:/opt/doubao-tts legado-tts
```

### Docker Compose
```bash
docker compose up -d
```

### Gunicorn (生产)
```bash
gunicorn -c gunicorn.conf.py app:app
```

### Systemd
```bash
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl enable --now legado-tts
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `80` | 服务端口 |
| `CONFIG_FILE` | `/opt/doubao-tts/config.json` | 配置文件路径 |
| `STATS_FILE` | `/opt/doubao-tts/stats.json` | 统计文件路径 |
| `MAX_TEXT_LENGTH` | `5000` | 单次合成最大文本长度 |
| `CHUNK_SIZE` | `500` | 长文本分块大小(字符) |
| `AUDIO_CACHE_SIZE` | `100` | 缓存最大条目数 |
| `AUDIO_CACHE_MAX_MB` | `200` | 缓存最大内存(MB) |
| `RATE_LIMIT_RPM` | `120` | 每IP每分钟请求限制(0=不限) |
| `ADMIN_TOKEN` | `""` | 管理API认证Token |
| `ALLOW_SSML` | `1` | 允许SSML输入(1=是/0=否) |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `FALLBACK_TO_EDGE` | `1` | 启用自动故障转移(1=是/0=否) |
| `REQUEST_TIMEOUT` | `30` | 单次Provider请求超时(秒) |
| `AUDIT_LOG_SIZE` | `200` | 审计日志保留条数 |
| `WEBHOOK_URL` | `""` | Webhook通知URL(可选) |
| `WEBHOOK_EVENTS` | `error` | Webhook事件类型(逗号分隔) |
| `LOG_JSON` | `0` | 启用JSON结构化日志(1=是) |
| `USE_GUNICORN` | `1` | Docker中使用gunicorn(1=是/0=否) |

---

## 🔌 API 文档

### Legado TTS 接口
`POST /speech/stream`

```json
{"text": "需要朗读的文本", "voice": "zh-CN-XiaoxiaoNeural", "rate": "+50%"}
```

### OpenAI 兼容接口
`POST /v1/audio/speech`

```json
{"model": "tts-1", "input": "Hello", "voice": "zh-CN-XiaoxiaoNeural", "speed": 1.5, "response_format": "mp3"}
```

### 批量合成
`POST /api/speech/batch`

```json
{"voice": "zh-CN-XiaoxiaoNeural", "texts": ["你好", "世界"], "rate": "0%"}
```

返回：
```json
{"results": [{"text": "你好", "audio": "base64...", "error": null}, ...]}
```

### Legado 订阅
- `GET /api/legado/config?voice=xxx` — 生成 Legado 配置 JSON
- `GET /api/legado/subscribe?voice=xxx&auto=true` — 生成订阅链接

### Prometheus 监控
`GET /metrics` — 导出 `tts_requests_total`、`tts_chars_total`、`tts_cache_hit_ratio`、`tts_response_time_ms_p95` 等

### 管理接口 (需要 ADMIN_TOKEN)
- `GET/POST /api/config` — 获取/修改配置
- `POST /api/config/test` — 测试当前配置
- `DELETE /api/stats` — 重置统计
- `DELETE /api/cache/clear` — 清除缓存
- `GET/POST /api/config/export` — 导出配置
- `POST /api/config/import` — 导入配置
- `GET/POST/DELETE /api/pronunciation` — 管理发音词典
- `GET /api/audit` — 查看请求审计日志
- `GET /api/events` — SSE 实时事件流
- `GET /api/voices/edge/live` — 动态获取 Edge TTS 全部语音

### 音频路由规则
| 规则 | Provider |
|------|----------|
| `voice` 包含 `Neural` 且有 `-` | Edge TTS |
| `voice` 为纯数字 1-999999 | 腾讯云 |
| `voice` 以 `zh_` 开头 | 火山引擎 |
| `voice` 为 `mimo_*` / `default_zh` / `default_eh` | 小米 MiMo |
| `voice` 以 `fish-` 开头或为 `custom` | Fish Audio |

---

## 📄 License

MIT License © 2024 Hamster-Prime
