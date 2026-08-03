# Legado TTS Server

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Version](https://img.shields.io/badge/version-1.9.0-green.svg)
![Tests](https://img.shields.io/badge/tests-207%20passed-brightgreen.svg)

**为开源阅读 (Legado) 量身打造的聚合语音合成服务**

</div>

---

## 特性

**核心能力**
- 多源聚合：Edge TTS (免费)、火山引擎、腾讯云、小米MiMo、Fish Audio
- 智能路由：单一接口，根据音色参数自动分发到对应 provider
- 听书低延迟：火山引擎 v3 HTTP Chunked 单向流，上游音频分片生成后立即向 Legado 透传
- OpenAI 兼容：`/v1/audio/speech` + `/v1/models`，支持 MP3/WAV/OGG 输出
- 长文本分块：自动按句子边界拆分；流式 Provider 逐段连续输出
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
| Edge TTS | 36 (精选) / 322 (完整) | 免费，中/英/日/韩/粤语/台湾腔，支持情感风格、音量、音调。`/api/voices` 返回精选列表，`/api/voices/edge/live` 返回微软完整列表 |
| 火山引擎 | 11 | 2.0 音色：灿灿、思思、悬疑解说、少儿故事等 |
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
PORT=8080 python3 app.py
```

非 mp3 输出格式（wav/ogg/opus/aac/flac/pcm）需要系统安装 `ffmpeg`；
未安装时自动回退为 mp3，并在 `X-TTS-Format` 中如实标注。

### Docker
```bash
docker build -t legado-tts .
# 容器内以非 root 用户监听 8080，宿主机端口可自行映射
docker run -d --name legado-tts -p 8080:8080 -v tts-data:/opt/doubao-tts legado-tts
```

### Docker Compose
```bash
docker compose up -d              # 默认发布在宿主机 8080
HOST_PORT=80 docker compose up -d # 改用 80
```

### Gunicorn（生产）
```bash
gunicorn -c gunicorn.conf.py app:app
```

限流、每日配额、音频缓存和 `/metrics` 都保存在进程内存中，因此默认配置为
**单进程 + 多线程**（`GUNICORN_WORKERS=1`）。需要更高并发请调大
`GUNICORN_THREADS`；若确实要多进程，请在反向代理层做限流。

### Systemd
```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin legado-tts
sudo mkdir -p /opt/legado-tts
sudo cp app.py gunicorn.conf.py requirements.txt /opt/legado-tts/
sudo cp -r templates static /opt/legado-tts/
sudo python3 -m venv /opt/legado-tts/.venv
sudo /opt/legado-tts/.venv/bin/pip install -r /opt/legado-tts/requirements.txt
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl enable --now legado-tts
```

该 unit 以非 root 用户运行，配置与统计写入 `/var/lib/legado-tts/`
（由 systemd 的 `StateDirectory` 自动创建）。若系统未提供 `venv` 模块，
请先安装发行版对应的 `python3-venv` 软件包。

### 测试
```bash
pip install -r requirements-dev.txt
pytest -q
```

---

## 火山引擎配置

火山引擎已切换到官方 v3 HTTP Chunked 单向流式语音合成接口，只使用一个 API Key：

1. 在火山引擎控制台的“API Key 管理”创建 Key。
2. 打开本服务 Web 管理页，选择“火山引擎”。
3. 填写 API Key。服务固定使用公共 2.0 音色资源 `seed-tts-2.0`。

服务访问火山引擎时仅发送以下鉴权与模型选择请求头：

- `X-Api-Key: <你的 API Key>`
- `X-Api-Resource-Id: seed-tts-2.0`
- `X-Api-Request-Id: <每次请求生成的 UUID>`

配置文件中的对应字段为：

```json
{
  "doubao_api_key": "your-api-key"
}
```

旧的 `appid`、`access_token`、`cluster` 鉴权已完全移除，也不再发送旧的 `Authorization: Bearer; ...` 请求头。服务读取旧配置时会删除这些字段，不会把旧 Token 当作新 API Key。升级后需要重新填写 API Key。旧版 `mars/moon` 音色 ID 会自动映射到对应的 `uranus` 2.0 音色。

注意：`doubao_api_key` 是服务访问火山引擎的出站凭证；环境变量 `API_KEYS` 是限制客户端访问本服务的入站凭证，两者用途不同。

官方参考：

- [单向流式语音合成 HTTP（单 API Key）](https://docs.volcengine.com/docs/6561/2528925?lang=zh)
- [HTTP Chunked 响应格式与结束帧](https://www.volcengine.com/docs/6561/1598757?lang=zh)
- [豆包语音合成模型 2.0 音色列表](https://www.volcengine.com/docs/6561/1257544?lang=zh)

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `PORT` | `8080` | 服务端口（Docker 镜像以非 root 运行，无法绑定 <1024） |
| `CONFIG_FILE` | `/opt/doubao-tts/config.json` | 配置文件路径 |
| `STATS_FILE` | `/opt/doubao-tts/stats.json` | 统计文件路径 |
| `MAX_TEXT_LENGTH` | `5000` | 单次合成最大文本长度 |
| `CHUNK_SIZE` | `500` | 长文本分块大小(字符) |
| `AUDIO_CACHE_SIZE` | `100` | 缓存最大条目数 |
| `AUDIO_CACHE_MAX_MB` | `200` | 缓存最大内存(MB) |
| `RATE_LIMIT_RPM` | `120` | 每IP每分钟请求限制(0=不限) |
| `RATE_LIMIT_WHITELIST` | `127.0.0.1,::1` | 限流白名单IP |
| `ADMIN_TOKEN` | `""` | 管理 API 认证 Token；非本机部署必须设置强随机值 |
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
| `BATCH_MAX_TEXTS` | `20` | `/api/speech/batch` 单次最大文本条数 |
| `EDGE_VOICES_TTL` | `3600` | Edge 完整语音列表缓存时长(秒) |
| `FFMPEG_TIMEOUT` | `30` | 单次 ffmpeg 转码超时(秒) |
| `FALLBACK_VOICE` | `zh-CN-XiaoxiaoNeural` | 故障转移时使用的音色 |
| `GUNICORN_THREADS` | `min(CPU×4, 32)` | gunicorn 线程数(并发调节首选) |
| `GUNICORN_WORKERS` | `1` | gunicorn 进程数，>1 会使限流/配额/缓存按进程各自独立 |

> 安全提示：未设置 `ADMIN_TOKEN` 时，同源 WebUI 与不携带 `Origin` 的直接客户端
> 会继续采用兼容开放模式。只要服务会被局域网或公网访问，就必须设置强随机 Token；
> 跨站浏览器管理请求会被拒绝，但这不能替代身份认证。
| `SSE_MAX_SUBSCRIBERS` | `20` | `/api/events` 最大并发订阅数 |

---

## API 端点

### TTS 合成
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/speech/stream` | Legado TTS；火山引擎端到端流式输出 |
| POST | `/speech/stream/chunked` | 兼容入口；火山引擎和 Edge 流式输出 |
| POST | `/v1/audio/speech` | OpenAI 兼容 TTS |
| POST | `/api/speech/batch` | 批量合成（最多 `BATCH_MAX_TEXTS` 条，默认20） |
| POST | `/api/tts/preview` | 试听短句（返回音频，计入限流与配额） |

### 音色管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/voices?provider=xxx` | 按Provider查询音色 |
| GET | `/api/voices/all` | 所有音色 |
| GET | `/api/voices/edge/live` | Edge TTS 微软完整语音列表(322，缓存 `EDGE_VOICES_TTL` 秒) |
| GET | `/api/voices/search` | 按名称/语言/性别搜索音色 |
| GET/POST/DELETE | `/api/favorites` | 音色收藏夹 🔒 |

> 🔒 = 设置 `ADMIN_TOKEN` 后，该行所列方法需要管理令牌
> （`Authorization: Bearer <token>`）。未设置 `ADMIN_TOKEN` 时不做鉴权。
> 注意 `/api/stats` 的 `GET` 是公开的，只有 `DELETE`（重置）受保护。

### 配置管理
| 方法 | 路径 | 说明 |
|------|------|------|
| GET/POST | `/api/config` | 获取/修改配置 🔒 |
| POST | `/api/config/test` | 测试当前配置 🔒 |
| GET | `/api/config/export` | 导出配置JSON 🔒 |
| POST | `/api/config/import` | 导入配置JSON 🔒 |
| GET/POST/DELETE | `/api/pronunciation` | 发音词典CRUD 🔒 |

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
| DELETE | `/api/stats` | 重置统计 🔒 |
| GET | `/api/cache/stats` | 缓存状态 |
| DELETE | `/api/cache/clear` | 清除缓存 🔒 |
| GET | `/api/audit` | 审计日志 🔒 |
| GET | `/api/events` | SSE 实时事件流 🔒 |
| GET | `/api/openapi.json` | OpenAPI 3.0 规范 |

`/api/events` 与 `/api/audit` 会暴露客户端 IP、音色与字符数，因此同样受
`ADMIN_TOKEN` 保护。浏览器的 `EventSource` 无法自定义请求头，故该端点
额外支持查询参数传令牌：

```js
new EventSource('/api/events?token=' + encodeURIComponent(token))
```

并发订阅数受 `SSE_MAX_SUBSCRIBERS`（默认 20）限制；超出时返回 `503` 并带
`Retry-After: 30`（每个订阅会占用一个工作线程）。

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

### 响应头

合成端点返回以下头部，便于客户端确认实际生效的参数：

| 头部 | 说明 |
|------|------|
| `X-TTS-Provider` | 实际合成的 Provider |
| `X-TTS-Voice` | 实际使用的音色 |
| `X-TTS-Chars` | 计费字符数 |
| `X-TTS-Format` | 实际输出格式（转码失败时回退为 `mp3`） |
| `X-TTS-Fallback` | 发生故障转移时为 `true` |
| `X-TTS-Requested-Provider` | 故障转移时，原本请求的 Provider |
| `X-TTS-Requested-Voice` | 故障转移时，原本请求的音色 |
| `X-RateLimit-Limit` | 每分钟请求上限 |
| `X-RateLimit-Remaining` | 当前窗口剩余请求数 |
| `X-RateLimit-Reset` | 限流窗口重置时间(Unix 时间戳) |

> 故障转移时 `X-TTS-Provider` / `X-TTS-Voice` 报告**实际**合成方，
> `X-TTS-Requested-*` 保留原始请求值。

---

## 流式部署说明

- `/speech/stream` 对火山引擎执行真实的端到端流式传输：上游返回按行分隔的 JSON 事件，服务逐块 Base64 解码并以 `audio/mpeg` 立即转发给 Legado，不等待整段音频完成。
- 流式响应不设置 `Content-Length`，并发送 `X-Accel-Buffering: no`。
- 如前置 Nginx，请关闭该路径的响应缓冲和缓存，并把读取超时设置得足够覆盖长段落：

```nginx
location /speech/stream {
    proxy_pass http://legado_tts;
    proxy_http_version 1.1;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 300s;
}
```

若客户端使用兼容入口 `/speech/stream/chunked`，请对该路径应用相同配置；外层 CDN 或网关也必须关闭响应缓冲，否则会重新变成整段音频完成后才开始播放。

OpenAI 兼容接口和批量接口仍会收集完整音频，以支持格式转换和 Base64 批量返回。

---

## 安全设计

- 所有错误响应返回标准化 JSON（含 request_id）
- 全局 404/405/500 错误处理
- Voice 参数自动清理 CRLF/null 字节（防 Header 注入）
- 已认证请求自动绕过限流
- CORS 开放（TTS 服务需要被任意客户端访问）

## License

MIT License
