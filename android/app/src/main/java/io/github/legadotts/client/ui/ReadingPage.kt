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
    activeVoice: VoiceItem?,
    text: String,
    speed: Float,
    connectionStatus: ConnectionStatus,
    connecting: Boolean,
    transport: Transport,
    playingPreview: Boolean,
    playingVoiceName: String,
    playProgress: Float?,
    playSegment: Int,
    playError: String,
    actions: ReaderActions,
    openCatalog: () -> Unit,
    openSettings: () -> Unit,
    showMessage: (String) -> Unit,
) {
    val dark = isSystemInDarkTheme()
    val colors = MiuixTheme.colorScheme
    val scrollBehavior = MiuixScrollBehavior()

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "朗读",
            subtitle = activeVoice?.let { "${providerName(it.provider)} · ${it.displayName}" } ?: "点击下方选择朗读音色",
            scrollBehavior = scrollBehavior,
            actions = {
                val green = connectedGreen(dark)
                val (label, dot, bg, fg, loading) = when (connectionStatus) {
                    ConnectionStatus.CONNECTED -> StatusVisual("已连接", green, green.copy(alpha = 0.14f), green, false)
                    ConnectionStatus.CONNECTING -> StatusVisual("连接中", null, colors.primary.copy(alpha = 0.12f), colors.primary, true)
                    ConnectionStatus.ERROR -> StatusVisual("连接异常", colors.error, colors.errorContainer, colors.onErrorContainer, false)
                    ConnectionStatus.DISCONNECTED -> StatusVisual("未连接", colors.onSurfaceVariantSummary, colors.secondaryContainer, colors.onSurfaceVariantSummary, false)
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
                .padding(horizontal = 14.dp, vertical = 10.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            TextEditorCard(
                text = text,
                speed = speed,
                onEditText = actions::editText,
                showMessage = showMessage,
            )

            // Card 2: Voice selector + isolated SpeedControlSection (dragging slider won't recompose editor!)
            Card(Modifier.fillMaxWidth()) {
                VoiceSelectorRow(voice = activeVoice, dark = dark, onClick = openCatalog)
                HorizontalDivider(Modifier.padding(horizontal = 14.dp))
                SpeedControlSection(
                    savedSpeed = speed,
                    enabled = !connecting,
                    onCommitSpeed = actions::setSpeed,
                )
            }

            ErrorBanner(
                message = playError,
                onRetry = if (text.isNotBlank() && activeVoice != null) actions::play else null,
                onDismiss = actions::dismissPlayError,
            )

            PlaybackControlSection(
                readingActive = transport != Transport.IDLE && !playingPreview,
                canPlay = !connecting && text.isNotBlank() && activeVoice != null,
                transport = transport,
                playingVoiceLabel = playingVoiceName.substringBefore(" - ").ifBlank { activeVoice?.displayName.orEmpty() },
                playProgress = playProgress,
                playSegment = playSegment,
                actions = actions,
            )

            Caption(
                "长文本自动分段预取并无缝衔接，支持切至后台或锁屏继续收听。",
                Modifier.padding(horizontal = 6.dp),
            )
            Spacer(Modifier.height(10.dp))
        }
    }
}

@Composable
private fun TextEditorCard(
    text: String,
    speed: Float,
    onEditText: (String) -> Unit,
    showMessage: (String) -> Unit,
) {
    val context = LocalContext.current
    Card(Modifier.fillMaxWidth(), insideMargin = PaddingValues(14.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column(Modifier.weight(1f)) {
                Text("朗读文本", style = MiuixTheme.textStyles.subtitle)
                val count = text.trim().length
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
                        onEditText(clip)
                        showMessage("已粘贴 ${clip.length} 字")
                    }
                }
                AnimatedContent(
                    targetState = text.isEmpty(),
                    transitionSpec = { AppMotion.iconSwap() },
                    label = "sample-clear-pill",
                ) { empty ->
                    if (empty) {
                        InlinePillAction(label = "示例") { onEditText(SAMPLE_TEXT) }
                    } else {
                        InlinePillAction(label = "清空") { onEditText("") }
                    }
                }
            }
        }
        Spacer(Modifier.height(10.dp))
        // The editor is owned by the host; never put book-length text in a Bundle.
        CompositionLocalProvider(LocalSaveableStateRegistry provides null) {
            TextField(
                value = text,
                onValueChange = onEditText,
                modifier = Modifier.fillMaxWidth(),
                label = "在这里输入或粘贴要朗读的文章或段落…",
                useLabelAsPlaceholder = true,
                minLines = 4,
                maxLines = 8,
            )
        }
    }
}

@Composable
private fun VoiceSelectorRow(voice: VoiceItem?, dark: Boolean, onClick: () -> Unit) {
    val colors = MiuixTheme.colorScheme
    val accent by animateColorAsState(
        voice?.let { providerAccent(it.provider, dark) } ?: colors.primary,
        AppMotion.colorSpec(),
        label = "voice-accent",
    )
    BasicComponent(
        title = voice?.displayName ?: "选择朗读音色",
        summary = if (voice == null) "从服务端音色库中选择默认音色"
        else listOfNotNull(providerName(voice.provider), voice.tag.takeIf { it.isNotBlank() }).joinToString(" · "),
        startAction = {
            Box(
                modifier = Modifier
                    .padding(end = 10.dp)
                    .size(36.dp)
                    .clip(CircleShape)
                    .background(accent.copy(alpha = 0.14f)),
                contentAlignment = Alignment.Center,
            ) {
                if (voice != null) {
                    Text(
                        text = voice.displayName.take(1),
                        style = MiuixTheme.textStyles.body1,
                        fontWeight = FontWeight.SemiBold,
                        color = accent,
                    )
                } else {
                    Icon(soundWaveIcon, null, Modifier.size(18.dp), tint = accent)
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
        onClick = onClick,
    )
}

@Composable
private fun SpeedControlSection(
    savedSpeed: Float,
    enabled: Boolean,
    onCommitSpeed: (Float) -> Unit,
) {
    val colors = MiuixTheme.colorScheme
    var sliderSpeed by remember(savedSpeed) { mutableFloatStateOf(savedSpeed.coerceIn(0.5f, 2f)) }
    Column(Modifier.fillMaxWidth().padding(14.dp)) {
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
                Text(
                    text = "${formatSpeed(sliderSpeed)}×",
                    style = MiuixTheme.textStyles.body2,
                    fontWeight = FontWeight.SemiBold,
                    color = colors.onTertiaryContainer,
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        Slider(
            value = sliderSpeed,
            onValueChange = { sliderSpeed = (it * 10f).roundToInt() / 10f },
            enabled = enabled,
            valueRange = 0.5f..2f,
            steps = 14,
            onValueChangeFinished = { onCommitSpeed(sliderSpeed) },
            modifier = Modifier.fillMaxWidth().semantics { contentDescription = "朗读语速" },
        )
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            for (preset in speedPresets) {
                val active = kotlin.math.abs(sliderSpeed - preset) < 0.04f
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
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(10.dp))
                        .background(pillBg)
                        .clickable(enabled = enabled) {
                            sliderSpeed = preset
                            onCommitSpeed(preset)
                        }
                        .padding(vertical = 6.dp),
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

@Composable
private fun PlaybackControlSection(
    readingActive: Boolean,
    canPlay: Boolean,
    transport: Transport,
    playingVoiceLabel: String,
    playProgress: Float?,
    playSegment: Int,
    actions: ReaderActions,
) {
    val colors = MiuixTheme.colorScheme
    val focus = LocalFocusManager.current

    AnimatedContent(
        targetState = readingActive,
        transitionSpec = { AppMotion.panelSwap() },
        label = "transport-panel",
    ) { active ->
        if (!active) {
            val btnContent by animateColorAsState(
                if (canPlay) colors.onPrimary else colors.disabledOnPrimaryButton,
                AppMotion.colorSpec(),
                label = "play-btn-fg",
            )
            Button(
                onClick = {
                    focus.clearFocus()
                    actions.play()
                },
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
                enabled = canPlay,
                cornerRadius = 14.dp,
                colors = ButtonDefaults.buttonColorsPrimary(),
            ) {
                Icon(playIcon, null, Modifier.size(17.dp), tint = btnContent)
                Spacer(Modifier.width(8.dp))
                Text(
                    text = "开始朗读",
                    style = MiuixTheme.textStyles.button,
                    color = btnContent,
                )
            }
        } else {
            val smoothProgress by animateFloatAsState(
                targetValue = (playProgress ?: 0f).coerceIn(0f, 1f),
                animationSpec = tween(260, easing = FastOutSlowInEasing),
                label = "reading-progress",
            )
            Card(Modifier.fillMaxWidth(), insideMargin = PaddingValues(14.dp)) {
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
                        when (transport) {
                            Transport.BUFFERING -> InfiniteProgressIndicator(size = 18.dp, strokeWidth = 2.dp)
                            Transport.PAUSED -> Icon(pauseIcon, null, Modifier.size(17.dp), tint = colors.primary)
                            else -> Icon(soundWaveIcon, null, Modifier.size(18.dp), tint = colors.primary)
                        }
                    }
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        val title = when (transport) {
                            Transport.BUFFERING -> "正在合成音频…"
                            Transport.PAUSED -> "朗读已暂停"
                            else -> "正在朗读"
                        }
                        Text(title, style = MiuixTheme.textStyles.body1, fontWeight = FontWeight.Medium)
                        val sub = buildString {
                            append(playingVoiceLabel)
                            if (playSegment > 0) append(" · 第 $playSegment 段")
                        }
                        if (sub.isNotBlank()) Caption(sub, Modifier.fillMaxWidth())
                    }
                    if (playProgress != null && transport != Transport.BUFFERING) {
                        Text(
                            text = "${(smoothProgress * 100).roundToInt()}%",
                            style = MiuixTheme.textStyles.footnote1,
                            fontWeight = FontWeight.SemiBold,
                            color = colors.primary,
                        )
                    }
                }

                Spacer(Modifier.height(10.dp))
                LinearProgressIndicator(
                    progress = if (transport == Transport.BUFFERING) null else smoothProgress,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(12.dp))

                if (transport == Transport.BUFFERING) {
                    Button(
                        onClick = actions::stop,
                        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp),
                        cornerRadius = 12.dp,
                    ) {
                        Icon(stopIcon, null, Modifier.size(16.dp), tint = colors.onSurface)
                        Spacer(Modifier.width(8.dp))
                        Text("取消合成", style = MiuixTheme.textStyles.button)
                    }
                } else {
                    val paused = transport == Transport.PAUSED
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Button(
                            onClick = if (paused) actions::resume else actions::pause,
                            modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                            cornerRadius = 12.dp,
                            colors = ButtonDefaults.buttonColorsPrimary(),
                        ) {
                            Icon(
                                if (paused) playIcon else pauseIcon,
                                null,
                                Modifier.size(16.dp),
                                tint = colors.onPrimary,
                            )
                            Spacer(Modifier.width(8.dp))
                            Text(
                                if (paused) "继续朗读" else "暂停",
                                style = MiuixTheme.textStyles.button,
                                color = colors.onPrimary,
                            )
                        }
                        Button(
                            onClick = actions::stop,
                            modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                            cornerRadius = 12.dp,
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
