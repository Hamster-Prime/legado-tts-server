package io.github.legadotts.client

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.legadotts.client.data.*
import io.github.legadotts.client.service.Phase
import io.github.legadotts.client.service.PlaybackService
import io.github.legadotts.client.ui.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.util.concurrent.atomic.AtomicLong
import javax.net.ssl.SSLException

/** Retain the editor on rotation, but never serialize book text or credentials into a Bundle. */
class ReaderModel(private val app: Application) : AndroidViewModel(app), ReaderActions {
    private val settings = ClientSettings(app)
    private val disk = Dispatchers.IO.limitedParallelism(1)
    private val mutable = MutableStateFlow(ReaderState(initializing = true))
    val state = mutable.asStateFlow()
    private val noticeSeq = AtomicLong(0L)
    private var request: CancellationToken? = null

    init {
        viewModelScope.launch {
            PlaybackService.state.collect { player ->
                mutable.update {
                    it.copy(
                        transport = when (player.phase) {
                            Phase.BUFFERING -> Transport.BUFFERING
                            Phase.PLAYING -> Transport.PLAYING
                            Phase.PAUSED -> Transport.PAUSED
                            Phase.ERROR, Phase.IDLE -> Transport.IDLE
                        },
                        playMessage = if (player.phase == Phase.ERROR) "" else player.message,
                        playError = if (player.phase == Phase.ERROR) player.message else "",
                        playingVoice = if (player.phase == Phase.IDLE || player.phase == Phase.ERROR) null else player.voiceKey,
                        playingVoiceName = if (player.phase == Phase.IDLE || player.phase == Phase.ERROR) "" else player.voiceName,
                        playingPreview = if (player.phase == Phase.IDLE || player.phase == Phase.ERROR) false else player.preview,
                        playProgress = if (player.phase == Phase.IDLE || player.phase == Phase.ERROR) null else player.progress,
                        playSegment = if (player.phase == Phase.IDLE || player.phase == Phase.ERROR) 0 else player.segment,
                    )
                }
            }
        }
        viewModelScope.launch {
            try {
                val saved = withContext(disk) {
                    val address = settings.address()
                    val configured = address.isNotEmpty()
                    val selected = settings.selectedVoice()?.let { VoiceItem(it.provider, it.id, it.name) }
                    val cached = settings.cachedVoices().map { VoiceItem(it.provider, it.id, it.name) }
                    ReaderState(
                        initializing = false,
                        configured = configured,
                        connectionStatus = if (configured) ConnectionStatus.CONNECTING else ConnectionStatus.DISCONNECTED,
                        address = address,
                        hasKey = settings.hasKey(),
                        voices = cached,
                        favorites = settings.favorites(),
                        selected = selected?.key,
                        selectedVoiceFallback = selected,
                        speed = settings.speed(),
                    )
                }
                mutable.update {
                    saved.copy(
                        text = it.text,
                        transport = it.transport,
                        playMessage = it.playMessage,
                        playError = it.playError,
                        playingVoice = it.playingVoice,
                        playingVoiceName = it.playingVoiceName,
                        playingPreview = it.playingPreview,
                        playProgress = it.playProgress,
                        playSegment = it.playSegment,
                    )
                }
                if (saved.configured) fetch(saved.address, null, verify = false, silent = true)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                mutable.update {
                    it.copy(
                        initializing = false,
                        connectionStatus = ConnectionStatus.ERROR,
                        connectionError = "无法读取本机设置，请重新验证地址与密钥",
                    )
                }
            }
        }
    }

    override fun connect(address: String, key: String?) {
        fetch(address, key, verify = true, silent = false)
    }

    override fun refresh() = fetch(state.value.address, null, verify = false, silent = false)

    private fun fetch(address: String, key: String?, verify: Boolean, silent: Boolean) {
        if (request != null) return
        val token = CancellationToken()
        request = token
        val editingExisting = verify && state.value.configured
        mutable.update {
            it.copy(
                connecting = verify,
                refreshing = !verify,
                connectionStatus = if (editingExisting) it.connectionStatus else ConnectionStatus.CONNECTING,
                connectionError = if (editingExisting) it.connectionError else "",
                formError = if (verify) "" else it.formError,
            )
        }
        viewModelScope.launch {
            try {
                val (ep, catalog, chosen) = withContext(disk) {
                    val normalizedInput = address.trim().trimEnd('/')
                    // Reuse credentials only for the exact previously saved address, including path.
                    val ep = if (key == null) {
                        require(normalizedInput == settings.address()) { "更换地址后请重新输入密钥" }
                        settings.endpoint() ?: throw IllegalArgumentException("请先填写服务端地址")
                    } else Endpoint.of(address, key)
                    val api = ClientApi(ep)
                    if (verify) api.probe(token)
                    val catalog = api.catalog(token)
                    val previous = if (ep.base == state.value.address) settings.selectedVoice() else null
                    val chosen = catalog.find { it.id == previous?.id && it.provider == previous.provider }
                        ?: catalog.find { it.provider == "edge" && it.id == "zh-CN-XiaoxiaoNeural" }
                        ?: catalog.first()
                    token.check()
                    if (verify) {
                        settings.saveConnection(ep, chosen)
                    } else if (previous != chosen) {
                        settings.select(chosen)
                    }
                    settings.cacheVoices(catalog)
                    Triple(ep, catalog, chosen)
                }
                val voiceItems = catalog.map { row -> VoiceItem(row.provider, row.id, row.name) }
                val chosenItem = VoiceItem(chosen.provider, chosen.id, chosen.name)
                val noticeText = when {
                    verify -> "连接成功，已载入 ${catalog.size} 个音色"
                    !silent -> "已刷新，共 ${catalog.size} 个可用音色"
                    else -> null
                }
                mutable.update {
                    it.copy(
                        configured = true,
                        connectionStatus = ConnectionStatus.CONNECTED,
                        connectionError = "",
                        formError = "",
                        address = ep.base,
                        hasKey = ep.key.isNotEmpty(),
                        voices = voiceItems,
                        selected = chosenItem.key,
                        selectedVoiceFallback = chosenItem,
                        favorites = if (it.address == ep.base) it.favorites else emptySet(),
                        notice = noticeText?.let { msg -> Notice(noticeSeq.incrementAndGet(), msg) } ?: it.notice,
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Only present controlled messages, never raw transport errors/URLs or response bodies.
                val message = when (e) {
                    is ClientException -> e.message
                    is IllegalArgumentException -> e.message ?: "地址或密钥格式不正确。密钥必须使用 HTTPS，地址不能包含查询参数。"
                    is SocketTimeoutException -> "连接超时，请检查网络或服务端状态后重试"
                    is SSLException -> "安全连接失败，请检查证书和 HTTPS 地址"
                    is UnknownHostException -> "找不到服务端，请检查地址和网络连接"
                    else -> "验证或加载失败，请检查网络、服务地址和本机密钥设置"
                } ?: "连接失败"
                mutable.update {
                    if (editingExisting) {
                        it.copy(formError = message)
                    } else {
                        it.copy(
                            connectionStatus = ConnectionStatus.ERROR,
                            connectionError = message,
                            formError = if (verify) message else it.formError,
                        )
                    }
                }
            } finally {
                request = null
                mutable.update { it.copy(connecting = false, refreshing = false) }
            }
        }
    }

    private fun voice(item: VoiceItem) = Voice(item.provider, item.id, item.name)

    private fun emitNotice(text: String) {
        mutable.update { it.copy(notice = Notice(noticeSeq.incrementAndGet(), text)) }
    }

    private fun persist(write: () -> Unit) {
        viewModelScope.launch {
            try {
                withContext(disk) { write() }
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                emitNotice("无法写入本机存储，此次更改未保存")
            }
        }
    }

    override fun select(voice: VoiceItem) {
        if (state.value.connecting || voice !in state.value.voices) return
        persist { settings.select(voice(voice)) }
        mutable.update { it.copy(selected = voice.key, selectedVoiceFallback = voice) }
    }

    override fun favorite(voice: VoiceItem) {
        if (state.value.connecting || voice !in state.value.voices) return
        val old = state.value.favorites
        val new = if (voice.key in old) old - voice.key else old + voice.key
        persist { settings.favorites(new) }
        mutable.update { it.copy(favorites = new) }
    }

    override fun setSpeed(value: Float) {
        val speed = value.coerceIn(0.5f, 2f)
        if (state.value.connecting) return
        persist { settings.setSpeed(speed) }
        PlaybackService.setSpeed(app, speed)
        mutable.update { it.copy(speed = speed) }
    }

    override fun preview(voice: VoiceItem) {
        val s = state.value
        if (s.connecting || voice !in s.voices) return
        if (s.playingPreview && s.playingVoice == voice.key && s.transport != Transport.IDLE) {
            stop()
            return
        }
        PlaybackService.play(app, "", voice(voice), s.speed, s.address, preview = true)
    }

    override fun editText(text: String) {
        mutable.update { it.copy(text = text) }
    }

    override fun play() {
        val s = state.value
        if (s.connecting || s.text.isBlank()) return
        val selected = s.activeVoice ?: return
        PlaybackService.play(app, s.text, voice(selected), s.speed, s.address)
    }

    override fun pause() = PlaybackService.pause(app)
    override fun resume() = PlaybackService.resume(app)
    override fun stop() = PlaybackService.stop(app)
    override fun dismissPlayError() = PlaybackService.clearError()
    override fun dismissNotice() { mutable.update { it.copy(notice = null) } }
    override fun clearFormError() { mutable.update { it.copy(formError = "") } }

    override fun onCleared() {
        request?.let { token -> Thread { token.cancel() }.start() }
        super.onCleared()
    }
}
