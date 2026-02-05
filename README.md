# Legado TTS Server

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
</p>

开源阅读(Legado)语音合成服务，支持 **Edge**（免费）、**火山引擎**、**腾讯云** 三个服务商，带 WebUI 管理界面。

## 功能特性

- **三服务商支持** - Edge（免费）、火山引擎、腾讯云
- **WebUI 管理界面** - 可视化配置，无需编辑文件
- **分服务商统计** - 独立统计各服务商用量
- **多音色支持** - 内置丰富音色可选
- **开源阅读适配** - 自动生成配置，一键复制

## 快速开始

### 安装依赖

```bash
apt install python3 python3-flask python3-pip
pip3 install requests edge-tts
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

访问 `http://your-server-ip`，选择服务商并填入对应的 API 密钥（Edge 免费无需配置）。

## 获取 API 密钥

### Edge（免费）
无需配置，开箱即用。

### 火山引擎
1. 访问 [火山引擎控制台](https://console.volcengine.com/speech/service/8)
2. 开通语音合成服务，创建应用获取 App ID 和 Access Token

### 腾讯云
1. 访问 [腾讯云控制台](https://console.cloud.tencent.com/cam/capi)
2. 获取 SecretId 和 SecretKey

## 可用音色

### Edge（20个中文音色）

| 音色ID | 名称 |
|--------|------|
| zh-CN-XiaoxiaoNeural | 晓晓 - 女声 |
| zh-CN-YunxiNeural | 云希 - 男声 |
| zh-CN-YunjianNeural | 云健 - 男声 |
| zh-CN-XiaoyiNeural | 晓伊 - 女声 |
| zh-CN-YunyangNeural | 云扬 - 新闻 |
| zh-CN-XiaochenNeural | 晓辰 - 女声 |
| zh-CN-XiaohanNeural | 晓涵 - 女声 |
| zh-CN-XiaomengNeural | 晓梦 - 女声 |
| zh-CN-XiaomoNeural | 晓墨 - 女声 |
| zh-CN-XiaoqiuNeural | 晓秋 - 女声 |
| zh-CN-XiaoruiNeural | 晓睿 - 女声 |
| zh-CN-XiaoshuangNeural | 晓双 - 童声 |
| zh-CN-XiaoxuanNeural | 晓萱 - 女声 |
| zh-CN-XiaoyanNeural | 晓颜 - 女声 |
| zh-CN-XiaoyouNeural | 晓悠 - 童声 |
| zh-CN-YunfengNeural | 云枫 - 男声 |
| zh-CN-YunhaoNeural | 云皓 - 男声 |
| zh-CN-YunxiaNeural | 云夏 - 男声 |
| zh-CN-YunyeNeural | 云野 - 男声 |
| zh-CN-YunzeNeural | 云泽 - 男声 |

### 火山引擎（8个音色）

| 音色ID | 名称 |
|--------|------|
| zh_female_cancan_mars_bigtts | 知性灿灿 |
| zh_female_shuangkuaisisi_moon_bigtts | 爽快思思 |
| zh_female_tiexinnvsheng_mars_bigtts | 贴心女生 |
| zh_female_jitangmeimei_mars_bigtts | 鸡汤妹妹 |
| zh_female_mengyatou_mars_bigtts | 萌丫头 |
| zh_male_shaonianzixin_moon_bigtts | 少年梓辛 |
| zh_male_wennuanahu_moon_bigtts | 温暖阿虎 |
| zh_male_jieshuonansheng_mars_bigtts | 磁性解说 |

### 腾讯云（7个音色）

| 音色ID | 名称 |
|--------|------|
| 501002 | 智菊 - 阅读女声 |
| 501000 | 智斌 - 阅读男声 |
| 501001 | 智兰 - 资讯女声 |
| 501003 | 智宇 - 阅读男声 |
| 601009 | 爱小芊 - 多情感女声 |
| 601008 | 爱小豪 - 多情感男声 |
| 601010 | 爱小娇 - 多情感女声 |

## License

[MIT License](LICENSE)
