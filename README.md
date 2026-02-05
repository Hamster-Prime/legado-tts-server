# Legado TTS Server

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
</p>

开源阅读(Legado)语音合成服务，支持**火山引擎**和**腾讯云**双服务商，带WebUI管理界面。

## 功能特性

- **双服务商支持** - 火山引擎、腾讯云一键切换
- **WebUI管理界面** - 可视化配置，无需编辑文件
- **分服务商统计** - 独立统计各服务商用量
- **多音色支持** - 内置多种音色可选
- **开源阅读适配** - 自动生成配置，一键复制

## 快速开始

### 安装依赖

```bash
apt install python3 python3-flask python3-pip
pip3 install requests
```

### 部署服务

```bash
git clone https://github.com/Hamster-Prime/legado-tts-server.git
cd legado-tts-server

# 直接运行
python3 app.py

# 或使用 systemd
sudo cp legado-tts.service /etc/systemd/system/
sudo systemctl enable legado-tts
sudo systemctl start legado-tts
```

### 配置

访问 `http://your-server-ip`，选择服务商并填入对应的API密钥。

## 获取API密钥

### 火山引擎
1. 访问 [火山引擎控制台](https://console.volcengine.com/speech/service/8)
2. 开通语音合成服务，创建应用获取 App ID 和 Access Token

### 腾讯云
1. 访问 [腾讯云控制台](https://console.cloud.tencent.com/cam/capi)
2. 获取 SecretId 和 SecretKey

## 可用音色

### 火山引擎
| 音色ID | 名称 |
|--------|------|
| zh_female_cancan_mars_bigtts | 知性灿灿 |
| zh_female_shuangkuaisisi_moon_bigtts | 爽快思思 |
| zh_male_wennuanahu_moon_bigtts | 温暖阿虎 |
| zh_male_jieshuonansheng_mars_bigtts | 磁性解说 |

### 腾讯云
| 音色ID | 名称 |
|--------|------|
| 501002 | 智菊 - 阅读女声 |
| 501000 | 智斌 - 阅读男声 |
| 601009 | 爱小芊 - 多情感女声 |
| 601010 | 爱小娇 - 多情感女声 |

## License

[MIT License](LICENSE)
