# 移动端接口核对

以本次检出的 [app.py](../app.py) 及 [服务端 README](../README.md) 为准。Android 客户端不调用管理 API，不改服务端配置。

| 操作 | 实际契约 | 客户端行为 |
| --- | --- | --- |
| 验证连接及访问密钥 | `_check_api_key` 在检查文本前执行；仅当 `API_KEYS_REQUIRED=1` 才强制鉴权，单独设置 `API_KEYS` 不会开启认证。合成接口支持 Bearer。 | `POST /speech/stream/chunked` 发送空 JSON，只有服务特有的 400 JSON（`invalid_request_error` / `Missing text`）才表示探测通过。401/403 另行提示。不会合成语音或消耗字符配额；探测本身仍占请求限额。服务未开启认证时，无法区分“有效密钥”与“密钥被忽略”。 |
| 获取目录 | `GET /api/voices/all` 返回按服务商分组的 JSON 数组（字段 `id`、`name`）。服务端当前输出 Edge、豆包、腾讯、小米、Fish Audio 五组。 | 遍历实际响应，不内置音色表。该接口对普通目录是公开的，不能代替鉴权检查。无权限查看的服务端私有自定义音色不会出现在列表，不能用客户端补齐。目录存在不代表上游凭证可用。 |
| 试听 | `POST /api/tts/preview`，JSON 含 `provider`、`voice`；响应的 `audio` 为 Base64 MP3。 | 不发送阅读输入框内容。试听固定短句，消耗服务配额。 |
| 实际朗读 | `POST /speech/stream/chunked`，JSON 含 `provider`、`voice`、`text`、`rate`（例如 `+25%`）；响应为 `audio/mpeg`。 | 增量读取 MP3，不要求 Content-Length。从 `GET /api/info` 的 `config.max_text_length` 读取实例上限（默认 5000 个 Unicode 码点）。客户端首段较短、后续按标点分段；读当前段时可预取后文。 |

## 约束及不适配点

- Edge、火山引擎、Fish Audio 支持单请求内逐块返回 MP3；腾讯和小米在各段合成完成后返回。客户端在当前段首块到达后即可启动下一段，最多两个在途请求；不能保证上游慢于播放速度时不断流。代理缓冲也影响首声，见服务端 README 的流式部署说明。
- 两个 `/speech/stream` 入口使用相同的合成参数，声读发送上表四项。语速由各服务商支持程度决定（如本服务的 Fish Audio 请求不发送速度参数）。系统引擎 PCM 回调不能直接传入 MP3；客户端用设备解码，不依赖服务端安装 FFmpeg。OpenAI 兼容接口即使请求 PCM，在 FFmpeg 缺失时也可能返回 MP3。
- 服务端允许回退到其他音色，以 `X-TTS-Fallback: true` 标明。客户端把这种响应作为“所选音色不可用”提示处理，不把替代音色当成所选音色朗读。已发送 HTTP 头后发生的上游错误可能表现为连接中断而非 5xx。
- 这里的实时目录指服务端当前公开目录，不是为每次浏览重新查询五个云厂商。服务端的 `/api/voices/edge/live` 单独从 Microsoft 查询，闭源厂商无对等接口。`_can_view_custom_voices` 按管理鉴权控制私有音色；客户端不索取管理权限。
- App 只接受不含 userinfo、查询参数、片段的 HTTP(S) 根地址或反代子路径；启用密钥时只允许 HTTPS。密钥只放 Authorization 头，不进请求 URL；禁用自动重定向、代理和 HTTP 缓存。请求只发给用户指定实例；实例本身会向用户选择的上游发送文本。

## 首版实测（宿主 JVM，不是设备验收）

运行未改动的 Flask 服务（隔离配置文件），以实际 Kotlin `ClientApi` 连回环地址。探测通过，取到 58 项（Edge 36、豆包 11、Fish Audio 1、腾讯 7、小米 3），实例报告单段上限 5000。使用免费晓晓音色合成“朗读测试。”并读取到非空 `audio/mpeg` 响应。没有使用付费上游。另用本机测试服务验证拒绝重定向、401/429、无效 400、增量读取、试听 Base64 和 Unicode 分段。以上不代替真机解码、听感或系统设置/Binder 集成测试。

另以 Flask test client 启用 `API_KEYS_REQUIRED`，使用随机的临时访问密钥：无密钥及错密钥均返回 401，正确 Bearer 密钥携空 JSON 返回预期 400，匿名目录仍为 200。该项测试的是路由鉴权顺序，不是 Android HTTPS/TLS 端到端验收。

还以仅监听回环地址的独立 HTTPS 实例复测，使用一次性证书及随机密钥，仅本次宿主 JVM 信任测试证书，不修改 App 的证书信任逻辑：同一 `ClientApi` 匿名和使用错误密钥都收到 401；正确密钥通过探测并获取 58 个音色。

再用相同实例、同一音色、两段测试文本串行请求，以实际 `Mp3Frames` 类解析响应：共 558 个完整帧，首个完整帧在请求开始后约 1.03 秒收到（一次样本，不含设备解码、音频焦点及扬声器延迟）。不代表所有网络或服务商表现。
