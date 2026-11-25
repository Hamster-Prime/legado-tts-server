package io.github.legadotts.client.ui

import androidx.compose.animation.*
import androidx.compose.animation.core.FastOutSlowInEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.semantics.selected
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import top.yukonga.miuix.kmp.basic.*
import top.yukonga.miuix.kmp.icon.MiuixIcons
import top.yukonga.miuix.kmp.icon.basic.Check
import top.yukonga.miuix.kmp.icon.basic.Close
import top.yukonga.miuix.kmp.icon.basic.Search
import top.yukonga.miuix.kmp.theme.MiuixTheme
import top.yukonga.miuix.kmp.utils.overScrollVertical

private enum class PreviewVisual { IDLE, BUFFERING, PLAYING }

@Composable
internal fun CatalogPage(
    voices: List<VoiceItem>,
    favorites: Set<Pair<String, String>>,
    selected: Pair<String, String>?,
    activeVoice: VoiceItem?,
    busy: Boolean,
    refreshing: Boolean,
    connecting: Boolean,
    connectionError: String,
    playError: String,
    playingPreview: Boolean,
    playingVoice: Pair<String, String>?,
    transport: Transport,
    actions: ReaderActions,
) {
    var query by rememberSaveable { mutableStateOf("") }
    var favoritesOnly by rememberSaveable { mutableStateOf(false) }
    var providerFilter by rememberSaveable { mutableStateOf<String?>(null) }
    val dark = isSystemInDarkTheme()
    val colors = MiuixTheme.colorScheme
    val scrollBehavior = MiuixScrollBehavior()

    val providerCounts = remember(voices, favorites, favoritesOnly) {
        voices.asSequence()
            .filter { !favoritesOnly || it.key in favorites }
            .groupingBy { it.provider }
            .eachCount()
    }

    val activeProvider = providerFilter?.takeIf { it in providerCounts }

    val groups = remember(voices, favorites, favoritesOnly, activeProvider, query) {
        val term = query.trim()
        voices.asSequence()
            .filter { !favoritesOnly || it.key in favorites }
            .filter { activeProvider == null || it.provider == activeProvider }
            .filter {
                term.isEmpty() || listOf(it.name, it.id, it.detail, it.provider, providerName(it.provider))
                    .any { field -> field.contains(term, ignoreCase = true) }
            }
            .groupBy { it.provider }
    }

    val favCount = remember(voices, favorites) {
        voices.count { it.key in favorites }
    }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "音色",
            subtitle = activeVoice?.let { "当前默认：${it.displayName} · ${providerName(it.provider)}" }
                ?: "${voices.size} 个服务端音色",
            scrollBehavior = scrollBehavior,
            actions = {
                IconButton(
                    onClick = actions::refresh,
                    enabled = !busy,
                    modifier = Modifier.padding(end = 8.dp),
                ) {
                    if (refreshing) {
                        InfiniteProgressIndicator(size = 18.dp, strokeWidth = 2.dp)
                    } else {
                        Icon(refreshIcon, "刷新音色目录", Modifier.size(20.dp), tint = colors.onSurface)
                    }
                }
            },
        )

        LazyColumn(
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth()
                .nestedScroll(scrollBehavior.nestedScrollConnection)
                .overScrollVertical(),
            contentPadding = PaddingValues(start = 14.dp, end = 14.dp, top = 6.dp, bottom = 20.dp),
        ) {
            item(key = "search") {
                TextField(
                    value = query,
                    onValueChange = { query = it },
                    modifier = Modifier.fillMaxWidth(),
                    singleLine = true,
                    label = "搜索名称、风格或音色 ID",
                    useLabelAsPlaceholder = true,
                    leadingIcon = {
                        Icon(
                            MiuixIcons.Basic.Search,
                            contentDescription = null,
                            modifier = Modifier.padding(start = 12.dp, end = 6.dp).size(18.dp),
                            tint = colors.onSurfaceVariantActions,
                        )
                    },
                    trailingIcon = if (query.isNotEmpty()) {
                        {
                            IconButton(onClick = { query = "" }, minWidth = 36.dp, minHeight = 36.dp) {
                                Icon(
                                    MiuixIcons.Basic.Close,
                                    contentDescription = "清除搜索",
                                    modifier = Modifier.size(16.dp),
                                    tint = colors.onSurfaceVariantActions,
                                )
                            }
                        }
                    } else null,
                )
                Spacer(Modifier.height(8.dp))
            }

            item(key = "tabs") {
                val favTab = if (favCount > 0) "我的收藏 ($favCount)" else "我的收藏"
                TabRow(
                    tabs = listOf("全部音色", favTab),
                    selectedTabIndex = if (favoritesOnly) 1 else 0,
                    onTabSelected = { favoritesOnly = it == 1 },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(8.dp))
            }

            if (providerCounts.size > 1) {
                item(key = "providers") {
                    LazyRow(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                    ) {
                        item(key = "all") {
                            FilterChip(
                                label = "全部",
                                selected = activeProvider == null,
                                onClick = { providerFilter = null },
                            )
                        }
                        items(providerCounts.entries.toList(), key = { it.key }) { (id, count) ->
                            FilterChip(
                                label = "${providerName(id)} $count",
                                selected = activeProvider == id,
                                onClick = { providerFilter = if (activeProvider == id) null else id },
                            )
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                }
            }

            if (playError.isNotBlank()) {
                item(key = "play-error") {
                    ErrorBanner(playError, onDismiss = actions::dismissPlayError)
                    Spacer(Modifier.height(10.dp))
                }
            }

            if (connectionError.isNotBlank() && voices.isEmpty()) {
                item(key = "conn-error") {
                    ErrorBanner(connectionError, onRetry = actions::refresh)
                    Spacer(Modifier.height(10.dp))
                }
            }

            if (groups.isEmpty()) {
                item(key = "empty") {
                    Card(Modifier.fillMaxWidth(), insideMargin = PaddingValues(24.dp)) {
                        Column(
                            Modifier.fillMaxWidth(),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            if (busy) {
                                InfiniteProgressIndicator()
                                Caption("正在获取服务端音色目录…")
                            } else {
                                Text(
                                    text = when {
                                        query.isNotBlank() -> "没有匹配的音色"
                                        favoritesOnly -> "还没有收藏音色"
                                        else -> "暂未获取到音色"
                                    },
                                    style = MiuixTheme.textStyles.body1,
                                    fontWeight = FontWeight.Medium,
                                )
                                Caption(
                                    when {
                                        query.isNotBlank() -> "试试更短的关键词，或切换服务商筛选"
                                        favoritesOnly -> "点按任意音色右侧的心形即可加入收藏"
                                        else -> "请确认服务端运行正常后点击重试"
                                    },
                                )
                                val actionLabel = when {
                                    query.isNotBlank() -> "清空搜索"
                                    favoritesOnly -> "浏览全部音色"
                                    else -> "重新加载"
                                }
                                TextButton(
                                    text = actionLabel,
                                    onClick = {
                                        when {
                                            query.isNotBlank() -> query = ""
                                            favoritesOnly -> favoritesOnly = false
                                            else -> actions.refresh()
                                        }
                                    },
                                )
                            }
                        }
                    }
                }
            }

            groups.forEach { (providerId, list) ->
                item(key = "header/$providerId") {
                    SmallTitle(
                        text = "${providerName(providerId)} · ${list.size}",
                        modifier = Modifier.animateItem(),
                        insideMargin = PaddingValues(start = 6.dp, top = 8.dp, bottom = 6.dp),
                    )
                }
                itemsIndexed(
                    items = list,
                    key = { _, v -> "voice/${providerId.length}/$providerId/${v.id}" },
                ) { index, voice ->
                    val isFirst = index == 0
                    val isLast = index == list.lastIndex
                    val isSelected = voice.key == selected
                    val isFav = voice.key in favorites
                    val isThisPreview = playingPreview && playingVoice == voice.key && transport != Transport.IDLE
                    val previewState = when {
                        !isThisPreview -> PreviewVisual.IDLE
                        transport == Transport.BUFFERING -> PreviewVisual.BUFFERING
                        else -> PreviewVisual.PLAYING
                    }
                    VoiceRow(
                        voice = voice,
                        chosen = isSelected,
                        favorite = isFav,
                        previewState = previewState,
                        enabled = !connecting,
                        first = isFirst,
                        last = isLast,
                        accent = providerAccent(providerId, dark),
                        actions = actions,
                        modifier = Modifier.animateItem(),
                    )
                    if (isLast) Spacer(Modifier.height(8.dp))
                }
            }
        }
    }
}

@Composable
private fun FilterChip(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val colors = MiuixTheme.colorScheme
    val bg by animateColorAsState(
        if (selected) colors.primary else colors.surfaceContainer,
        AppMotion.colorSpec(),
        label = "chip-bg",
    )
    val fg by animateColorAsState(
        if (selected) colors.onPrimary else colors.onSurface,
        AppMotion.colorSpec(),
        label = "chip-fg",
    )
    Box(
        modifier = modifier
            .clip(RoundedCornerShape(100))
            .background(bg)
            .clickable(onClick = onClick)
            .padding(horizontal = 11.dp, vertical = 6.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = label,
            style = MiuixTheme.textStyles.footnote1,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
            color = fg,
            maxLines = 1,
        )
    }
}

/**
 * High-FPS grouped voice row: avoids nested AnimatedContent/AnimatedVisibility subcompositions
 * inside LazyColumn and drives selection/favorite transitions in draw-phase (`drawBehind` / `graphicsLayer`).
 */
@Composable
private fun VoiceRow(
    voice: VoiceItem,
    chosen: Boolean,
    favorite: Boolean,
    previewState: PreviewVisual,
    enabled: Boolean,
    first: Boolean,
    last: Boolean,
    accent: Color,
    actions: ReaderActions,
    modifier: Modifier = Modifier,
) {
    val colors = MiuixTheme.colorScheme
    val shape = when {
        first && last -> RoundedCornerShape(16.dp)
        first -> RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp)
        last -> RoundedCornerShape(bottomStart = 16.dp, bottomEnd = 16.dp)
        else -> RectangleShape
    }

    // Just two lightweight float animations per row, evaluated in draw phase to skip relayout.
    val selectProgress = animateFloatAsState(
        targetValue = if (chosen) 1f else 0f,
        animationSpec = tween(AppMotion.FAST_MS, easing = FastOutSlowInEasing),
        label = "sel",
    )
    val favScale = animateFloatAsState(
        targetValue = if (favorite) 1.08f else 1f,
        animationSpec = tween(AppMotion.FAST_MS, easing = FastOutSlowInEasing),
        label = "fav",
    )

    val idleBg = colors.surfaceContainer
    val selectedBg = colors.tertiaryContainer.copy(alpha = 0.65f)
    val avatarIdle = accent.copy(alpha = 0.14f)
    val avatarSelected = colors.primary

    Box(
        modifier = modifier
            .fillMaxWidth()
            .clip(shape)
            .drawBehind {
                drawRect(lerp(idleBg, selectedBg, selectProgress.value))
            },
    ) {
        Column(Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { selected = chosen }
                    .clickable(enabled = enabled) { actions.select(voice) }
                    .padding(start = 12.dp, end = 6.dp, top = 9.dp, bottom = 9.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(34.dp)
                        .clip(CircleShape)
                        .drawBehind {
                            drawCircle(lerp(avatarIdle, avatarSelected, selectProgress.value))
                        },
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = voice.displayName.take(1),
                        style = MiuixTheme.textStyles.body2,
                        fontWeight = FontWeight.SemiBold,
                        color = accent,
                        modifier = Modifier.graphicsLayer {
                            val a = 1f - selectProgress.value
                            alpha = a
                            val s = 0.85f + 0.15f * a
                            scaleX = s
                            scaleY = s
                        },
                    )
                    Icon(
                        MiuixIcons.Basic.Check,
                        contentDescription = null,
                        modifier = Modifier
                            .size(18.dp)
                            .graphicsLayer {
                                val a = selectProgress.value
                                alpha = a
                                val s = 0.75f + 0.25f * a
                                scaleX = s
                                scaleY = s
                            },
                        tint = colors.onPrimary,
                    )
                }
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = voice.displayName,
                            style = MiuixTheme.textStyles.body1,
                            fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (chosen) colors.primary else colors.onSurface,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f, fill = false),
                        )
                        if (chosen) {
                            Box(
                                Modifier
                                    .clip(RoundedCornerShape(6.dp))
                                    .background(colors.primary.copy(alpha = 0.14f))
                                    .padding(horizontal = 6.dp, vertical = 2.dp),
                            ) {
                                Text(
                                    "使用中",
                                    style = MiuixTheme.textStyles.footnote2,
                                    fontWeight = FontWeight.Medium,
                                    color = colors.primary,
                                )
                            }
                        }
                    }
                    val subtitle = listOfNotNull(
                        voice.tag.takeIf { it.isNotBlank() },
                        voice.id.takeIf { it != voice.displayName },
                    ).joinToString(" · ")
                    if (subtitle.isNotBlank()) {
                        Text(
                            text = subtitle,
                            style = MiuixTheme.textStyles.footnote1,
                            color = colors.onSurfaceVariantSummary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
                IconButton(
                    onClick = { actions.favorite(voice) },
                    enabled = enabled,
                    minWidth = 38.dp,
                    minHeight = 38.dp,
                ) {
                    Icon(
                        if (favorite) heartFilledIcon else heartIcon,
                        contentDescription = (if (favorite) "取消收藏：" else "收藏：") + voice.displayName,
                        modifier = Modifier
                            .size(18.dp)
                            .graphicsLayer {
                                val s = favScale.value
                                scaleX = s
                                scaleY = s
                            },
                        tint = if (favorite) colors.primary else colors.onSurfaceVariantActions,
                    )
                }
                IconButton(
                    onClick = { actions.preview(voice) },
                    enabled = enabled,
                    minWidth = 38.dp,
                    minHeight = 38.dp,
                ) {
                    val activePreview = previewState != PreviewVisual.IDLE
                    Box(
                        modifier = Modifier
                            .size(30.dp)
                            .clip(CircleShape)
                            .background(if (activePreview) colors.primary else colors.secondaryContainer),
                        contentAlignment = Alignment.Center,
                    ) {
                        when (previewState) {
                            PreviewVisual.BUFFERING -> InfiniteProgressIndicator(
                                size = 15.dp,
                                strokeWidth = 1.8.dp,
                                color = colors.onPrimary,
                            )
                            PreviewVisual.PLAYING -> Icon(
                                stopIcon,
                                contentDescription = "停止试听：" + voice.displayName,
                                modifier = Modifier.size(13.dp),
                                tint = colors.onPrimary,
                            )
                            PreviewVisual.IDLE -> Icon(
                                playIcon,
                                contentDescription = "试听：" + voice.displayName,
                                modifier = Modifier.size(14.dp),
                                tint = colors.primary,
                            )
                        }
                    }
                }
            }
            if (!last) {
                HorizontalDivider(Modifier.padding(start = 56.dp, end = 14.dp))
            }
        }
    }
}
