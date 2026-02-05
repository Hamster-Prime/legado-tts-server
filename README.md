# Legado TTS Server

开源阅读 (Legado) 语音合成服务，支持多个TTS服务商。

## 特性

- **多服务商支持**：Edge TTS (免费)、火山引擎、腾讯云
- **智能路由**：根据音色参数自动选择服务商
- **统一接口**：一个API端点，兼容所有服务商
- **Web管理界面**：可视化配置和测试
- **使用统计**：按服务商统计字符数和请求数

## 支持的音色

### Edge TTS (免费，20种)
晓晓、云希、云健、晓伊、云扬、晓辰、晓涵、晓梦、晓墨、晓秋、晓睿、晓双、晓萱、晓颜、晓悠、云枫、云皓、云夏、云野、云泽

### 火山引擎 (8种)
知性灿灿、爽快思思、贴心女生、鸡汤妹妹、萌丫头、少年梓辛、温暖阿虎、磁性解说

### 腾讯云 (7种)
智菊、智斌、智兰、智宇、爱小芊、爱小豪、爱小娇

## 安装

```bash
# 安装依赖
pip install flask requests edge-tts pydub

# 安装ffmpeg (pydub依赖)
apt install ffmpeg
```

## 部署

```bash
# 复制服务文件
cp legado-tts.service /etc/systemd/system/

# 启动服务
systemctl daemon-reload
systemctl enable legado-tts
systemctl start legado-tts
```

## API

### POST /speech/stream

语音合成接口，根据voice参数自动路由到对应服务商。

**请求体：**
```json
{
  "text": "要合成的文本",
  "voice": "zh-CN-XiaoxiaoNeural",
  "rate": "0%"
}
```

**路由规则：**
- 包含 `Neural` → Edge TTS
- 纯数字 → 腾讯云
- 以 `zh_` 开头 → 火山引擎

## 开源阅读配置

在Web界面选择音色后，复制生成的配置到开源阅读的朗读引擎设置即可。

## License

MIT
