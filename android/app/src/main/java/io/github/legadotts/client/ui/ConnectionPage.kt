package io.github.legadotts.client.ui

import android.content.ActivityNotFoundException
import android.content.Intent
import android.widget.Toast
import androidx.compose.animation.*
import androidx.compose.animation.core.tween
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
    state: ReaderState,
    actions: ReaderActions,
    openCatalog: () -> Unit,
) {
    // When already configured, default to showing the Connected status overview — never the setup form.
    var editing by rememberSaveable { mutableStateOf(false) }
    var awaitingSave by remember { mutableStateOf(false) }

    LaunchedEffect(state.connecting, state.formError, state.connectionStatus) {
        if (awaitingSave && !state.connecting) {
            awaitingSave = false
            if (state.formError.isEmpty() && state.connectionStatus == ConnectionStatus.CONNECTED) {
                editing = false
            }
        }
    }

    if (!state.configured) {
        OnboardingConnectionView(
            state = state,
            onSubmit = { addr, key -> actions.connect(addr, key) },
        )
    } else {
        ConfiguredSettingsView(
            state = state,
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
    state: ReaderState,
    onSubmit: (String, String?) -> Unit,
) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .overScrollVertical()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 20.dp, vertical = 28.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Spacer(Modifier.height(8.dp))
        Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
            AppMark(size = 56)
            Text("连接你的服务端", style = MiuixTheme.textStyles.title1, fontWeight = FontWeight.Bold)
            Caption("输入手机可访问的 Legado TTS 服务地址，验证通过后即可浏览音色、朗读文章或设为系统语音引擎。")
        }

        ConnectionFormCard(
            savedAddress = state.address,
            savedHasKey = state.hasKey,
            connecting = state.connecting,
            submitLabel = if (state.connecting) "正在验证…" else "验证并连接",
            onCancel = null,
            onSubmit = onSubmit,
        )

        ErrorBanner(state.connectionError)
        Spacer(Modifier.height(12.dp))
    }
}

@Composable
private fun ConfiguredSettingsView(
    state: ReaderState,
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
    val providerCount = remember(state.voices) { state.voices.map { it.provider }.distinct().size }
    val isHttps = state.address.startsWith("https://", ignoreCase = true)

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "设置",
            subtitle = when (state.connectionStatus) {
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
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
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
                    Card(
                        Modifier.fillMaxWidth().animateContentSize(AppMotion.sizeSpec()),
                        insideMargin = PaddingValues(16.dp),
                    ) {
                        // Status header row
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.SpaceBetween,
                        ) {
                            val green = connectedGreen(dark)
                            when (state.connectionStatus) {
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
                                MiniBadge(
                                    text = if (isHttps) "HTTPS" else "HTTP",
                                    accent = isHttps,
                                )
                                AnimatedVisibility(
                                    visible = state.hasKey,
                                    enter = fadeIn(tween(180)) + expandHorizontally(AppMotion.sizeSpec()),
                                    exit = fadeOut(tween(130)) + shrinkHorizontally(AppMotion.sizeSpec()),
                                ) {
                                    MiniBadge(text = "已设密钥", accent = true)
                                }
                            }
                        }

                        Spacer(Modifier.height(14.dp))

                        // Server address display
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(38.dp)
                                    .clip(CircleShape)
                                    .background(colors.primary.copy(alpha = 0.12f)),
                                contentAlignment = Alignment.Center,
                            ) {
                                Icon(serverIcon, null, Modifier.size(19.dp), tint = colors.primary)
                            }
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    text = state.address,
                                    style = MiuixTheme.textStyles.body1,
                                    fontWeight = FontWeight.Medium,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Caption(
                                    buildString {
                                        append("${state.voices.size} 个可用音色")
                                        if (providerCount > 0) append(" · $providerCount 个服务商")
                                        state.activeVoice?.let { append(" · ${it.displayName}") }
                                    },
                                )
                            }
                        }

                        ErrorBanner(
                            message = state.connectionError,
                            modifier = Modifier.padding(top = if (state.connectionError.isNotBlank()) 12.dp else 0.dp),
                        )

                        Spacer(Modifier.height(14.dp))
                        HorizontalDivider()
                        Spacer(Modifier.height(12.dp))

                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            val isError = state.connectionStatus == ConnectionStatus.ERROR
                            val syncFg by animateColorAsState(
                                if (isError) colors.onPrimary else colors.onSurface,
                                AppMotion.colorSpec(),
                                label = "sync-btn-fg",
                            )
                            Button(
                                onClick = actions::refresh,
                                enabled = !state.busy,
                                modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                                cornerRadius = 12.dp,
                                colors = if (isError) ButtonDefaults.buttonColorsPrimary() else ButtonDefaults.buttonColors(),
                            ) {
                                AnimatedContent(
                                    targetState = state.refreshing to isError,
                                    transitionSpec = { AppMotion.textFade() },
                                    label = "sync-btn-content",
                                ) { (refreshing, err) ->
                                    Row(verticalAlignment = Alignment.CenterVertically) {
                                        if (refreshing) {
                                            InfiniteProgressIndicator(
                                                size = 15.dp,
                                                strokeWidth = 1.8.dp,
                                                color = if (err) colors.onPrimary else colors.primary,
                                            )
                                            Spacer(Modifier.width(6.dp))
                                        }
                                        Text(
                                            text = when {
                                                refreshing -> "同步中…"
                                                err -> "重新连接"
                                                else -> "刷新目录"
                                            },
                                            style = MiuixTheme.textStyles.body2,
                                            fontWeight = FontWeight.Medium,
                                            color = syncFg,
                                        )
                                    }
                                }
                            }
                            Button(
                                onClick = onStartEdit,
                                enabled = !state.busy,
                                modifier = Modifier.weight(1f).heightIn(min = 44.dp),
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
                            savedAddress = state.address,
                            savedHasKey = state.hasKey,
                            connecting = state.connecting,
                            submitLabel = if (state.connecting) "正在验证…" else "验证并保存",
                            onCancel = onCancelEdit,
                            onSubmit = onSaveEdit,
                        )
                        ErrorBanner(state.formError)
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
                HorizontalDivider(Modifier.padding(horizontal = 16.dp))
                BasicComponent(
                    title = "系统引擎默认音色",
                    summary = state.activeVoice?.let {
                        "${providerName(it.provider)} · ${it.displayName} · 基准语速 ${formatSpeed(state.speed)}×"
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
                BasicComponent(
                    title = "声读 · Legado TTS",
                    summary = "版本 1.0 · 设置与密钥仅使用本机 Keystore 加密保存，不上传任何第三方",
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

    Card(
        Modifier.fillMaxWidth().animateContentSize(AppMotion.sizeSpec()),
        insideMargin = PaddingValues(16.dp),
    ) {
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

            Spacer(Modifier.height(16.dp))
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
                        trailingIcon = {
                            AnimatedVisibility(
                                visible = key.isNotEmpty(),
                                enter = fadeIn(tween(150)) + scaleIn(initialScale = 0.75f),
                                exit = fadeOut(tween(120)) + scaleOut(targetScale = 0.75f),
                            ) {
                                IconButton(onClick = { showKey = !showKey }, minWidth = 36.dp, minHeight = 36.dp) {
                                    AnimatedContent(
                                        targetState = showKey,
                                        transitionSpec = { AppMotion.iconSwap() },
                                        label = "eye-toggle",
                                    ) { visible ->
                                        Icon(
                                            if (visible) eyeOffIcon else eyeIcon,
                                            contentDescription = if (visible) "隐藏密钥" else "显示密钥",
                                            modifier = Modifier.size(18.dp),
                                            tint = colors.onSurfaceVariantActions,
                                        )
                                    }
                                }
                            }
                        },
                    )
                }
            }
            Spacer(Modifier.height(6.dp))
            Caption("密钥仅通过系统 Keystore 加密留在本机，更换地址后需重新输入。")
        }

        Spacer(Modifier.height(18.dp))
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
                    modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    cornerRadius = 14.dp,
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
                modifier = Modifier.weight(if (onCancel != null) 1.4f else 1f).heightIn(min = 48.dp),
                cornerRadius = 14.dp,
                colors = ButtonDefaults.buttonColorsPrimary(),
            ) {
                AnimatedContent(
                    targetState = connecting to submitLabel,
                    transitionSpec = { AppMotion.textFade() },
                    label = "submit-content",
                ) { (busy, label) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        if (busy) {
                            InfiniteProgressIndicator(size = 16.dp, strokeWidth = 1.8.dp, color = submitFg)
                            Spacer(Modifier.width(8.dp))
                        }
                        Text(label, style = MiuixTheme.textStyles.button, color = submitFg)
                    }
                }
            }
        }
    }
}

@Composable
private fun MiniBadge(text: String, accent: Boolean) {
    val colors = MiuixTheme.colorScheme
    val bg by animateColorAsState(
        if (accent) colors.tertiaryContainer else colors.secondaryContainer,
        AppMotion.colorSpec(),
        label = "mini-badge-bg",
    )
    val fg by animateColorAsState(
        if (accent) colors.onTertiaryContainer else colors.onSurfaceVariantSummary,
        AppMotion.colorSpec(),
        label = "mini-badge-fg",
    )
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(bg)
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Text(
            text = text,
            style = MiuixTheme.textStyles.footnote2,
            fontWeight = FontWeight.Medium,
            color = fg,
        )
    }
}
