# Android 依赖许可证清单

核对日期：2026-11-25。以下清单由 `./gradlew :app:dependencies --configuration debugRuntimeClasspath --offline` 与该配置解析后的 AAR/JAR 生成。完整的坐标、版本及每项许可证见随包的 [RUNTIME_COMPONENTS.tsv](../android/app/src/main/assets/licenses/RUNTIME_COMPONENTS.tsv)。BOM、版本约束及无代码的重定向元数据不另算为制品。

## 结论

- **未发现 GPL、LGPL、AGPL 或其他 copyleft 运行时依赖。** APK 运行时依赖均为 Apache-2.0 系列或 MIT。
- 显式声明的运行库为 `miuix-ui-android`、`activity-compose`、`lifecycle-viewmodel-ktx`、`startup-runtime` 与 `kotlinx-coroutines-android`，均为 Apache-2.0。Miuix 自己的 core、shader、squircle 模块同为 Apache-2.0。
- Miuix **传递引入**的 MaterialKolor 是 MIT；其许可证原文随 APK 以 `assets/licenses/MaterialKolor-MIT.txt` 提供。
- AndroidX、Kotlin、Kotlin Coroutines、Kotlin Serialization、JetBrains Annotations、JSpecify、Poko annotations 以及 Guava `listenablefuture:1.0` 均为 Apache-2.0（部分 POM 文案为 “The Apache Software License, Version 2.0”）。
- 运行时 AAR/JAR 未发现额外字体、OFL 字体、图片版权包或 copyleft 文本；Miuix AAR 使用图标代码，不随包携带字体文件。无统计、崩溃上报或第三方网络 SDK。

## 运行时坐标（按许可证归类）

### Apache License 2.0

- `androidx.activity`: `activity-compose`, `activity-ktx`, `activity`
- `androidx.annotation`: `annotation-experimental`, `annotation-jvm`
- `androidx.arch.core`: `core-common`, `core-runtime`
- `androidx.autofill:autofill`
- `androidx.collection`: `collection-jvm`, `collection-ktx`
- `androidx.compose.animation`: `animation-android`, `animation-core-android`
- `androidx.compose.foundation`: `foundation-android`, `foundation-layout-android`
- `androidx.compose.material3:material3-window-size-class-android`
- `androidx.compose.runtime`: `runtime-android`, `runtime-annotation-android`, `runtime-retain-android`, `runtime-saveable-android`
- `androidx.compose.ui`: `ui-android`, `ui-geometry-android`, `ui-graphics-android`, `ui-text-android`, `ui-unit-android`, `ui-util-android`
- `androidx.concurrent:concurrent-futures`, `androidx.core`: `core`, `core-ktx`, `core-viewtree`
- `androidx.customview:customview-poolingcontainer`, `androidx.documentfile:documentfile`, `androidx.dynamicanimation:dynamicanimation`, `androidx.emoji2:emoji2`, `androidx.graphics:graphics-path`, `androidx.interpolator:interpolator`, `androidx.legacy:legacy-support-core-utils`, `androidx.loader:loader`, `androidx.localbroadcastmanager:localbroadcastmanager`
- `androidx.lifecycle`: `lifecycle-common-jvm`, `lifecycle-livedata-core-ktx`, `lifecycle-livedata-core`, `lifecycle-livedata`, `lifecycle-process`, `lifecycle-runtime-android`, `lifecycle-runtime-compose-android`, `lifecycle-runtime-ktx-android`, `lifecycle-viewmodel-android`, `lifecycle-viewmodel-ktx`, `lifecycle-viewmodel-savedstate-android`, `lifecycle-viewmodel`
- `androidx.navigationevent`: `navigationevent-android`, `navigationevent-compose-android`; `androidx.print:print`; `androidx.profileinstaller:profileinstaller`
- `androidx.savedstate`: `savedstate-android`, `savedstate-compose-android`, `savedstate-ktx`; `androidx.startup:startup-runtime`; `androidx.tracing:tracing`; `androidx.transition:transition`; `androidx.versionedparcelable:versionedparcelable`; `androidx.window`: `window-core-android`, `window`
- `org.jetbrains.kotlin:kotlin-stdlib:2.4.0`
- `org.jetbrains.kotlinx`: `kotlinx-coroutines-android:1.10.2`, `kotlinx-coroutines-core-jvm:1.10.2`, `kotlinx-serialization-core-jvm:1.7.3`
- `org.jetbrains:annotations:23.0.0`; `org.jspecify:jspecify:1.0.0`; `dev.drewhamilton.poko:poko-annotations-jvm:0.21.1`
- `com.google.guava:listenablefuture:1.0`（POM 未重复声明许可证；Guava 项目 Apache-2.0，且该 artifact 仅含 ListenableFuture 类）
- `top.yukonga.miuix.kmp`: `miuix-core-android`, `miuix-shader-android`, `miuix-squircle-android`, `miuix-ui-android`（0.9.3）

### MIT

- `com.materialkolor:material-color-utilities-android:4.1.1` — MaterialKolor 项目 MIT License，版权归 Jordon de Hoog；原文：`android/app/src/main/assets/licenses/MaterialKolor-MIT.txt`。

## 随 APK 提供的文本

`android/app/src/main/assets/licenses/Apache-2.0.txt` 是 Apache License 2.0 原文；`THIRD_PARTY_NOTICES.txt` 给出组件归类和坐标索引。MaterialKolor 原文单独提供，避免仅用短摘要替代 MIT 条款。

项目源码许可证见仓库根 [LICENSE](../LICENSE)。Gradle Wrapper、AGP、JDK、Android SDK 和 Kotlin 编译器属于构建工具，不随 APK 分发。测试代码的 JUnit 4.13.2 是 **EPL-1.0（弱 copyleft）**，只在本地单元测试配置（`testImplementation`），不在 App 运行时制品、测试运行器或安装包内。测试传递依赖 Hamcrest Core 1.3 是 BSD-3-Clause。Android 系统提供的音频解码器不随包复制；客户端未嵌入 FFmpeg。

版本升级、增加依赖或启用新的构建 variant 后应重新导出 `debugRuntimeClasspath`/`releaseRuntimeClasspath`，检查是否出现未声明许可证、字体或 copyleft 组件。
