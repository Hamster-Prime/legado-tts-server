package io.github.legadotts.client.ui

import android.content.ClipboardManager
import androidx.compose.animation.*
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.LocalSaveableStateRegistry
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.basic.*
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.basic.ArrowRight
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.utils.overScrollVertical
import kotlin.math.max
import kotlin.math.roundToInt

private val speedPresets = listOf(0.8f, 1.0f, 1.2f, 1.5f, 2.0f)

private const val SAMPLE_TEXT =
    "夜色渐深，窗外飘起了细密的秋雨，屋内的台灯透出柔和的暖光。\n\n" +
        "翻开书页，文字随着声音缓缓流淌。段落之间自然衔接，不再有突兀的留白，让听书真正成为一种轻松的享受。"

@Composable
internal fun ReadingPage(
    state: ReaderState,
    actions: ReaderActions,
    openCatalog: () -> Unit,
    openSettings: () -> Unit,
    showMessage: (String) -> Unit,
) {
    val voice = state.activeVoice
    val focus = LocalFocusManager.current
    val context = LocalContext.current
    val dark = isSystemInDarkTheme()
    val colors = MiuixTheme.colorScheme
    var speed by remember(state.speed) { mutableFloatStateOf(state.speed.coerceIn(0.5f, 2f)) }
    val scrollBehavior = MiuixScrollBehavior()

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "朗读",
            subtitle = voice?.let { "${providerName(it.provider)} · ${it.displayName}" } ?: "点击下方选择朗读音色",
            scrollBehavior = scrollBehavior,
            actions = {
                val (label, dot, bg, fg, loading) = when (state.connectionStatus) {
                    ConnectionStatus.CONNECTED -> StatusVisual(
                        "已连接", connectedGreen(dark),
                        connectedGreen(dark).copy(alpha = 0.14f), connectedGreen(dark), false,
                    )
                    ConnectionStatus.CONNECTING -> StatusVisual(
                        "连接中", null,
                        colors.primary.copy(alpha = 0.12f), colors.primary, true,
                    )
                    ConnectionStatus.ERROR -> StatusVisual(
                        "连接异常", colors.error,
                        colors.errorContainer, colors.onErrorContainer, false,
                    )
                    ConnectionStatus.DISCONNECTED -> StatusVisual(
                        "未连接", colors.onSurfaceVariantSummary,
                        colors.secondaryContainer, colors.onSurfaceVariantSummary, false,
                    )
                }
                StatusPill(
                    text = label,
                    containerColor = bg,
                    contentColor = fg,
                    modifier = Modifier
                        .padding(end = 12.dp)
                        .clip(RoundedCornerShape(100))
                        .clickable(onClick = openSettings),
                    dotColor = dot,
                    loading = loading,
                )
            },
        )

        Column(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .overScrollVertical()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Card 1: Text input card with animated paste / sample / clear tools
            Card(
                Modifier.fillMaxWidth().animateContentSize(AppMotion.sizeSpec()),
                insideMargin = PaddingValues(16.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.SpaceBetween,
                ) {
                    Column(Modifier.weight(1f)) {
                        Text("朗读文本", style = MiuixTheme.textStyles.subtitle)
                        val count = state.text.trim().length
                        val meta = if (count == 0) "输入或粘贴任意段落"
                        else {
                            val sec = max(1, (count / (4.4f * speed)).roundToInt())
                            val timeText = if (sec < 60) "约 $sec 秒" else "约 ${sec / 60} 分 ${sec % 60} 秒"
                            "$count 字 · $timeText"
                        }
                        Caption(meta)
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        InlinePillAction(label = "粘贴") {
                            val cm = context.getSystemService(ClipboardManager::class.java)
                            val clip = cm?.primaryClip?.takeIf { it.itemCount > 0 }
                                ?.getItemAt(0)?.coerceToText(context)?.toString()?.trim()
                            if (clip.isNullOrEmpty()) {
                                showMessage("剪贴板中没有可用文本")
                            } else {
                                actions.editText(clip)
                                showMessage("已粘贴 ${clip.length} 字")
                            }
                        }
                        AnimatedContent(
                            targetState = state.text.isEmpty(),
                            transitionSpec = { AppMotion.iconSwap() },
                            label = "sample-clear-pill",
                        ) { empty ->
                            if (empty) {
                                InlinePillAction(label = "示例") { actions.editText(SAMPLE_TEXT) }
                            } else {
                                InlinePillAction(label = "清空") { actions.editText("") }
                            }
                        }
                    }
                }
                Spacer(Modifier.height(12.dp))
                // The editor is owned by the host; never put book-length text in a Bundle.
                CompositionLocalProvider(LocalSaveableStateRegistry provides null) {
                    TextField(
                        value = state.text,
                        onValueChange = actions::editText,
                        modifier = Modifier.fillMaxWidth(),
                        label = "在这里输入或粘贴要朗读的文章或段落…",
                        useLabelAsPlaceholder = true,
                        minLines = 5,
                        maxLines = 9,
                    )
                }
            }

            // Card 2: Voice selector + Speed slider & animated presets
            Card(Modifier.fillMaxWidth()) {
                val targetAccent = voice?.let { providerAccent(it.provider, dark) } ?: colors.primary
                val accent by animateColorAsState(targetAccent, AppMotion.colorSpec(), label = "voice-accent")
                BasicComponent(
                    title = voice?.displayName ?: "选择朗读音色",
                    summary = if (voice == null) "从服务端音色库中选择默认音色"
                    else listOfNotNull(providerName(voice.provider), voice.tag.takeIf { it.isNotBlank() }).joinToString(" · "),
                    startAction = {
                        Box(
                            modifier = Modifier
                                .padding(end = 12.dp)
                                .size(40.dp)
                                .clip(CircleShape)
                                .background(accent.copy(alpha = 0.14f)),
                            contentAlignment = Alignment.Center,
                        ) {
                            AnimatedContent(
                                targetState = voice?.displayName?.take(1),
                                transitionSpec = { AppMotion.iconSwap() },
                                label = "voice-monogram",
                            ) { glyphChar ->
                                if (glyphChar != null) {
                                    Text(
                                        text = glyphChar,
                                        style = MiuixTheme.textStyles.subtitle,
                                        fontWeight = FontWeight.SemiBold,
                                        color = accent,
                                    )
                                } else {
                                    Icon(soundWaveIcon, null, Modifier.size(20.dp), tint = accent)
                                }
                            }
                        }
                    },
                    endActions = {
                        Text(
                            text = "更换",
                            style = MiuixTheme.textStyles.body2,
                            color = colors.primary,
                            modifier = Modifier.padding(end = 4.dp),
                        )
                        Icon(
                            MiuixIcons.Basic.ArrowRight,
                            contentDescription = null,
                            modifier = Modifier.size(16.dp),
                            tint = colors.onSurfaceVariantActions,
                        )
                    },
                    onClick = openCatalog,
                )

                HorizontalDivider(Modifier.padding(horizontal = 16.dp))

                Column(Modifier.fillMaxWidth().padding(16.dp)) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.SpaceBetween,
                    ) {
                        Text("语速调节", style = MiuixTheme.textStyles.body1, fontWeight = FontWeight.Medium)
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(8.dp))
                                .background(colors.tertiaryContainer)
                                .padding(horizontal = 9.dp, vertical = 3.dp),
                        ) {
                            AnimatedContent(
                                targetState = formatSpeed(speed),
                                transitionSpec = {
                                    val up = targetState > initialState
                                    (slideInVertically(tween(150)) { if (up) it / 2 else -it / 2 } + fadeIn(tween(150)))
                                        .togetherWith(slideOutVertically(tween(110)) { if (up) -it / 2 else it / 2 } + fadeOut(tween(110)))
                                },
                                label = "speed-badge",
                            ) { formatted ->
                                Text(
                                    text = "$formatted×",
                                    style = MiuixTheme.textStyles.body2,
                                    fontWeight = FontWeight.SemiBold,
                                    color = colors.onTertiaryContainer,
                                )
                            }
                        }
                    }
                    Spacer(Modifier.height(10.dp))
                    Slider(
                        value = speed,
                        onValueChange = { speed = (it * 10f).roundToInt() / 10f },
                        enabled = !state.connecting,
                        valueRange = 0.5f..2f,
                        steps = 14,
                        onValueChangeFinished = { actions.setSpeed(speed) },
                        modifier = Modifier.fillMaxWidth().semantics { contentDescription = "朗读语速" },
                    )
                    Spacer(Modifier.height(10.dp))
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        for (preset in speedPresets) {
                            val active = kotlin.math.abs(speed - preset) < 0.04f
                            val pillBg by animateColorAsState(
                                if (active) colors.primary else colors.secondaryContainer,
                                AppMotion.colorSpec(),
                                label = "preset-bg-$preset",
                            )
                            val pillFg by animateColorAsState(
                                if (active) colors.onPrimary else colors.onSurface,
                                AppMotion.colorSpec(),
                                label = "preset-fg-$preset",
                            )
                            val pillScale by animateFloatAsState(
                                if (active) 1f else 0.97f,
                                AppMotion.popSpec(),
                                label = "preset-scale-$preset",
                            )
                            Box(
                                modifier = Modifier
                                    .weight(1f)
                                    .graphicsLayer { scaleX = pillScale; scaleY = pillScale }
                                    .clip(RoundedCornerShape(10.dp))
                                    .background(pillBg)
                                    .clickable(enabled = !state.connecting) {
                                        speed = preset
                                        actions.setSpeed(preset)
                                    }
                                    .padding(vertical = 7.dp),
                                contentAlignment = Alignment.Center,
                            ) {
                                Text(
                                    text = "${formatSpeed(preset)}×",
                                    style = MiuixTheme.textStyles.footnote1,
                                    fontWeight = if (active) FontWeight.SemiBold else FontWeight.Normal,
                                    color = pillFg,
                                    maxLines = 1,
                                )
                            }
                        }
                    }
                }
            }

            // Animated error banner
            ErrorBanner(
                message = state.playError,
                onRetry = if (state.text.isNotBlank() && voice != null) actions::play else null,
                onDismiss = actions::dismissPlayError,
            )

            // Card 3 / Transport Controls with smooth state morphing
            val readingActive = state.transport != Transport.IDLE && !state.playingPreview
            AnimatedContent(
                targetState = readingActive,
                transitionSpec = { AppMotion.panelSwap() },
                label = "transport-panel",
            ) { active ->
                if (!active) {
                    val playEnabled = !state.connecting && state.text.isNotBlank() && voice != null
                    val btnContent by animateColorAsState(
                        if (playEnabled) colors.onPrimary else colors.disabledOnPrimaryButton,
                        AppMotion.colorSpec(),
                        label = "play-btn-fg",
                    )
                    Button(
                        onClick = {
                            focus.clearFocus()
                            actions.play()
                        },
                        modifier = Modifier.fillMaxWidth().heightIn(min = 52.dp),
                        enabled = playEnabled,
                        cornerRadius = 16.dp,
                        colors = ButtonDefaults.buttonColorsPrimary(),
                    ) {
                        Icon(playIcon, null, Modifier.size(18.dp), tint = btnContent)
                        Spacer(Modifier.width(8.dp))
                        Text(
                            text = "开始朗读",
                            style = MiuixTheme.textStyles.button,
                            color = btnContent,
                        )
                    }
                } else {
                    val smoothProgress by animateFloatAsState(
                        targetValue = (state.playProgress ?: 0f).coerceIn(0f, 1f),
                        animationSpec = tween(320, easing = FastOutSlowInEasing),
                        label = "reading-progress",
                    )
                    Card(
                        Modifier.fillMaxWidth().animateContentSize(AppMotion.sizeSpec()),
                        insideMargin = PaddingValues(16.dp),
                    ) {
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Box(
                                modifier = Modifier
                                    .size(36.dp)
                                    .clip(CircleShape)
                                    .background(colors.primary.copy(alpha = 0.14f)),
                                contentAlignment = Alignment.Center,
                            ) {
                                AnimatedContent(
                                    targetState = state.transport,
                                    transitionSpec = { AppMotion.iconSwap() },
                                    label = "playing-state-icon",
                                ) { tr ->
                                    when (tr) {
                                        Transport.BUFFERING -> InfiniteProgressIndicator(size = 18.dp, strokeWidth = 2.dp)
                                        Transport.PAUSED -> Icon(pauseIcon, null, Modifier.size(17.dp), tint = colors.primary)
                                        else -> Icon(soundWaveIcon, null, Modifier.size(18.dp), tint = colors.primary)
                                    }
                                }
                            }
                            Spacer(Modifier.width(12.dp))
                            Column(Modifier.weight(1f)) {
                                val title = when (state.transport) {
                                    Transport.BUFFERING -> "正在合成音频…"
                                    Transport.PAUSED -> "朗读已暂停"
                                    else -> "正在朗读"
                                }
                                AnimatedContent(
                                    targetState = title,
                                    transitionSpec = { AppMotion.textFade() },
                                    label = "playing-title",
                                ) { t ->
                                    Text(t, style = MiuixTheme.textStyles.body1, fontWeight = FontWeight.Medium)
                                }
                                val sub = buildString {
                                    append(state.playingVoiceName.substringBefore(" - ").ifBlank { voice?.displayName.orEmpty() })
                                    if (state.playSegment > 0) append(" · 第 ${state.playSegment} 段")
                                }
                                if (sub.isNotBlank()) {
                                    Caption(sub, Modifier.fillMaxWidth())
                                }
                            }
                            AnimatedVisibility(
                                visible = state.playProgress != null && state.transport != Transport.BUFFERING,
                                enter = fadeIn(tween(180)),
                                exit = fadeOut(tween(130)),
                            ) {
                                Text(
                                    text = "${(smoothProgress * 100).roundToInt()}%",
                                    style = MiuixTheme.textStyles.footnote1,
                                    fontWeight = FontWeight.SemiBold,
                                    color = colors.primary,
                                )
                            }
                        }

                        Spacer(Modifier.height(12.dp))
                        LinearProgressIndicator(
                            progress = if (state.transport == Transport.BUFFERING) null else smoothProgress,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(Modifier.height(14.dp))

                        AnimatedContent(
                            targetState = state.transport == Transport.BUFFERING,
                            transitionSpec = { AppMotion.textFade() },
                            label = "transport-buttons",
                        ) { buffering ->
                            if (buffering) {
                                Button(
                                    onClick = actions::stop,
                                    modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                                    cornerRadius = 14.dp,
                                ) {
                                    Icon(stopIcon, null, Modifier.size(16.dp), tint = colors.onSurface)
                                    Spacer(Modifier.width(8.dp))
                                    Text("取消合成", style = MiuixTheme.textStyles.button)
                                }
                            } else {
                                val paused = state.transport == Transport.PAUSED
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                                ) {
                                    Button(
                                        onClick = if (paused) actions::resume else actions::pause,
                                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                                        cornerRadius = 14.dp,
                                        colors = ButtonDefaults.buttonColorsPrimary(),
                                    ) {
                                        AnimatedContent(
                                            targetState = paused,
                                            transitionSpec = { AppMotion.iconSwap() },
                                            label = "pause-resume-content",
                                        ) { isPaused ->
                                            Row(verticalAlignment = Alignment.CenterVertically) {
                                                Icon(
                                                    if (isPaused) playIcon else pauseIcon,
                                                    null,
                                                    Modifier.size(16.dp),
                                                    tint = colors.onPrimary,
                                                )
                                                Spacer(Modifier.width(8.dp))
                                                Text(
                                                    if (isPaused) "继续朗读" else "暂停",
                                                    style = MiuixTheme.textStyles.button,
                                                    color = colors.onPrimary,
                                                )
                                            }
                                        }
                                    }
                                    Button(
                                        onClick = actions::stop,
                                        modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                                        cornerRadius = 14.dp,
                                    ) {
                                        Icon(stopIcon, null, Modifier.size(15.dp), tint = colors.onSurface)
                                        Spacer(Modifier.width(8.dp))
                                        Text("停止", style = MiuixTheme.textStyles.button)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            Caption(
                "长文本自动分段预取并无缝衔接，支持切至后台或锁屏继续收听。",
                Modifier.padding(horizontal = 6.dp),
            )
            Spacer(Modifier.height(12.dp))
        }
    }
}

private data class StatusVisual(
    val label: String,
    val dot: androidx.compose.ui.graphics.Color?,
    val bg: androidx.compose.ui.graphics.Color,
    val fg: androidx.compose.ui.graphics.Color,
    val loading: Boolean,
)

@Composable
private fun InlinePillAction(label: String, onClick: () -> Unit) {
    val colors = MiuixTheme.colorScheme
    Box(
        modifier = Modifier
            .clip(RoundedCornerShape(100))
            .background(colors.tertiaryContainer)
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 5.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            style = MiuixTheme.textStyles.footnote1,
            fontWeight = FontWeight.Medium,
            color = colors.onTertiaryContainer,
            maxLines = 1,
            overflow = TextOverflow.Clip,
        )
    }
}
