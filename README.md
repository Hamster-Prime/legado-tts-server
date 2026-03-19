# 📖 Legado TTS Server

<div align="center">

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.8+-blue.svg)
![Status](https://img.shields.io/badge/status-active-success.svg)

**为开源阅读 (Legado) 量身打造的聚合语音合成服务**

[特性](#-特性) • [安装](#-安装) • [部署](#-部署) • [使用指南](#-使用指南) • [API 文档](#-api-文档)

</div>

---

## ✨ 特性

Legado TTS Server 是一个轻量级的语音合成中间件，旨在为阅读类APP提供高质量、多源的TTS服务。

- **🎯 多源聚合**：同时支持 **Edge TTS** (免费高质量)、**火山引擎** (Doubao)、**腾讯云** TTS、**小米MiMo** TTS。
- **🧠 智能路由**：单一接口，后端根据音色参数自动分发请求，无需在APP端切换引擎。
- **💻 可视化管理**：提供优雅的 Web 管理界面，支持配置、测试、状态监控。
- **🔌 一键集成**：支持一键复制配置到开源阅读，无缝对接。
- **📊 流量统计**：实时监控各服务商的调用量和字符数。
- **🔒 安全隐私**：敏感密钥自动掩码显示，防止泄露。

## 🎧 支持音色

### 1. Edge TTS (免费 & 推荐)
微软提供的免费高质量神经与网络语音，无需配置 Key 即可使用。
- **20+ 种中文音色**：包括晓晓、云希、云健、晓伊等热门音色。
- **风格多样**：支持新闻、客服、助理、聊天等多种说话风格。

### 2. 火山引擎 (字节跳动)
提供极其自然的拟人化语音。
- **8 种精品音色**：灿灿、思思、贴心女生、鸡汤妹妹等。
- **特点**：情感丰富，适合小说朗读。

### 3. 腾讯云 TTS
- **7 种基础音色**：智菊、智斌等。
- **特点**：稳定，支持长文本。

### 4. 小米MiMo TTS
小米自研的新一代语音合成大模型，支持风格控制、方言和歌声合成。
- **3 种官方音色**：MiMo默认语音、中文女声、英文女声。
- **特点**：自然度高，支持情绪控制、多种方言、角色扮演和歌声合成，当前限时免费。

---

## 🛠 安装

### 环境要求
- Python 3.8+
- Linux / Windows / macOS

### 1. 克隆仓库
```bash
git clone https://github.com/Hamster-Prime/legado-tts-server.git
cd legado-tts-server
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
# 或者直接安装
pip install flask requests edge-tts openai
```

---

## 🚀 部署

### 方法一：直接运行 (开发/测试)
```bash
python3 app.py
```
服务将在 `http://0.0.0.0:80` 启动。

### 方法二：Systemd 托管 (推荐 Linux 生产环境)

1. 编辑 `legado-tts.service` 文件，根据实际路径修改 `WorkingDirectory` 和 `ExecStart`。
2. 复制服务文件并启动：
```bash
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now legado-tts
```

### 方法三：Docker (计划中)
*Docker 镜像构建支持即将推出...*

---

## 📖 使用指南

### 1. 访问管理界面
在浏览器打开 `http://你的服务器IP`。

### 2. 配置服务商
- **Edge TTS**：开箱即用，无需配置。
- **火山/腾讯云**：在 "API 设置" 卡片中填入对应的 `AppID` / `SecretID` 和 `Token` / `Key`。
- **小米MiMo**：在 "API 设置" 卡片中填入从 [小米开放平台](https://platform.xiaomimimo.com/) 获取的 `API Key`。

### 3. 导入到开源阅读
1. 在网页上的 **"开源阅读配置"** 区域，选择你喜欢的默认音色。
2. 点击 **"复制配置"** 按钮。
3. 打开开源阅读 APP -> **朗读引擎** -> **网络导入** -> 粘贴配置。

---

## 🔌 API 文档

### 语音合成接口
`POST /speech/stream`

后端会自动根据 `voice` 参数判断使用哪个服务商。

**请求参数 (JSON):**

| 字段 | 类型 | 说明 | 示例 |
| :--- | :--- | :--- | :--- |
| `text` | string | **必填**，需要朗读的文本 | "你好，世界" |
| `voice` | string | **必填**，音色ID | "zh-CN-XiaoxiaoNeural" |
| `rate` | string | 语速 (可选) | "+0%" |

**路由规则:**
- `voice` 包含 "Neural" -> **Edge TTS**
- `voice` 为纯数字 -> **腾讯云**
- `voice` 以 "zh_" 开头 -> **火山引擎**
- `voice` 为 `mimo_default` / `default_zh` / `default_eh` 或以 "mimo_" 开头 -> **小米MiMo TTS**

---

## 📄 License

MIT License © 2024 Hamster-Prime
