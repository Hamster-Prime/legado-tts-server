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
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.graphics.graphicsLayer
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
internal fun CatalogPage(state: ReaderState, actions: ReaderActions) {
    var query by rememberSaveable { mutableStateOf("") }
    var favoritesOnly by rememberSaveable { mutableStateOf(false) }
    var providerFilter by rememberSaveable { mutableStateOf<String?>(null) }
    val dark = isSystemInDarkTheme()
    val colors = MiuixTheme.colorScheme
    val scrollBehavior = MiuixScrollBehavior()

    val providerCounts = remember(state.voices, state.favorites, favoritesOnly) {
        state.voices.asSequence()
            .filter { !favoritesOnly || it.key in state.favorites }
            .groupingBy { it.provider }
            .eachCount()
    }

    // Clear stale provider filter if switching to favorites where that provider has no items.
    val activeProvider = providerFilter?.takeIf { it in providerCounts }

    val groups = remember(state.voices, state.favorites, favoritesOnly, activeProvider, query) {
        val term = query.trim()
        state.voices.asSequence()
            .filter { !favoritesOnly || it.key in state.favorites }
            .filter { activeProvider == null || it.provider == activeProvider }
            .filter {
                term.isEmpty() || listOf(it.name, it.id, it.detail, it.provider, providerName(it.provider))
                    .any { field -> field.contains(term, ignoreCase = true) }
            }
            .groupBy { it.provider }
    }

    val favCount = remember(state.voices, state.favorites) {
        state.voices.count { it.key in state.favorites }
    }

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = "音色",
            subtitle = state.activeVoice?.let { "当前默认：${it.displayName} · ${providerName(it.provider)}" }
                ?: "${state.voices.size} 个服务端音色",
            scrollBehavior = scrollBehavior,
            actions = {
                IconButton(
                    onClick = actions::refresh,
                    enabled = !state.busy,
                    modifier = Modifier.padding(end = 8.dp),
                ) {
                    AnimatedContent(
                        targetState = state.refreshing,
                        transitionSpec = { AppMotion.iconSwap() },
                        label = "catalog-refresh",
                    ) { refreshing ->
                        if (refreshing) {
                            InfiniteProgressIndicator(size = 18.dp, strokeWidth = 2.dp)
                        } else {
                            Icon(refreshIcon, "刷新音色目录", Modifier.size(20.dp), tint = colors.onSurface)
                        }
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
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 24.dp),
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
                    trailingIcon = {
                        AnimatedVisibility(
                            visible = query.isNotEmpty(),
                            enter = fadeIn(tween(150)) + scaleIn(initialScale = 0.75f),
                            exit = fadeOut(tween(120)) + scaleOut(targetScale = 0.75f),
                        ) {
                            IconButton(onClick = { query = "" }, minWidth = 36.dp, minHeight = 36.dp) {
                                Icon(
                                    MiuixIcons.Basic.Close,
                                    contentDescription = "清除搜索",
                                    modifier = Modifier.size(16.dp),
                                    tint = colors.onSurfaceVariantActions,
                                )
                            }
                        }
                    },
                )
                Spacer(Modifier.height(10.dp))
            }

            item(key = "tabs") {
                val favTab = if (favCount > 0) "我的收藏 ($favCount)" else "我的收藏"
                TabRow(
                    tabs = listOf("全部音色", favTab),
                    selectedTabIndex = if (favoritesOnly) 1 else 0,
                    onTabSelected = { favoritesOnly = it == 1 },
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
            }

            item(key = "providers") {
                AnimatedVisibility(
                    visible = providerCounts.size > 1,
                    enter = fadeIn(tween(200)) + expandVertically(AppMotion.sizeSpec()),
                    exit = fadeOut(tween(140)) + shrinkVertically(AppMotion.sizeSpec()),
                ) {
                    Column {
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
                                    modifier = Modifier.animateItem(),
                                )
                            }
                        }
                        Spacer(Modifier.height(10.dp))
                    }
                }
            }

            item(key = "play-error") {
                Column {
                    ErrorBanner(state.playError, onDismiss = actions::dismissPlayError)
                    if (state.playError.isNotBlank()) Spacer(Modifier.height(10.dp))
                }
            }

            item(key = "conn-error") {
                val showConnErr = state.connectionError.isNotBlank() && state.voices.isEmpty()
                Column {
                    ErrorBanner(if (showConnErr) state.connectionError else "", onRetry = actions::refresh)
                    if (showConnErr) Spacer(Modifier.height(10.dp))
                }
            }

            if (groups.isEmpty()) {
                item(key = "empty") {
                    Card(
                        Modifier.fillMaxWidth().animateItem(),
                        insideMargin = PaddingValues(24.dp),
                    ) {
                        Column(
                            Modifier.fillMaxWidth(),
                            horizontalAlignment = Alignment.CenterHorizontally,
                            verticalArrangement = Arrangement.spacedBy(10.dp),
                        ) {
                            if (state.busy) {
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

            groups.forEach { (providerId, voices) ->
                item(key = "header/$providerId") {
                    SmallTitle(
                        text = "${providerName(providerId)} · ${voices.size}",
                        modifier = Modifier.animateItem(),
                        insideMargin = PaddingValues(start = 6.dp, top = 8.dp, bottom = 6.dp),
                    )
                }
                itemsIndexed(
                    items = voices,
                    key = { _, v -> "voice/${providerId.length}/$providerId/${v.id}" },
                ) { index, voice ->
                    val isFirst = index == 0
                    val isLast = index == voices.lastIndex
                    val isSelected = voice.key == state.selected
                    val isFav = voice.key in state.favorites
                    val isThisPreview = state.playingPreview && state.playingVoice == voice.key && state.transport != Transport.IDLE
                    val previewState = when {
                        !isThisPreview -> PreviewVisual.IDLE
                        state.transport == Transport.BUFFERING -> PreviewVisual.BUFFERING
                        else -> PreviewVisual.PLAYING
                    }
                    VoiceRow(
                        voice = voice,
                        chosen = isSelected,
                        favorite = isFav,
                        previewState = previewState,
                        enabled = !state.connecting,
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
    val scale by animateFloatAsState(
        if (selected) 1f else 0.97f,
        AppMotion.popSpec(),
        label = "chip-scale",
    )
    Box(
        modifier = modifier
            .graphicsLayer { scaleX = scale; scaleY = scale }
            .clip(RoundedCornerShape(100))
            .background(bg)
            .clickable(onClick = onClick)
            .padding(horizontal = 13.dp, vertical = 7.dp),
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

@Composable
private fun VoiceRow(
    voice: VoiceItem,
    chosen: Boolean,
    favorite: Boolean,
    previewState: PreviewVisual,
    enabled: Boolean,
    first: Boolean,
    last: Boolean,
    accent: androidx.compose.ui.graphics.Color,
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
    val rowBg by animateColorAsState(
        if (chosen) colors.tertiaryContainer.copy(alpha = 0.65f) else colors.surfaceContainer,
        AppMotion.colorSpec(),
        label = "row-bg",
    )
    val avatarBg by animateColorAsState(
        if (chosen) colors.primary else accent.copy(alpha = 0.14f),
        AppMotion.colorSpec(),
        label = "avatar-bg",
    )
    val titleColor by animateColorAsState(
        if (chosen) colors.primary else colors.onSurface,
        AppMotion.colorSpec(),
        label = "row-title",
    )
    val heartTint by animateColorAsState(
        if (favorite) colors.primary else colors.onSurfaceVariantActions,
        AppMotion.colorSpec(),
        label = "heart-tint",
    )
    val heartScale by animateFloatAsState(
        if (favorite) 1.08f else 1.0f,
        AppMotion.popSpec(),
        label = "heart-scale",
    )
    val previewBg by animateColorAsState(
        if (previewState != PreviewVisual.IDLE) colors.primary else colors.secondaryContainer,
        AppMotion.colorSpec(),
        label = "preview-bg",
    )

    Surface(
        modifier = modifier.fillMaxWidth(),
        shape = shape,
        color = rowBg,
    ) {
        Column(Modifier.fillMaxWidth()) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .semantics { selected = chosen }
                    .clickable(enabled = enabled) { actions.select(voice) }
                    .padding(start = 14.dp, end = 6.dp, top = 11.dp, bottom = 11.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(
                    modifier = Modifier
                        .size(36.dp)
                        .clip(CircleShape)
                        .background(avatarBg),
                    contentAlignment = Alignment.Center,
                ) {
                    AnimatedContent(
                        targetState = chosen,
                        transitionSpec = { AppMotion.iconSwap() },
                        label = "voice-check",
                    ) { isChosen ->
                        if (isChosen) {
                            Icon(MiuixIcons.Basic.Check, null, Modifier.size(18.dp), tint = colors.onPrimary)
                        } else {
                            Text(
                                text = voice.displayName.take(1),
                                style = MiuixTheme.textStyles.body2,
                                fontWeight = FontWeight.SemiBold,
                                color = accent,
                            )
                        }
                    }
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(
                            text = voice.displayName,
                            style = MiuixTheme.textStyles.body1,
                            fontWeight = if (chosen) FontWeight.SemiBold else FontWeight.Normal,
                            color = titleColor,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.weight(1f, fill = false),
                        )
                        AnimatedVisibility(
                            visible = chosen,
                            enter = fadeIn(tween(180, easing = FastOutSlowInEasing)) +
                                expandHorizontally(AppMotion.sizeSpec()),
                            exit = fadeOut(tween(120, easing = FastOutSlowInEasing)) +
                                shrinkHorizontally(AppMotion.sizeSpec()),
                        ) {
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
                    minWidth = 42.dp,
                    minHeight = 42.dp,
                ) {
                    AnimatedContent(
                        targetState = favorite,
                        transitionSpec = { AppMotion.iconSwap() },
                        label = "favorite-icon",
                    ) { fav ->
                        Icon(
                            if (fav) heartFilledIcon else heartIcon,
                            contentDescription = (if (fav) "取消收藏：" else "收藏：") + voice.displayName,
                            modifier = Modifier
                                .size(19.dp)
                                .graphicsLayer { scaleX = heartScale; scaleY = heartScale },
                            tint = heartTint,
                        )
                    }
                }
                IconButton(
                    onClick = { actions.preview(voice) },
                    enabled = enabled,
                    minWidth = 42.dp,
                    minHeight = 42.dp,
                ) {
                    Box(
                        modifier = Modifier
                            .size(32.dp)
                            .clip(CircleShape)
                            .background(previewBg),
                        contentAlignment = Alignment.Center,
                    ) {
                        AnimatedContent(
                            targetState = previewState,
                            transitionSpec = { AppMotion.iconSwap() },
                            label = "preview-icon",
                        ) { pv ->
                            when (pv) {
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
            }
            if (!last) {
                HorizontalDivider(Modifier.padding(start = 62.dp, end = 16.dp))
            }
        }
    }
}
