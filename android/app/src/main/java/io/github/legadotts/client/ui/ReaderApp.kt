package io.github.legadotts.client.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import top.yukonga.miuix.kmp.basic.*
import top.yukonga.miuix.kmp.theme.*
import java.util.Locale

private val tabs = listOf("朗读", "音色", "设置")
private val navIcons = listOf(
    glyph("Reader", "M3,4.5 Q7.5,3 12,6 Q16.5,3 21,4.5 L21,18.5 Q16.5,17 12,20 Q7.5,17 3,18.5 Z M12,6 L12,20"),
    glyph("Voices", "M3,10 V14 M7.5,6 V18 M12,3 V21 M16.5,7 V17 M21,10 V14"),
    glyph("Settings", "M4,7 H9 M14,7 H20 M4,17 H10 M15,17 H20 M9,4.5 V9.5 M10,14.5 V19.5"),
)

internal val playIcon = glyph("Play", "M7,4.5 L19.5,12 L7,19.5 Z", filled = true)
internal val pauseIcon = glyph("Pause", "M6.5,5 H10 V19 H6.5 Z M14,5 H17.5 V19 H14 Z", filled = true)
internal val stopIcon = glyph("Stop", "M6,6 H18 V18 H6 Z", filled = true)
internal val heartIcon = glyph("Favorite", "M12,20 C7.5,16.8 3,13 3,8.5 C3,4.2 8.2,2.8 12,6.8 C15.8,2.8 21,4.2 21,8.5 C21,13 16.5,16.8 12,20 Z")
internal val heartFilledIcon = glyph("Favorited", "M12,20 C7.5,16.8 3,13 3,8.5 C3,4.2 8.2,2.8 12,6.8 C15.8,2.8 21,4.2 21,8.5 C21,13 16.5,16.8 12,20 Z", filled = true)
internal val refreshIcon = glyph("Refresh", "M20,12 A8,8 0 1,1 17.6,6.3 M20,4 V8.5 H15.5")
internal val clearIcon = glyph("Clear", "M6,6 L18,18 M18,6 L6,18")
internal val soundWaveIcon = glyph("SoundWave", "M4,10 V14 M8,7 V17 M12,4 V20 M16,7 V17 M20,10 V14")
internal val eyeIcon = glyph("Show", "M2,12 C5,7 9,5 12,5 C15,5 19,7 22,12 C19,17 15,19 12,19 C9,19 5,17 2,12 Z M12,9 A3,3 0 1,0 12,15 A3,3 0 1,0 12,9")
internal val eyeOffIcon = glyph("Hide", "M3,3 L21,21 M10.5,5.2 C11,5.1 11.5,5.1 12,5 C15,5 19,7 22,12 C21,13.7 19.7,15.2 18.2,16.3 M6.2,7.4 C4.5,8.6 3.1,10.2 2,12 C5,17 9,19 12,19 C13.5,19 15,18.6 16.4,17.8")
internal val serverIcon = glyph("Server", "M4,5 H20 V10 H4 Z M4,14 H20 V19 H4 Z M7.5,7.5 H7.6 M7.5,16.5 H7.6")

/** Shared motion timings for consistent HyperOS / Miuix feel across all screens. */
internal object AppMotion {
    const val FAST_MS = 180
    const val STANDARD_MS = 260
    fun <T> colorSpec() = tween<T>(durationMillis = FAST_MS, easing = FastOutSlowInEasing)
    fun sizeSpec() = spring<IntSize>(dampingRatio = 0.88f, stiffness = Spring.StiffnessMediumLow)
    fun slideSpec() = tween<IntOffset>(durationMillis = STANDARD_MS, easing = FastOutSlowInEasing)
    fun popSpec() = spring<Float>(dampingRatio = Spring.DampingRatioMediumBouncy, stiffness = Spring.StiffnessMedium)

    fun iconSwap(): ContentTransform =
        (fadeIn(tween(170, easing = FastOutSlowInEasing)) +
            scaleIn(initialScale = 0.82f, animationSpec = tween(180, easing = FastOutSlowInEasing)))
            .togetherWith(
                fadeOut(tween(120, easing = FastOutSlowInEasing)) +
                    scaleOut(targetScale = 0.82f, animationSpec = tween(120, easing = FastOutSlowInEasing))
            )

    fun panelSwap(): ContentTransform = ContentTransform(
        targetContentEnter = fadeIn(tween(230, easing = FastOutSlowInEasing)) +
            expandVertically(animationSpec = sizeSpec(), expandFrom = Alignment.Top),
        initialContentExit = fadeOut(tween(160, easing = FastOutSlowInEasing)) +
            shrinkVertically(animationSpec = sizeSpec(), shrinkTowards = Alignment.Top),
        sizeTransform = SizeTransform(clip = false) { _, _ -> sizeSpec() },
    )

    fun textFade(): ContentTransform =
        fadeIn(tween(180, easing = FastOutSlowInEasing))
            .togetherWith(fadeOut(tween(120, easing = FastOutSlowInEasing)))
}

private enum class RootScreen { LOADING, ONBOARDING, MAIN }

private fun glyph(name: String, path: String, filled: Boolean = false): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f).apply {
        addPath(
            pathData = PathParser().parsePathString(path).toNodes(),
            stroke = SolidColor(Color.Black),
            strokeLineWidth = if (filled) 1.2f else 1.9f,
            strokeLineCap = StrokeCap.Round,
            strokeLineJoin = StrokeJoin.Round,
            fill = if (filled) SolidColor(Color.Black) else null,
        )
    }.build()

@Composable
fun ReaderApp(state: ReaderState, actions: ReaderActions) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val focus = LocalFocusManager.current
    val navigate: (Int) -> Unit = { target ->
        if (target != tab) {
            focus.clearFocus()
            tab = target
        }
    }
    BackHandler(state.configured && tab != 0) { navigate(0) }

    val dark = isSystemInDarkTheme()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val showMessage: (String) -> Unit = { msg ->
        scope.launch {
            snackbarHostState.showSnackbar(msg, duration = SnackbarDuration.Short)
        }
    }

    LaunchedEffect(state.notice?.id) {
        val notice = state.notice ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(notice.text, duration = SnackbarDuration.Short)
        actions.dismissNotice()
    }

    MiuixTheme(colors = remember(dark) { if (dark) darkColorScheme() else lightColorScheme() }) {
        val rootScreen = when {
            state.initializing -> RootScreen.LOADING
            !state.configured -> RootScreen.ONBOARDING
            else -> RootScreen.MAIN
        }
        val keyboardVisible = WindowInsets.ime.getBottom(LocalDensity.current) > 0
        val showMiniPlayer = rootScreen == RootScreen.MAIN && state.transport != Transport.IDLE &&
            (tab != 0 || state.playingPreview)

        Scaffold(
            modifier = Modifier.fillMaxSize(),
            containerColor = MiuixTheme.colorScheme.surface,
            contentWindowInsets = WindowInsets(0),
            snackbarHost = { SnackbarHost(snackbarHostState) },
            bottomBar = {
                AnimatedVisibility(
                    visible = rootScreen == RootScreen.MAIN && !keyboardVisible,
                    enter = slideInVertically(animationSpec = AppMotion.slideSpec()) { it } +
                        fadeIn(tween(200, easing = FastOutSlowInEasing)),
                    exit = slideOutVertically(animationSpec = tween(180, easing = FastOutSlowInEasing)) { it } +
                        fadeOut(tween(140, easing = FastOutSlowInEasing)),
                ) {
                    Column(Modifier.fillMaxWidth()) {
                        AnimatedVisibility(
                            visible = showMiniPlayer,
                            enter = fadeIn(tween(220, easing = FastOutSlowInEasing)) +
                                expandVertically(animationSpec = AppMotion.sizeSpec(), expandFrom = Alignment.Bottom),
                            exit = fadeOut(tween(160, easing = FastOutSlowInEasing)) +
                                shrinkVertically(animationSpec = AppMotion.sizeSpec(), shrinkTowards = Alignment.Bottom),
                        ) {
                            MiniPlayerBar(
                                state = state,
                                actions = actions,
                                onOpenReader = { if (!state.playingPreview) navigate(0) },
                            )
                        }
                        NavigationBar(defaultWindowInsetsPadding = true) {
                            tabs.forEachIndexed { index, label ->
                                NavigationBarItem(
                                    selected = tab == index,
                                    onClick = { navigate(index) },
                                    icon = navIcons[index],
                                    label = label,
                                    badge = if (index == 2) {
                                        {
                                            AnimatedVisibility(
                                                visible = state.connectionStatus == ConnectionStatus.ERROR,
                                                enter = fadeIn(tween(180)) + scaleIn(initialScale = 0.4f),
                                                exit = fadeOut(tween(140)) + scaleOut(targetScale = 0.4f),
                                            ) {
                                                Box(
                                                    Modifier.size(7.dp)
                                                        .clip(CircleShape)
                                                        .background(MiuixTheme.colorScheme.error)
                                                )
                                            }
                                        }
                                    } else null,
                                )
                            }
                        }
                    }
                }
            },
        ) { innerPadding ->
            Box(
                Modifier.fillMaxSize()
                    .padding(bottom = innerPadding.calculateBottomPadding())
                    .then(if (keyboardVisible) Modifier.imePadding() else Modifier),
                contentAlignment = Alignment.TopCenter,
            ) {
                Box(Modifier.widthIn(max = 760.dp).fillMaxSize()) {
                    AnimatedContent(
                        targetState = rootScreen,
                        transitionSpec = {
                            (fadeIn(tween(AppMotion.STANDARD_MS, easing = FastOutSlowInEasing)) +
                                scaleIn(initialScale = 0.97f, animationSpec = tween(AppMotion.STANDARD_MS, easing = FastOutSlowInEasing)))
                                .togetherWith(fadeOut(tween(AppMotion.FAST_MS, easing = FastOutSlowInEasing)))
                        },
                        label = "root-screen",
                    ) { screen ->
                        when (screen) {
                            RootScreen.LOADING -> Box(
                                Modifier.fillMaxSize(),
                                contentAlignment = Alignment.Center,
                            ) {
                                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                    AppMark(size = 56)
                                    Spacer(Modifier.height(16.dp))
                                    InfiniteProgressIndicator()
                                }
                            }
                            RootScreen.ONBOARDING -> ConnectionPage(
                                state = state,
                                actions = actions,
                                openCatalog = { navigate(1) },
                            )
                            RootScreen.MAIN -> AnimatedContent(
                                targetState = tab,
                                transitionSpec = {
                                    val forward = targetState > initialState
                                    val enterOffset: (Int) -> Int = { width ->
                                        val delta = (width * 0.08f).toInt().coerceAtLeast(24)
                                        if (forward) delta else -delta
                                    }
                                    val exitOffset: (Int) -> Int = { width ->
                                        val delta = (width * 0.05f).toInt().coerceAtLeast(16)
                                        if (forward) -delta else delta
                                    }
                                    (slideInHorizontally(animationSpec = AppMotion.slideSpec(), initialOffsetX = enterOffset) +
                                        fadeIn(tween(240, easing = FastOutSlowInEasing)))
                                        .togetherWith(
                                            slideOutHorizontally(animationSpec = tween(200, easing = FastOutSlowInEasing), targetOffsetX = exitOffset) +
                                                fadeOut(tween(150, easing = FastOutSlowInEasing))
                                        )
                                },
                                label = "bottom-tab",
                            ) { currentTab ->
                                when (currentTab) {
                                    0 -> ReadingPage(
                                        state = state,
                                        actions = actions,
                                        openCatalog = { navigate(1) },
                                        openSettings = { navigate(2) },
                                        showMessage = showMessage,
                                    )
                                    1 -> CatalogPage(state = state, actions = actions)
                                    else -> ConnectionPage(
                                        state = state,
                                        actions = actions,
                                        openCatalog = { navigate(1) },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun MiniPlayerBar(
    state: ReaderState,
    actions: ReaderActions,
    onOpenReader: () -> Unit,
) {
    val colors = MiuixTheme.colorScheme
    val dark = isSystemInDarkTheme()
    val animatedProgress by animateFloatAsState(
        targetValue = (state.playProgress ?: 0f).coerceIn(0f, 1f),
        animationSpec = tween(320, easing = FastOutSlowInEasing),
        label = "mini-progress",
    )
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp)
            .animateContentSize(AppMotion.sizeSpec()),
        shape = RoundedCornerShape(16.dp),
        color = if (dark) colors.surfaceContainerHighest else colors.surfaceContainer,
        border = BorderStroke(0.5.dp, colors.dividerLine),
    ) {
        Column(Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(enabled = !state.playingPreview, onClick = onOpenReader)
                    .padding(start = 12.dp, end = 6.dp, top = 8.dp, bottom = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    Modifier
                        .size(34.dp)
                        .clip(CircleShape)
                        .background(colors.primary.copy(alpha = 0.14f)),
                    contentAlignment = Alignment.Center,
                ) {
                    AnimatedContent(
                        targetState = state.transport,
                        transitionSpec = { AppMotion.iconSwap() },
                        label = "mini-icon",
                    ) { tr ->
                        when (tr) {
                            Transport.BUFFERING -> InfiniteProgressIndicator(size = 18.dp, strokeWidth = 2.dp)
                            Transport.PAUSED -> Icon(pauseIcon, null, Modifier.size(16.dp), tint = colors.primary)
                            else -> Icon(soundWaveIcon, null, Modifier.size(18.dp), tint = colors.primary)
                        }
                    }
                }
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    val label = when (state.transport) {
                        Transport.BUFFERING -> if (state.playingPreview) "正在准备试听" else "正在合成"
                        Transport.PAUSED -> "已暂停"
                        else -> if (state.playingPreview) "正在试听" else "正在朗读"
                    }
                    Text(
                        text = state.playingVoiceName.substringBefore(" - ").ifBlank { "语音播放" },
                        style = MiuixTheme.textStyles.body2,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    AnimatedContent(
                        targetState = label,
                        transitionSpec = { AppMotion.textFade() },
                        label = "mini-status",
                    ) { statusText ->
                        Text(
                            text = statusText,
                            style = MiuixTheme.textStyles.footnote2,
                            color = colors.onSurfaceVariantSummary,
                            maxLines = 1,
                        )
                    }
                }
                AnimatedVisibility(
                    visible = !state.playingPreview && state.transport != Transport.BUFFERING,
                    enter = fadeIn(tween(180)) + scaleIn(initialScale = 0.8f),
                    exit = fadeOut(tween(130)) + scaleOut(targetScale = 0.8f),
                ) {
                    val paused = state.transport == Transport.PAUSED
                    IconButton(
                        onClick = if (paused) actions::resume else actions::pause,
                        minWidth = 40.dp, minHeight = 40.dp,
                    ) {
                        AnimatedContent(
                            targetState = paused,
                            transitionSpec = { AppMotion.iconSwap() },
                            label = "mini-play-pause",
                        ) { isPaused ->
                            Icon(
                                if (isPaused) playIcon else pauseIcon,
                                contentDescription = if (isPaused) "继续朗读" else "暂停朗读",
                                modifier = Modifier.size(18.dp),
                                tint = colors.primary,
                            )
                        }
                    }
                }
                IconButton(onClick = actions::stop, minWidth = 40.dp, minHeight = 40.dp) {
                    Icon(
                        stopIcon,
                        contentDescription = "停止播放",
                        modifier = Modifier.size(16.dp),
                        tint = colors.onSurfaceVariantActions,
                    )
                }
            }
            AnimatedVisibility(
                visible = !state.playingPreview && (state.playProgress != null || state.transport == Transport.BUFFERING),
                enter = fadeIn(tween(180)) + expandVertically(),
                exit = fadeOut(tween(140)) + shrinkVertically(),
            ) {
                Column(Modifier.fillMaxWidth()) {
                    LinearProgressIndicator(
                        progress = if (state.transport == Transport.BUFFERING) null else animatedProgress,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                        height = 2.5.dp,
                    )
                    Spacer(Modifier.height(4.dp))
                }
            }
        }
    }
}

@Composable
internal fun AppMark(size: Int = 52) {
    Box(
        modifier = Modifier
            .size(size.dp)
            .clip(RoundedCornerShape((size * 0.28f).dp))
            .background(MiuixTheme.colorScheme.primary),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            soundWaveIcon,
            contentDescription = null,
            modifier = Modifier.size((size * 0.54f).dp),
            tint = MiuixTheme.colorScheme.onPrimary,
        )
    }
}

@Composable
internal fun StatusPill(
    text: String,
    containerColor: Color,
    contentColor: Color,
    modifier: Modifier = Modifier,
    dotColor: Color? = null,
    loading: Boolean = false,
) {
    val bg by animateColorAsState(containerColor, AppMotion.colorSpec(), label = "pill-bg")
    val fg by animateColorAsState(contentColor, AppMotion.colorSpec(), label = "pill-fg")
    val dot by animateColorAsState(dotColor ?: Color.Transparent, AppMotion.colorSpec(), label = "pill-dot")

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(100))
            .background(bg)
            .animateContentSize(AppMotion.sizeSpec())
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        AnimatedContent(
            targetState = loading to (dotColor != null),
            transitionSpec = { AppMotion.iconSwap() },
            label = "pill-indicator",
        ) { (isLoading, hasDot) ->
            when {
                isLoading -> InfiniteProgressIndicator(size = 12.dp, strokeWidth = 1.6.dp, color = fg)
                hasDot -> Box(Modifier.size(7.dp).clip(CircleShape).background(dot))
            }
        }
        AnimatedContent(
            targetState = text,
            transitionSpec = { AppMotion.textFade() },
            label = "pill-text",
        ) { label ->
            Text(
                text = label,
                style = MiuixTheme.textStyles.footnote1,
                fontWeight = FontWeight.Medium,
                color = fg,
                maxLines = 1,
            )
        }
    }
}

@Composable
internal fun ErrorBanner(
    message: String,
    modifier: Modifier = Modifier,
    onRetry: (() -> Unit)? = null,
    onDismiss: (() -> Unit)? = null,
) {
    var lastMessage by remember { mutableStateOf(message) }
    if (message.isNotBlank()) lastMessage = message
    AnimatedVisibility(
        visible = message.isNotBlank(),
        modifier = modifier,
        enter = fadeIn(tween(200, easing = FastOutSlowInEasing)) +
            expandVertically(animationSpec = AppMotion.sizeSpec()),
        exit = fadeOut(tween(150, easing = FastOutSlowInEasing)) +
            shrinkVertically(animationSpec = AppMotion.sizeSpec()),
    ) {
        val colors = MiuixTheme.colorScheme
        Surface(
            modifier = Modifier.fillMaxWidth().semantics { liveRegion = LiveRegionMode.Polite },
            shape = RoundedCornerShape(14.dp),
            color = colors.errorContainer,
        ) {
            Row(
                modifier = Modifier.fillMaxWidth().padding(start = 14.dp, end = 8.dp, top = 10.dp, bottom = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Text(
                    text = lastMessage,
                    modifier = Modifier.weight(1f),
                    style = MiuixTheme.textStyles.footnote1,
                    color = colors.onErrorContainer,
                )
                if (onRetry != null) {
                    Text(
                        text = "重试",
                        style = MiuixTheme.textStyles.footnote1,
                        fontWeight = FontWeight.SemiBold,
                        color = colors.error,
                        modifier = Modifier
                            .clip(RoundedCornerShape(8.dp))
                            .clickable(onClick = onRetry)
                            .padding(horizontal = 8.dp, vertical = 6.dp),
                    )
                }
                if (onDismiss != null) {
                    IconButton(onClick = onDismiss, minWidth = 34.dp, minHeight = 34.dp) {
                        Icon(clearIcon, "关闭提示", Modifier.size(14.dp), tint = colors.onErrorContainer)
                    }
                }
            }
        }
    }
}

@Composable
internal fun Caption(message: String, modifier: Modifier = Modifier) {
    Text(
        message, modifier,
        style = MiuixTheme.textStyles.footnote1,
        color = MiuixTheme.colorScheme.onSurfaceVariantSummary,
    )
}

internal fun formatSpeed(speed: Float): String = String.format(Locale.ROOT, "%.1f", speed)

internal fun providerName(id: String): String = when (id) {
    "edge" -> "微软 Edge"
    "doubao" -> "火山引擎"
    "tencent" -> "腾讯云"
    "xiaomi" -> "小米 MiMo"
    "fishaudio" -> "Fish Audio"
    else -> id
}

internal fun providerAccent(id: String, dark: Boolean): Color = when (id) {
    "edge" -> if (dark) Color(0xFF4DA3FF) else Color(0xFF1976D2)
    "doubao" -> if (dark) Color(0xFFFF8A50) else Color(0xFFE65100)
    "tencent" -> if (dark) Color(0xFF4CD4A9) else Color(0xFF00897B)
    "xiaomi" -> if (dark) Color(0xFFFFA726) else Color(0xFFEF6C00)
    "fishaudio" -> if (dark) Color(0xFFB388FF) else Color(0xFF6A48D7)
    else -> if (dark) Color(0xFF7FA8FF) else Color(0xFF3A6FE2)
}

internal fun connectedGreen(dark: Boolean): Color =
    if (dark) Color(0xFF34D399) else Color(0xFF0F9D58)
