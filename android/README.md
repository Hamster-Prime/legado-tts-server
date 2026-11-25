# 声读 · Legado TTS

Android 9（API 28）及以上。直接使用自己的 Legado TTS Server，无需安装开源阅读。设置与收藏只留在本机。

## 安装和连接

1. 安装构建产物 `app-debug.apk`。在“连接你的服务端”填**服务根地址**（可含反向代理路径），不要填 `/v1`、`/speech` 或带密钥的分享链接。真机的 `localhost` 指手机自己，不是服务器。
2. 需要认证时填写服务的访问密钥（不是管理令牌或云厂商密钥）。点“验证并保存”。有密钥必须用系统信任证书的 HTTPS；无密钥可用可信局域网 HTTP，但文本会明文传输。
3. 在“音色”展开分组。播放图标试听服务器短句，心形图标收藏，点整行选为 App 与系统引擎的默认音色。要查看服务端新公开的音色，点“刷新”。
4. 在“朗读”输入或粘贴文本，点“开始朗读”。可在播放时调速、暂停、继续、停止。切到后台后可通过系统媒体控件操作。在“设置”打开系统文字转语音设置，将 **声读 · Legado TTS** 设为首选引擎；支持自行选择朗读引擎的阅读 App 也可单独选择它。

系统引擎提供的是联网音色。其他 App 调用此引擎时，其请求文本会发送至当前服务器，可能消耗上游配额。引擎只回传 PCM，不把访问密钥给调用方。系统和上游的限流、断网或超时均会影响朗读。

## 从源码构建

从仓库根目录进入 `android/`，使用 Android Studio 打开，或使用 JDK 21、Android SDK Platform 37.0 与 Build Tools 构建（首次构建需从官方 Maven 仓库下载依赖）。设置 `ANDROID_HOME` 或在不入库的 `local.properties` 中填写 `sdk.dir`。

```sh
cd android
./gradlew :app:assembleDebug :app:testDebugUnitTest :app:lintDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

以上为 debug 签名的验收包。面向其他用户分发时，在 Android Studio 的 **Generate Signed App Bundle or APK** 中选择 release，用自己的长期发布密钥签名；不要提交签名文件。

## 设备自动检查

使用一部测试手机。以下命令检查网络契约、码点分段、Keystore、设备 MP3 解码和 Android 标准 TTS 绑定；Keystore 测试使用独立的测试命名空间。

```sh
./gradlew :app:assembleDebugAndroidTest
adb install -r -t app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w io.github.legadotts.client.test/io.github.legadotts.client.ContractRunner
```

先在 App 内完成配置，选择免费的 Edge 音色，再运行：

```sh
adb shell am instrument -w -e configured true -e synthesize true -e systemSynthesis true \
  io.github.legadotts.client.test/io.github.legadotts.client.ContractRunner
```

该命令还会探测真实实例、拉取目录、读取 Edge 音频，并调用系统 `synthesizeToFile` 检查是否生成有效 WAV。成功时最后一行是 `OK (contract checks)`；失败时会报告步骤。不要把密钥放入 adb 命令、截图或工单。Binder 文件测试不能代替听感验收。

## 真机验收要点

| 场景 | 操作与预期 |
| --- | --- |
| 首次配置 | 正确地址 + 错误密钥应提示认证失败且不保存。用正确密钥重试；按返回目录分组，有新音色无需更新 APK。 |
| 长文本和控件 | 粘贴超过服务器单次上限的多段文章。记下点播放到首声的时间，听过至少两个分段边界；慢网、断网、限流时应提示或停止，不能无限等待。暂停后静音、继续不从头重播，变速或停止及时生效。 |
| 后台与打断 | 播放时回桌面并锁屏，在系统媒体控件上暂停/继续。来电应暂停；短暂失焦结束可继续，用户手动暂停不应自动恢复。拔出耳机应暂停。 |
| 系统应用调用 | 在系统设置与至少一个阅读 App 中选择该引擎，听中文示例；在调用方停止后，切换本 App 音色，再朗读新文本。 |
| 界面与本地状态 | 切换系统深浅色、小屏、横屏、大字体，展开键盘，检查输入和导航；旋转不丢稿。重新打开后地址、收藏与音色应恢复。换服务器不会沿用旧密钥或收藏。 |

[本次交付与验证范围](../docs/ANDROID_DELIVERY.md) · [接口核对](../docs/API_AUDIT.md) · [依赖许可证](../docs/ANDROID_LICENSES.md)
