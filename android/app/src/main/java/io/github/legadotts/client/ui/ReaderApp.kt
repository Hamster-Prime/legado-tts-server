package io.github.legadotts.client.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.*
import androidx.compose.animation.core.Animatable
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
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import androidx.compose.ui.zIndex
import kotlinx.coroutines.delay
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

/** Shared motion timings tuned for smooth 60/120 fps performance with minimal relayout. */
internal object AppMotion {
    const val FAST_MS = 160
    const val STANDARD_MS = 220
    fun <T> colorSpec() = tween<T>(durationMillis = FAST_MS, easing = FastOutSlowInEasing)
    fun sizeSpec() = spring<IntSize>(dampingRatio = 0.9f, stiffness = Spring.StiffnessMedium)
    fun slideSpec() = tween<IntOffset>(durationMillis = STANDARD_MS, easing = FastOutSlowInEasing)

    fun iconSwap(): ContentTransform =
        (fadeIn(tween(150, easing = FastOutSlowInEasing)) +
            scaleIn(initialScale = 0.85f, animationSpec = tween(150, easing = FastOutSlowInEasing)))
            .togetherWith(fadeOut(tween(100, easing = FastOutSlowInEasing)))

    /** Single-pass fade + subtle scale + SizeTransform avoids triple relayout fighting. */
    fun panelSwap(): ContentTransform = ContentTransform(
        targetContentEnter = fadeIn(tween(200, easing = FastOutSlowInEasing)) +
            scaleIn(initialScale = 0.98f, animationSpec = tween(200, easing = FastOutSlowInEasing)),
        initialContentExit = fadeOut(tween(120, easing = FastOutSlowInEasing)),
        sizeTransform = SizeTransform(clip = false) { _, _ -> sizeSpec() },
    )
}

private enum class RootScreen { LOADING, ONBOARDING, MAIN }

/** Subtle uniform UI scale so Miuix components feel crisp and well-proportioned on Pixel/standard displays. */
private const val UI_DENSITY_SCALE = 0.88f

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
    val navigate = remember(focus) {
        { target: Int ->
            if (target != tab) {
                focus.clearFocus()
                tab = target
            }
        }
    }
    val openCatalog = remember(navigate) { { navigate(1) } }
    val openSettings = remember(navigate) { { navigate(2) } }
    BackHandler(state.configured && tab != 0) { navigate(0) }

    val dark = isSystemInDarkTheme()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val showMessage = remember(scope, snackbarHostState) {
        { msg: String ->
            scope.launch {
                snackbarHostState.showSnackbar(msg, duration = SnackbarDuration.Short)
            }
            Unit
        }
    }

    LaunchedEffect(state.notice?.id) {
        val notice = state.notice ?: return@LaunchedEffect
        snackbarHostState.showSnackbar(notice.text, duration = SnackbarDuration.Short)
        actions.dismissNotice()
    }

    // Derived boolean so IME animation frames do not recompose the whole screen tree.
    val imeInsets = WindowInsets.ime
    val baseDensity = LocalDensity.current
    val compactDensity = remember(baseDensity) {
        Density(
            density = baseDensity.density * UI_DENSITY_SCALE,
            fontScale = baseDensity.fontScale,
        )
    }
    val keyboardVisible by remember(imeInsets, compactDensity) {
        derivedStateOf { imeInsets.getBottom(compactDensity) > 0 }
    }

    CompositionLocalProvider(LocalDensity provides compactDensity) {
        MiuixTheme(colors = remember(dark) { if (dark) darkColorScheme() else lightColorScheme() }) {
        val rootScreen = when {
            state.initializing -> RootScreen.LOADING
            !state.configured -> RootScreen.ONBOARDING
            else -> RootScreen.MAIN
        }
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
                        fadeIn(tween(180, easing = FastOutSlowInEasing)),
                    exit = slideOutVertically(animationSpec = tween(160, easing = FastOutSlowInEasing)) { it } +
                        fadeOut(tween(120, easing = FastOutSlowInEasing)),
                ) {
                    Column(Modifier.fillMaxWidth()) {
                        AnimatedVisibility(
                            visible = showMiniPlayer,
                            enter = fadeIn(tween(180, easing = FastOutSlowInEasing)) +
                                expandVertically(animationSpec = AppMotion.sizeSpec(), expandFrom = Alignment.Bottom),
                            exit = fadeOut(tween(140, easing = FastOutSlowInEasing)) +
                                shrinkVertically(animationSpec = AppMotion.sizeSpec(), shrinkTowards = Alignment.Bottom),
                        ) {
                            MiniPlayerBar(
                                transport = state.transport,
                                playingVoiceName = state.playingVoiceName,
                                playingPreview = state.playingPreview,
                                playProgress = state.playProgress,
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
                                    badge = if (index == 2 && state.connectionStatus == ConnectionStatus.ERROR) {
                                        {
                                            Box(
                                                Modifier.size(7.dp)
                                                    .clip(CircleShape)
                                                    .background(MiuixTheme.colorScheme.error)
                                            )
                                        }
                                    } else null,
                                )
                            }
                        }
                    }
                }
            },
        ) { innerPadding ->
            val providerCount = remember(state.voices) {
                state.voices.asSequence().map { it.provider }.distinct().count()
            }
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
                            fadeIn(tween(AppMotion.STANDARD_MS, easing = FastOutSlowInEasing))
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
                                    AppMark(size = 50)
                                    Spacer(Modifier.height(14.dp))
                                    InfiniteProgressIndicator()
                                }
                            }
                            RootScreen.ONBOARDING -> ConnectionPage(
                                configured = false,
                                connectionStatus = state.connectionStatus,
                                connectionError = state.connectionError,
                                formError = state.formError,
                                address = state.address,
                                hasKey = state.hasKey,
                                connecting = state.connecting,
                                refreshing = state.refreshing,
                                voiceCount = state.voices.size,
                                providerCount = providerCount,
                                activeVoice = state.activeVoice,
                                speed = state.speed,
                                actions = actions,
                                openCatalog = openCatalog,
                            )
                            RootScreen.MAIN -> KeepAliveTabHost(
                                currentTab = tab,
                                tabCount = 3,
                                modifier = Modifier.fillMaxSize(),
                            ) { slot ->
                                when (slot) {
                                    0 -> ReadingPage(
                                        activeVoice = state.activeVoice,
                                        text = state.text,
                                        speed = state.speed,
                                        connectionStatus = state.connectionStatus,
                                        connecting = state.connecting,
                                        transport = state.transport,
                                        playingPreview = state.playingPreview,
                                        playingVoiceName = state.playingVoiceName,
                                        playProgress = state.playProgress,
                                        playSegment = state.playSegment,
                                        playError = state.playError,
                                        actions = actions,
                                        openCatalog = openCatalog,
                                        openSettings = openSettings,
                                        showMessage = showMessage,
                                    )
                                    1 -> CatalogPage(
                                        voices = state.voices,
                                        favorites = state.favorites,
                                        selected = state.selected,
                                        activeVoice = state.activeVoice,
                                        busy = state.busy,
                                        refreshing = state.refreshing,
                                        connecting = state.connecting,
                                        connectionError = state.connectionError,
                                        playError = state.playError,
                                        playingPreview = state.playingPreview,
                                        playingVoice = state.playingVoice,
                                        transport = state.transport,
                                        actions = actions,
                                    )
                                    else -> ConnectionPage(
                                        configured = true,
                                        connectionStatus = state.connectionStatus,
                                        connectionError = state.connectionError,
                                        formError = state.formError,
                                        address = state.address,
                                        hasKey = state.hasKey,
                                        connecting = state.connecting,
                                        refreshing = state.refreshing,
                                        voiceCount = state.voices.size,
                                        providerCount = providerCount,
                                        activeVoice = state.activeVoice,
                                        speed = state.speed,
                                        actions = actions,
                                        openCatalog = openCatalog,
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
}

/**
 * Keeps visited tabs alive in composition (preserving LazyColumn/ScrollState) and animates
 * tab switches exclusively inside GPU `graphicsLayer` with zero recomposition or relayout per frame.
 */
@Composable
private fun KeepAliveTabHost(
    currentTab: Int,
    tabCount: Int,
    modifier: Modifier = Modifier,
    content: @Composable (tab: Int) -> Unit,
) {
    var activeTab by remember { mutableIntStateOf(currentTab) }
    var previousTab by remember { mutableIntStateOf(currentTab) }
    val visited = remember { mutableStateListOf<Boolean>().apply { repeat(tabCount) { add(it == currentTab) } } }
    if (!visited[currentTab]) visited[currentTab] = true

    // Warm sibling tabs quietly after the first frame settles so even the very first tap is jank-free.
    LaunchedEffect(Unit) {
        delay(220)
        for (i in 0 until tabCount) {
            if (!visited[i]) {
                visited[i] = true
                delay(40)
            }
        }
    }

    val progress = remember { Animatable(1f) }
    LaunchedEffect(currentTab) {
        if (currentTab != activeTab) {
            previousTab = activeTab
            activeTab = currentTab
            progress.snapTo(0f)
            progress.animateTo(
                targetValue = 1f,
                animationSpec = tween(durationMillis = AppMotion.STANDARD_MS, easing = FastOutSlowInEasing),
            )
        }
    }

    val isTransitioning by remember { derivedStateOf { progress.value < 0.999f } }
    val forward = activeTab > previousTab

    Layout(
        modifier = modifier,
        content = {
            for (index in 0 until tabCount) {
                key(index) {
                    val isActive = index == activeTab
                    val isOutgoing = isTransitioning && index == previousTab
                    Box(
                        modifier = Modifier
                            .fillMaxSize()
                            .zIndex(if (isActive) 1f else 0f)
                            .graphicsLayer {
                                val p = progress.value
                                when {
                                    isActive && p < 0.999f -> {
                                        val delta = (size.width * 0.06f).coerceAtLeast(20f)
                                        translationX = (if (forward) delta else -delta) * (1f - p)
                                        alpha = p
                                    }
                                    isOutgoing -> {
                                        val delta = (size.width * 0.04f).coerceAtLeast(14f)
                                        translationX = (if (forward) -delta else delta) * p
                                        alpha = 1f - p
                                    }
                                    isActive -> {
                                        translationX = 0f
                                        alpha = 1f
                                    }
                                    else -> {
                                        alpha = 0f
                                    }
                                }
                            },
                    ) {
                        if (visited[index]) content(index)
                    }
                }
            }
        },
    ) { measurables, constraints ->
        val zero = Constraints.fixed(0, 0)
        val placeables = measurables.mapIndexed { index, measurable ->
            val shouldPlace = index == activeTab || (isTransitioning && index == previousTab)
            val measured = measurable.measure(if (visited[index]) constraints else zero)
            if (shouldPlace) measured else null
        }
        layout(constraints.maxWidth, constraints.maxHeight) {
            for (p in placeables) {
                p?.placeRelative(0, 0)
            }
        }
    }
}

@Composable
private fun MiniPlayerBar(
    transport: Transport,
    playingVoiceName: String,
    playingPreview: Boolean,
    playProgress: Float?,
    actions: ReaderActions,
    onOpenReader: () -> Unit,
) {
    val colors = MiuixTheme.colorScheme
    val dark = isSystemInDarkTheme()
    val animatedProgress by animateFloatAsState(
        targetValue = (playProgress ?: 0f).coerceIn(0f, 1f),
        animationSpec = tween(260, easing = FastOutSlowInEasing),
        label = "mini-progress",
    )
    Surface(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 5.dp),
        shape = RoundedCornerShape(14.dp),
        color = if (dark) colors.surfaceContainerHighest else colors.surfaceContainer,
        border = BorderStroke(0.5.dp, colors.dividerLine),
    ) {
        Column(Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(enabled = !playingPreview, onClick = onOpenReader)
                    .padding(start = 11.dp, end = 6.dp, top = 7.dp, bottom = 7.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    Modifier
                        .size(32.dp)
                        .clip(CircleShape)
                        .background(colors.primary.copy(alpha = 0.14f)),
                    contentAlignment = Alignment.Center,
                ) {
                    when (transport) {
                        Transport.BUFFERING -> InfiniteProgressIndicator(size = 16.dp, strokeWidth = 2.dp)
                        Transport.PAUSED -> Icon(pauseIcon, null, Modifier.size(15.dp), tint = colors.primary)
                        else -> Icon(soundWaveIcon, null, Modifier.size(16.dp), tint = colors.primary)
                    }
                }
                Spacer(Modifier.width(9.dp))
                Column(Modifier.weight(1f)) {
                    val label = when (transport) {
                        Transport.BUFFERING -> if (playingPreview) "正在准备试听" else "正在合成"
                        Transport.PAUSED -> "已暂停"
                        else -> if (playingPreview) "正在试听" else "正在朗读"
                    }
                    Text(
                        text = playingVoiceName.substringBefore(" - ").ifBlank { "语音播放" },
                        style = MiuixTheme.textStyles.body2,
                        fontWeight = FontWeight.Medium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(
                        text = label,
                        style = MiuixTheme.textStyles.footnote2,
                        color = colors.onSurfaceVariantSummary,
                        maxLines = 1,
                    )
                }
                if (!playingPreview && transport != Transport.BUFFERING) {
                    val paused = transport == Transport.PAUSED
                    IconButton(
                        onClick = if (paused) actions::resume else actions::pause,
                        minWidth = 38.dp, minHeight = 38.dp,
                    ) {
                        Icon(
                            if (paused) playIcon else pauseIcon,
                            contentDescription = if (paused) "继续朗读" else "暂停朗读",
                            modifier = Modifier.size(17.dp),
                            tint = colors.primary,
                        )
                    }
                }
                IconButton(onClick = actions::stop, minWidth = 38.dp, minHeight = 38.dp) {
                    Icon(
                        stopIcon,
                        contentDescription = "停止播放",
                        modifier = Modifier.size(15.dp),
                        tint = colors.onSurfaceVariantActions,
                    )
                }
            }
            if (!playingPreview && (playProgress != null || transport == Transport.BUFFERING)) {
                LinearProgressIndicator(
                    progress = if (transport == Transport.BUFFERING) null else animatedProgress,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                    height = 2.5.dp,
                )
                Spacer(Modifier.height(4.dp))
            }
        }
    }
}

@Composable
internal fun AppMark(size: Int = 48) {
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

    Row(
        modifier = modifier
            .clip(RoundedCornerShape(100))
            .background(bg)
            .padding(horizontal = 10.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (loading) {
            InfiniteProgressIndicator(size = 12.dp, strokeWidth = 1.6.dp, color = fg)
        } else if (dotColor != null) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(dotColor))
        }
        Text(
            text = text,
            style = MiuixTheme.textStyles.footnote1,
            fontWeight = FontWeight.Medium,
            color = fg,
            maxLines = 1,
        )
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
        enter = fadeIn(tween(180, easing = FastOutSlowInEasing)) +
            expandVertically(animationSpec = AppMotion.sizeSpec()),
        exit = fadeOut(tween(130, easing = FastOutSlowInEasing)) +
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
