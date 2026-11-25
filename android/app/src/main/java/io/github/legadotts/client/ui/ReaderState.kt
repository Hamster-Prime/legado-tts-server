package io.github.legadotts.client.ui

/** Display models only. In particular, saved credentials must never be passed into Compose. */
data class VoiceItem(
    val provider: String,
    val id: String,
    val name: String,
    val detail: String = "",
) {
    val key: Pair<String, String> get() = provider to id

    /** Splits server labels like "晓晓 - 女声" into primary display name and style tag. */
    val displayName: String get() = name.substringBefore(" - ").trim().ifBlank { name.ifBlank { id } }
    val tag: String get() = name.substringAfter(" - ", "").trim().ifBlank { detail }
}

enum class Transport { IDLE, BUFFERING, PLAYING, PAUSED }

enum class ConnectionStatus { DISCONNECTED, CONNECTING, CONNECTED, ERROR }

data class Notice(val id: Long, val text: String)

data class ReaderState(
    val initializing: Boolean = true,
    val configured: Boolean = false,
    val connectionStatus: ConnectionStatus = ConnectionStatus.DISCONNECTED,
    val connectionError: String = "",
    val formError: String = "",
    val address: String = "",
    val hasKey: Boolean = false,
    val connecting: Boolean = false,
    val refreshing: Boolean = false,
    val notice: Notice? = null,
    val voices: List<VoiceItem> = emptyList(),
    val favorites: Set<Pair<String, String>> = emptySet(),
    val selected: Pair<String, String>? = null,
    val selectedVoiceFallback: VoiceItem? = null,
    val text: String = "",
    val speed: Float = 1f,
    val transport: Transport = Transport.IDLE,
    val playMessage: String = "",
    val playError: String = "",
    val playingVoice: Pair<String, String>? = null,
    val playingVoiceName: String = "",
    val playingPreview: Boolean = false,
    val playProgress: Float? = null,
    val playSegment: Int = 0,
) {
    val busy: Boolean get() = connecting || refreshing
    val activeVoice: VoiceItem? get() = voices.find { it.key == selected } ?: selectedVoiceFallback
}

interface ReaderActions {
    /** null = keep the stored key, only valid for the exact saved address. Empty = no key. */
    fun connect(address: String, key: String?)
    fun refresh()
    fun select(voice: VoiceItem)
    fun favorite(voice: VoiceItem)
    fun preview(voice: VoiceItem)
    fun editText(text: String)
    fun setSpeed(value: Float)
    fun play()
    fun pause()
    fun resume()
    fun stop()
    fun dismissPlayError()
    fun dismissNotice()
    fun clearFormError()
}
