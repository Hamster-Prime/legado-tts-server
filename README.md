# Legado TTS Server

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Version](https://img.shields.io/badge/version-1.8.0-green.svg)
![Tests](https://img.shields.io/badge/tests-100%20passed-brightgreen.svg)

**为开源阅读 (Legado) 量身打造的聚合语音合成服务**

</div>

---

## 特性

**核心能力**
- 多源聚合：Edge TTS (免费)、火山引擎、腾讯云、小米MiMo、Fish Audio
- 智能路由：单一接口，根据音色参数自动分发到对应 provider
- OpenAI 兼容：`/v1/audio/speech` + `/v1/models`，支持 MP3/WAV/OGG 输出
- 长文本分块：自动按句子边界拆分，逐段合成再拼接
- 批量合成：`/api/speech/batch` 一次请求合成最多 20 段文本
- SSML 支持：Edge TTS 原生 SSML 1.0 语法
- 音频格式转换：MP3 / WAV / OGG（需 FFmpeg）

**文本智能处理**
- 文本正规化：数字、日期、时间、百分比、温度、单位自动转中文
- 发音词典：自定义词语发音替换，解决生僻字/专有名词误读
- 语速预设：支持 `fast`/`slow`/`快速`/`慢速`/`1.5x`/`2x` 等自然语言语速

**运维与安全**
- ADMIN_TOKEN 管理接口认证
- API_KEYS 多密钥访问控制（API_KEYS_REQUIRED=1 强制认证）
- IP 滑动窗口限流 + 白名单（RATE_LIMIT_WHITELIST）
- 自动故障转移：主 Provider 失败时自动 fallback 到 Edge TTS
- 可配置超时：REQUEST_TIMEOUT 环境变量
- Header 注入防护：voice 参数清理 CRLF/null 字节
- 请求 ID 追踪：X-Request-ID 贯穿响应和审计日志

**监控与诊断**
- Prometheus `/metrics` 端点
- 请求审计日志（环形缓冲区，AUDIT_LOG_SIZE 控制）
- SSE 实时事件流（`/api/events`）
- Per-voice 用量统计 + 热门音色排行（`/api/stats/summary`）
- Kubernetes 就绪/存活探针（`/readyz`、`/livez`）
- Webhook 通知：合成失败时推送告警
- JSON 结构化日志（LOG_JSON=1）

**Web 管理界面**
- 配置管理、TTS 测试、统计面板
- 实时活动面板（SSE 推送）
- 音色搜索过滤 + 试听按钮
- 暗色模式切换
- 配置导出/导入

---

## 支持音色

| 服务商 | 音色数 | 说明 |
|--------|--------|------|
| Edge TTS | 322+ | 免费，中/英/日/韩/粤语/台湾腔，支持情感风格、音量、音调 |
| 火山引擎 | 8 | 灿灿、思思、贴心女生等 |
| 腾讯云 | 7 | 智菊、智斌、智兰等 |
| 小米 MiMo | 3 | 风格控制、方言、歌声合成 |
| Fish Audio | 5 | 高质量多语言 + 声音克隆 |

**音色别名**：支持 OpenAI 音色名（alloy/echo/nova）、中文名（晓晓/云希）、倍速（1.5x/2x）等

---

## 快速开始

### 安装
```bash
git clone https://github.com/Hamster-Prime/legado-tts-server.git
cd legado-tts-server
pip install -r requirements.txt
python3 app.py
```

### Docker
```bash
docker build -t legado-tts .
docker run -d --name legado-tts -p 80:80 -v tts-data:/opt/doubao-tts legado-tts
```

### Docker Compose
```bash
docker compose up -d
```

### Gunicorn（生产）
```bash
gunicorn -c gunicorn.conf.py app:app
```

### Systemd
```bash
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl enable --now legado-tts
```

---

## 环境变量

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
| `RATE_LIMIT_WHITELIST` | `1.8.0.0.1,::1` | 限流白名单IP |
| `ADMIN_TOKEN` | `""` | 管理API认证Token |
| `API_KEYS` | `""` | TTS访问密钥(逗号分隔) |
| `API_KEYS_REQUIRED` | `0` | 强制API密钥认证(1=是) |
| `ALLOW_SSML` | `1` | 允许SSML输入(1=是/0=否) |
| `FALLBACK_TO_EDGE` | `1` | 启用自动故障转移(1=是/0=否) |
| `REQUEST_TIMEOUT` | `30` | 单次Provider请求超时(秒) |
| `TEXT_NORMALIZE` | `1` | 启用文本正规化(1=是/0=否) |
| `AUDIT_LOG_SIZE` | `200` | 审计日志保留条数 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `LOG_JSON` | `0` | 启用JSON结构化日志(1=是) |
| `WEBHOOK_URL` | `""` | Webhook通知URL |
| `WEBHOOK_EVENTS` | `error` | Webhook事件类型 |
| `USE_GUNICORN` | `1` | Docker中使用gunicorn(1=是) |
| `DAILY_CHAR_QUOTA` | `0` | 每IP每日字符配额(0=不限) |

---

## API 端点

### TTS 合成
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/speech/stream` | Legado TTS 合成（支持 style/volume/pitch） |
| POST | `/speech/stream/chunked` | Edge TTS 流式合成（低延迟） |
| POST | `/v1/audio/speech` | OpenAI 兼容 TTS |
| POST | `/api/speech/batch` | 批量合成（最多20条） |

### 音色管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/voices?provider=xxx` | 按Provider查询音色 |
| GET | `/api/voices/all` | 所有音色 |
| GET | `/api/voices/edge/live` | Edge TTS 动态语音列表(322+) |
| GET/POST/DELETE | `/api/favorites` | 音色收藏夹 |

### 配置管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/config` | 获取/修改配置 |
| POST | `/api/config/test` | 测试当前配置 |
| GET | `/api/config/export` | 导出配置JSON |
| POST | `/api/config/import` | 导入配置JSON |
| GET/POST/DELETE | `/api/pronunciation` | 发音词典CRUD |

### 监控与诊断
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 健康检查（详细信息） |
| GET | `/livez` | K8s 存活探针 |
| GET | `/readyz` | K8s 就绪探针 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/api/info` | 系统信息总览 |
| GET | `/api/stats` | 用量统计 |
| GET | `/api/stats/summary` | 热门音色排行 |
| DELETE | `/api/stats` | 重置统计 |
| GET | `/api/cache/stats` | 缓存状态 |
| DELETE | `/api/cache/clear` | 清除缓存 |
| GET | `/api/audit` | 审计日志 |
| GET | `/api/events` | SSE 实时事件流 |
| GET | `/api/openapi.json` | OpenAPI 3.0 规范 |

### Legado 专用
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/legado/config` | Legado 配置JSON |
| GET | `/api/legado/subscribe` | Legado 订阅链接 |

### 音频路由规则
| voice 格式 | Provider |
|------------|----------|
| 包含 `Neural` 且有 `-` | Edge TTS |
| 纯数字 1-999999 | 腾讯云 |
| 以 `zh_` 开头 | 火山引擎 |
| `mimo_*` / `default_zh` | 小米 MiMo |
| 以 `fish-` 开头或 `custom` | Fish Audio |
| 别名/中文名 | 自动解析 |

---

## 安全设计

- 所有错误响应返回标准化 JSON（含 request_id）
- 全局 404/405/500 错误处理
- Voice 参数自动清理 CRLF/null 字节（防 Header 注入）
- 已认证请求自动绕过限流
- CORS 开放（TTS 服务需要被任意客户端访问）

## License

MIT License
