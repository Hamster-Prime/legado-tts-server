# Legado TTS Server

一个为开源阅读 (Legado) 设计的语音合成服务，集成了多家TTS服务商，提供统一、易用的API和Web管理界面。

## ✨ 特性

-   **多服务商支持**: 同时支持 **Edge TTS** (免费)、**火山引擎** (Doubao)、和**腾讯云**。
-   **智能路由**: 无需修改阅读APP配置，后端根据所选音色自动路由到对应的服务商。
-   **统一接口**: 简化的API端点，兼容开源阅读的TTS引擎格式。
-   **Web管理界面**:
    -   可视化配置和测试。
    -   统一的音色选择器，动态加载不同服务商的音色列表。
    -   一键复制开源阅读配置。
    -   按服务商展示使用统计（总字符、总请求、今日字符、今日请求）。
-   **即时部署**: 提供`systemd`服务文件，方便快速部署。

## 🎤 支持的音色

| 服务商   | 音色数量 | 详情                                                                                                                                                                       |
| :------- | :------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Edge TTS** | 20       | 晓晓, 云希, 云健, 晓伊, 云扬, 晓辰, 晓涵, 晓梦, 晓墨, 晓秋, 晓睿, 晓双, 晓萱, 晓颜, 晓悠, 云枫, 云皓, 云夏, 云野, 云泽                                                         |
| **火山引擎** | 8        | 知性灿灿, 爽快思思, 贴心女生, 鸡汤妹妹, 萌丫头, 少年梓辛, 温暖阿虎, 磁性解说                                                                                             |
| **腾讯云**   | 7        | 智菊, 智斌, 智兰, 智宇, 爱小芊, 爱小豪, 爱小娇                                                                                                                             |

## 🚀 快速开始

### 1. 安装依赖

```bash
# 确保Python 3.8+ 和 pip 已安装
pip install flask requests edge-tts pydub

# 安装ffmpeg (用于音频格式转换)
sudo apt update && sudo apt install ffmpeg -y
```

### 2. 部署

```bash
# 克隆仓库
git clone https://github.com/Hamster-Prime/legado-tts-server.git
cd legado-tts-server

# 运行 (用于测试)
python3 app.py

# 或使用 systemd 持久化运行 (推荐)
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now legado-tts
```

### 3. 配置
服务启动后，访问 `http://<你的服务器IP>` 即可打开Web管理界面，在其中配置火山引擎和腾讯云的API密钥。Edge TTS无需配置。

## ⚙️ API & 路由

服务通过统一的 `POST /speech/stream` 接口接收请求。

**路由规则**:
服务器会根据请求体中的 `voice` 参数自动判断使用哪个服务商：
-   `voice` 值包含 `Neural`: **Edge TTS**
-   `voice` 值为纯数字: **腾讯云**
-   `voice` 值以 `zh_` 开头: **火山引擎**

## 📖 开源阅读配置

在Web界面的“开源阅读配置”卡片中，选择您喜欢的音色，然后点击“复制”按钮，将生成的配置粘贴到开源阅读的朗读引擎设置中即可。

## License

[MIT](LICENSE)
