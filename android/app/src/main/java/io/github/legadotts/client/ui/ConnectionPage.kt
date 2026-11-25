package io.github.legadotts.client.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.widget.Toast
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.basic.*
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.basic.ArrowRight
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.utils.overScrollVertical

@Composable
internal fun ConnectionPage(
    configured: Boolean,
    connectionStatus: ConnectionStatus,
    connectionError: String,
    formError: String,
    address: String,
    hasKey: Boolean,
    connecting: Boolean,
    refreshing: Boolean,
    voiceCount: Int,
    providerCount: Int,
    activeVoice: VoiceItem?,
    speed: Float,
    actions: ReaderActions,
    openCatalog: () -> Unit,
) {
    // When already configured, default to showing the Connected status overview — never the setup form.
    var editing by rememberSaveable { mutableStateOf(false) }
    var awaitingSave by remember { mutableStateOf(false) }

    LaunchedEffect(connecting, formError, connectionStatus) {
        if (awaitingSave && !connecting) {
            awaitingSave = false
            if (formError.isEmpty() && connectionStatus == ConnectionStatus.CONNECTED) {
                editing = false
            }
        }
    }

    if (!configured) {
        OnboardingConnectionView(
            address = address,
            hasKey = hasKey,
            connecting = connecting,
            connectionError = connectionError,
            onSubmit = { addr, key -> actions.connect(addr, key) },
        )
    } else {
        ConfiguredSettingsView(
            connectionStatus = connectionStatus,
            connectionError = connectionError,
            formError = formError,
            address = address,
            hasKey = hasKey,
            connecting = connecting,
            refreshing = refreshing,
            voiceCount = voiceCount,
            providerCount = providerCount,
            activeVoice = activeVoice,
            speed = speed,
            actions = actions,
            editing = editing,
            onStartEdit = {
                actions.clearFormError()
                editing = true
            },
            onCancelEdit = {
                actions.clearFormError()
                editing = false
                awaitingSave = false
            },
            onSaveEdit = { addr, key ->
                awaitingSave = true
                actions.connect(addr, key)
            },
            openCatalog = openCatalog,
        )
    }
}

@Composable
private fun OnboardingConnectionView(
    address: String,
    hasKey: Boolean,
    connecting: Boolean,
    connectionError: String,
    onSubmit: (String, String?) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .overScrollVertical()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 22.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Spacer(Modifier.height(6.dp))
        Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            AppMark(size = 50)
            Text("连接你的服务端", style = MiuixTheme.textStyles.title1, fontWeight = FontWeight.Bold)
            Caption("输入手机可访问的 Legado TTS 服务地址，验证通过后即可浏览音色、朗读文章或设为系统语音引擎。")
        }

        ConnectionFormCard(
            savedAddress = address,
            savedHasKey = hasKey,
            connecting = connecting,
            submitLabel = if (connecting) "正在验证…" else "验证并连接",
            onCancel = null,
            onSubmit = onSubmit,
        )

        ErrorBanner(connectionError)
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun ConfiguredSettingsView(
    connectionStatus: ConnectionStatus,
    connectionError: String,
    formError: String,
    address: String,
    hasKey: Boolean,
    connecting: Boolean,
    refreshing: Boolean,
    voiceCount: Int,
    providerCount: Int,
    activeVoice: VoiceItem?,
    speed: Float,
    actions: ReaderActions,
    editing: Boolean,
    onStartEdit: () -> Unit,
    onCancelEdit: () -> Unit,
    onSaveEdit: (String, String?) -> Unit,
    openCatalog: () -> Unit,
) {
    val context = LocalContext.current
    val dark = isSystemInDarkTheme()
    val colors = MiuixTheme.colorScheme
    val scrollBehavior = MiuixScrollBehavior()
    val isHttps = address.startsWith("https://", ignoreCase = true)
    val busy = connecting || refreshing

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "设置",
            subtitle = when (connectionStatus) {
                ConnectionStatus.CONNECTED -> "服务已连接"
                ConnectionStatus.CONNECTING -> "正在同步服务端…"
                ConnectionStatus.ERROR -> "服务连接异常"
                ConnectionStatus.DISCONNECTED -> "本机设置"
            },
            scrollBehavior = scrollBehavior,
        )

        Column(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .overScrollVertical()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 14.dp, vertical = 6.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            SmallTitle(
                text = "服务连接",
                insideMargin = PaddingValues(start = 6.dp, top = 4.dp, bottom = 4.dp),
            )

            AnimatedContent(
                targetState = editing,
                transitionSpec = { AppMotion.panelSwap() },
                label = "connection-mode-swap",
            ) { isEditing ->
                if (!isEditing) {
                    Card(Modifier.fillMaxWidth(), insideMargin = PaddingValues(14.dp)) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            val green = connectedGreen(dark)
                            when (connectionStatus) {
                                ConnectionStatus.CONNECTED -> StatusPill(
                                    text = "已连接",
                                    containerColor = green.copy(alpha = 0.14f),
                                    contentColor = green,
                                    dotColor = green,
                                )
                                ConnectionStatus.CONNECTING -> StatusPill(
                                    text = "正在连接…",
                                    containerColor = colors.primary.copy(alpha = 0.14f),
                                    contentColor = colors.primary,
                                    loading = true,
                                )
                                ConnectionStatus.ERROR -> StatusPill(
                                    text = "连接异常",
                                    containerColor = colors.errorContainer,
                                    contentColor = colors.onErrorContainer,
                                    dotColor = colors.error,
                                )
                                ConnectionStatus.DISCONNECTED -> StatusPill(
                                    text = "已配置",
                                    containerColor = colors.secondaryContainer,
                                    contentColor = colors.onSurfaceVariantSummary,
                                    dotColor = colors.onSurfaceVariantSummary,
                                )
                            }

                            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                                MiniBadge(text = if (isHttps) "HTTPS" else "HTTP", accent = isHttps)
                                if (hasKey) MiniBadge(text = "已设密钥", accent = true)
                            }
                        }

                        Spacer(Modifier.height(12.dp))

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(34.dp)
                                    .clip(CircleShape)
                                    .background(colors.primary.copy(alpha = 0.12f)),
                                contentAlignment = Alignment.Center,
                            ) {
                                Icon(serverIcon, null, Modifier.size(17.dp), tint = colors.primary)
                            }
                            Spacer(Modifier.width(10.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    text = address,
                                    style = MiuixTheme.textStyles.body1,
                                    fontWeight = FontWeight.Medium,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Caption(
                                    buildString {
                                        append("$voiceCount 个可用音色")
                                        if (providerCount > 0) append(" · $providerCount 个服务商")
                                        activeVoice?.let { append(" · ${it.displayName}") }
                                    },
                                )
                            }
                        }

                        ErrorBanner(
                            message = connectionError,
                            modifier = Modifier.padding(top = if (connectionError.isNotBlank()) 12.dp else 0.dp),
                        )

                        Spacer(Modifier.height(12.dp))
                        HorizontalDivider()
                        Spacer(Modifier.height(10.dp))

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            val isError = connectionStatus == ConnectionStatus.ERROR
                            Button(
                                onClick = actions::refresh,
                                enabled = !busy,
                                modifier = Modifier.weight(1f).heightIn(min = 42.dp),
                                cornerRadius = 12.dp,
                                colors = if (isError) ButtonDefaults.buttonColorsPrimary() else ButtonDefaults.buttonColors(),
                            ) {
                                if (refreshing) {
                                    InfiniteProgressIndicator(
                                        size = 15.dp,
                                        strokeWidth = 1.8.dp,
                                        color = if (isError) colors.onPrimary else colors.primary,
                                    )
                                    Spacer(Modifier.width(6.dp))
                                }
                                Text(
                                    text = when {
                                        refreshing -> "同步中…"
                                        isError -> "重新连接"
                                        else -> "刷新目录"
                                    },
                                    style = MiuixTheme.textStyles.body2,
                                    fontWeight = FontWeight.Medium,
                                    color = if (isError) colors.onPrimary else colors.onSurface,
                                )
                            }
                            Button(
                                onClick = onStartEdit,
                                enabled = !busy,
                                modifier = Modifier.weight(1f).heightIn(min = 42.dp),
                                cornerRadius = 12.dp,
                            ) {
                                Text(
                                    text = "更改服务器",
                                    style = MiuixTheme.textStyles.body2,
                                    fontWeight = FontWeight.Medium,
                                )
                            }
                        }
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        ConnectionFormCard(
                            savedAddress = address,
                            savedHasKey = hasKey,
                            connecting = connecting,
                            submitLabel = if (connecting) "正在验证…" else "验证并保存",
                            onCancel = onCancelEdit,
                            onSubmit = onSaveEdit,
                        )
                        ErrorBanner(formError)
                    }
                }
            }

            SmallTitle(
                text = "系统文字转语音",
                insideMargin = PaddingValues(start = 6.dp, top = 10.dp, bottom = 4.dp),
            )
            Card(Modifier.fillMaxWidth()) {
                BasicComponent(
                    title = "设为系统朗读引擎",
                    summary = "打开系统设置，将「声读 · Legado TTS」设为首选引擎，供开源阅读等应用调用",
                    endActions = {
                        Icon(
                            MiuixIcons.Basic.ArrowRight,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp),
                            tint = colors.onSurfaceVariantActions,
                        )
                    },
                    onClick = {
                        try {
                            context.startActivity(Intent("com.android.settings.TTS_SETTINGS"))
                        } catch (_: ActivityNotFoundException) {
                            Toast.makeText(context, "请在系统设置中搜索“文字转语音”", Toast.LENGTH_LONG).show()
                        } catch (_: SecurityException) {
                            Toast.makeText(context, "请在系统设置中搜索“文字转语音”", Toast.LENGTH_LONG).show()
                        }
                    },
                )
                HorizontalDivider(Modifier.padding(horizontal = 14.dp))
                BasicComponent(
                    title = "系统引擎默认音色",
                    summary = activeVoice?.let {
                        "${providerName(it.provider)} · ${it.displayName} · 基准语速 ${formatSpeed(speed)}×"
                    } ?: "尚未选择音色",
                    endActions = {
                        Text("更换", style = MiuixTheme.textStyles.body2, color = colors.primary)
                    },
                    onClick = openCatalog,
                )
            }
            Caption(
                "其他应用调用系统引擎时，文本会发送至当前已连接的服务端并消耗相应配额；访问密钥不会暴露给调用方。",
                Modifier.padding(horizontal = 6.dp),
            )

            SmallTitle(
                text = "关于",
                insideMargin = PaddingValues(start = 6.dp, top = 10.dp, bottom = 4.dp),
            )
            Card(Modifier.fillMaxWidth()) {
                val appVersion = remember(context) {
                    runCatching {
                        val pm = context.packageManager
                        val info = if (Build.VERSION.SDK_INT >= 33) {
                            pm.getPackageInfo(context.packageName, PackageManager.PackageInfoFlags.of(0))
                        } else {
                            @Suppress("DEPRECATION")
                            pm.getPackageInfo(context.packageName, 0)
                        }
                        info.versionName.orEmpty().ifBlank { "1.2" }
                    }.getOrDefault("1.2")
                }
                BasicComponent(
                    title = "声读 · Legado TTS",
                    summary = "版本 $appVersion · 设置与密钥仅使用本机 Keystore 加密保存，不上传任何第三方",
                )
            }
            Spacer(Modifier.height(16.dp))
        }
    }
}

@Composable
private fun ConnectionFormCard(
    savedAddress: String,
    savedHasKey: Boolean,
    connecting: Boolean,
    submitLabel: String,
    onCancel: (() -> Unit)?,
    onSubmit: (String, String?) -> Unit,
) {
    var address by remember(savedAddress) { mutableStateOf(savedAddress) }
    var replacingKey by remember(savedAddress, savedHasKey) { mutableStateOf(false) }
    var key by remember(address) { mutableStateOf("") }
    var showKey by remember { mutableStateOf(false) }
    val normalizedCurrent = address.trim().trimEnd('/')
    val reuseKey = savedHasKey && normalizedCurrent == savedAddress && !replacingKey
    val focus = LocalFocusManager.current
    val colors = MiuixTheme.colorScheme

    Card(Modifier.fillMaxWidth(), insideMargin = PaddingValues(14.dp)) {
        // No saved-instance-state or Compose saveable state for either field; an
        // accidentally pasted URL with credentials must not enter the system Bundle.
        CompositionLocalProvider(LocalSaveableStateRegistry provides null) {
            Text("服务端地址", style = MiuixTheme.textStyles.body2, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(6.dp))
            TextField(
                value = address,
                onValueChange = { address = it },
                label = "例如 https://tts.example.com",
                useLabelAsPlaceholder = true,
                enabled = !connecting,
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            )
            Spacer(Modifier.height(6.dp))
            Caption("填写服务根地址（可含反代路径）。携带访问密钥时必须使用 HTTPS；局域网无密钥可使用 HTTP。")

            Spacer(Modifier.height(14.dp))
            Text("访问密钥", style = MiuixTheme.textStyles.body2, fontWeight = FontWeight.Medium)
            Spacer(Modifier.height(6.dp))
            AnimatedContent(
                targetState = reuseKey,
                transitionSpec = { AppMotion.panelSwap() },
                label = "key-input-mode",
            ) { reuse ->
                if (reuse) {
                    Surface(
                        modifier = Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(12.dp),
                        color = colors.secondaryContainer,
                    ) {
                        BasicComponent(
                            title = "已加密保存访问密钥",
                            summary = "点按更换新密钥，或留空以移除密钥",
                            onClick = { replacingKey = true },
                            enabled = !connecting,
                            endActions = {
                                Text("更换", style = MiuixTheme.textStyles.body2, color = colors.primary)
                            },
                        )
                    }
                } else {
                    TextField(
                        value = key,
                        onValueChange = { key = it },
                        label = "访问密钥（服务未开启鉴权可留空）",
                        useLabelAsPlaceholder = true,
                        singleLine = true,
                        enabled = !connecting,
                        modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Password,
                            autoCorrectEnabled = false,
                        ),
                        visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
                        trailingIcon = if (key.isNotEmpty()) {
                            {
                                IconButton(onClick = { showKey = !showKey }, minWidth = 36.dp, minHeight = 36.dp) {
                                    Icon(
                                        if (showKey) eyeOffIcon else eyeIcon,
                                        contentDescription = if (showKey) "隐藏密钥" else "显示密钥",
                                        modifier = Modifier.size(18.dp),
                                        tint = colors.onSurfaceVariantActions,
                                    )
                                }
                            }
                        } else null,
                    )
                }
            }
            Spacer(Modifier.height(6.dp))
            Caption("密钥仅通过系统 Keystore 加密留在本机，更换地址后需重新输入。")
        }

        Spacer(Modifier.height(16.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (onCancel != null) {
                Button(
                    onClick = {
                        focus.clearFocus()
                        onCancel()
                    },
                    enabled = !connecting,
                    modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                    cornerRadius = 12.dp,
                ) {
                    Text("取消", style = MiuixTheme.textStyles.button)
                }
            }
            val submitEnabled = !connecting && address.isNotBlank()
            val submitFg by animateColorAsState(
                if (submitEnabled) colors.onPrimary else colors.disabledOnPrimaryButton,
                AppMotion.colorSpec(),
                label = "submit-fg",
            )
            Button(
                onClick = {
                    focus.clearFocus()
                    val submittedKey = if (reuseKey) null else key
                    onSubmit(address.trim(), submittedKey)
                },
                enabled = submitEnabled,
                modifier = Modifier.weight(if (onCancel != null) 1.4f else 1f).heightIn(min = 44.dp),
                cornerRadius = 12.dp,
                colors = ButtonDefaults.buttonColorsPrimary(),
            ) {
                if (connecting) {
                    InfiniteProgressIndicator(size = 16.dp, strokeWidth = 1.8.dp, color = submitFg)
                    Spacer(Modifier.width(8.dp))
                }
                Text(submitLabel, style = MiuixTheme.textStyles.button, color = submitFg)
            }
        }
    }
}

@Composable
private fun MiniBadge(text: String, accent: Boolean) {
    val colors = MiuixTheme.colorScheme
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (accent) colors.tertiaryContainer else colors.secondaryContainer)
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(
            text = text,
            style = MiuixTheme.textStyles.footnote2,
            fontWeight = FontWeight.Medium,
            color = if (accent) colors.onTertiaryContainer else colors.onSurfaceVariantSummary,
        )
    }
}
