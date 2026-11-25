package io.github.legadotts.client.service

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.media.*
import android.media.session.MediaSession
import android.media.session.PlaybackState
import android.os.*
import io.github.legadotts.client.data.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

enum class Phase { IDLE, BUFFERING, PLAYING, PAUSED, ERROR }
data class PlayerState(
    val phase: Phase = Phase.IDLE,
    val message: String = "",
    val voiceKey: Pair<String, String>? = null,
    val voiceName: String = "",
    val preview: Boolean = false,
    val progress: Float? = null,
    val segment: Int = 0,
)

/** Transport/session/focus state is main-thread confined. Each generation owns its own output. */
class PlaybackService : Service() {
    private data class Request(val text: String, val voice: Voice, val speed: Float,
                               val expectedAddress: String, val preview: Boolean)
    private class Run(val request: Request) {
        val token = CancellationToken()
        val output = SpeechOutput(token, request.speed)
        var speed = request.speed
        var worker: Thread? = null
        var gotPcm = false
        var progress: Float? = null
        var segment: Int = 0
        @Volatile var lastProgress = SystemClock.elapsedRealtime()
    }
    private var run: Run? = null
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var session: MediaSession
    private lateinit var wake: PowerManager.WakeLock
    private lateinit var audio: AudioManager
    private var focus: AudioFocusRequest? = null
    private var resumeOnGain = false
    private val guard = object : Runnable {
        override fun run() {
            val current = this@PlaybackService.run ?: return
            if (state.value.phase == Phase.PAUSED) return
            if (SystemClock.elapsedRealtime() - current.lastProgress >= STALL_MS) {
                finishRun("合成或播放长时间无进展，已停止，请检查网络及音频输出")
            } else {
                wake.acquire(LEASE_MS)
                handler.postDelayed(this, GUARD_MS)
            }
        }
    }
    private fun supervise(current: Run) {
        current.lastProgress = SystemClock.elapsedRealtime()
        wake.acquire(LEASE_MS)
        handler.removeCallbacks(guard)
        handler.postDelayed(guard, GUARD_MS)
    }
    private val unplug = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == AudioManager.ACTION_AUDIO_BECOMING_NOISY) pauseRun()
        }
    }

    override fun onCreate() {
        super.onCreate()
        wake = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LegadoTts:reading")
        wake.setReferenceCounted(false)
        audio = getSystemService(AudioManager::class.java)
        getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "后台朗读", NotificationManager.IMPORTANCE_LOW))
        session = MediaSession(this, "后台朗读").apply {
            setCallback(object : MediaSession.Callback() {
                override fun onPlay() = resumeRun()
                override fun onPause() = pauseRun()
                override fun onStop() = finishRun()
            }, handler)
        }
        val filter = IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(unplug, filter, RECEIVER_NOT_EXPORTED)
        else @Suppress("UnspecifiedRegisterReceiverFlag") registerReceiver(unplug, filter)
        activeService = this
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            PLAY -> {
                // Immediately satisfy startForegroundService's deadline, even if a new
                // request superseded this one or the process was killed between calls.
                foreground()
                val req = pending.getAndSet(null)
                if (req != null) start(req) else if (run == null) finishRun()
            }
            PAUSE -> pauseRun()
            RESUME -> resumeRun()
            STOP -> finishRun()
        }
        if (run == null) stopSelf(startId)
        return START_NOT_STICKY
    }

    private fun start(request: Request) {
        releaseRun()
        val current = Run(request)
        run = current
        session.isActive = true
        session.setMetadata(MediaMetadata.Builder().putString(MediaMetadata.METADATA_KEY_TITLE,
            if (request.preview) "音色试听" else "文本朗读")
            .putString(MediaMetadata.METADATA_KEY_ARTIST, request.voice.name).build())
        if (!requestFocus(current)) {
            finishRun("未取得音频焦点，请稍后重试")
            return
        }
        supervise(current)
        publish(Phase.BUFFERING)
        current.worker = thread(name = "speech-output", isDaemon = true) {
            try {
                val endpoint = ClientSettings(applicationContext).endpoint()
                    ?: throw ClientException("请先配置服务端")
                if (request.expectedAddress != endpoint.base)
                    throw ClientException("服务地址已更改，请重新开始朗读")
                // All app playback is time-stretched at the output. This also updates
                // already-prefetched sentences and needs no duplicate upstream requests.
                streamSpeech(
                    api = ClientApi(endpoint),
                    text = request.text,
                    voice = request.voice,
                    speed = 1f,
                    preview = request.preview,
                    token = current.token,
                    singleUtterance = false,
                    onProgress = { spoken, total, seg ->
                        val frac = if (total > 0) (spoken.toFloat() / total).coerceIn(0f, 1f) else null
                        handler.post {
                            if (run === current) {
                                current.progress = frac
                                current.segment = seg
                                val phase = state.value.phase
                                if (phase == Phase.PLAYING || phase == Phase.PAUSED || phase == Phase.BUFFERING) {
                                    updates.value = updates.value.copy(progress = frac, segment = seg)
                                }
                            }
                        }
                    },
                ) { format, pcm ->
                    current.output.write(format, pcm) {
                        handler.post {
                            if (run === current) {
                                current.gotPcm = true
                                if (state.value.phase != Phase.PAUSED) publish(Phase.PLAYING)
                            }
                        }
                    }
                    current.lastProgress = SystemClock.elapsedRealtime()
                }
                current.output.drain()
                handler.post { if (run === current) finishRun() }
            } catch (e: Exception) {
                handler.post { if (run === current) finishRun(safeMessage(e)) }
            }
        }
    }

    private fun requestFocus(current: Run): Boolean {
        if (focus != null) return true
        // Reject queued callbacks from abandoned focus requests (replacement/new playback).
        lateinit var req: AudioFocusRequest
        val listener = AudioManager.OnAudioFocusChangeListener { change ->
            if (focus === req && run === current) when (change) {
                AudioManager.AUDIOFOCUS_LOSS -> pauseRun()
                AudioManager.AUDIOFOCUS_LOSS_TRANSIENT, AudioManager.AUDIOFOCUS_LOSS_TRANSIENT_CAN_DUCK ->
                    if (state.value.phase != Phase.PAUSED) pauseRun(transient = true)
                AudioManager.AUDIOFOCUS_GAIN -> if (resumeOnGain) resumeRun()
            }
        }
        req = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
            .setAudioAttributes(speechAttributes).setWillPauseWhenDucked(true)
            .setOnAudioFocusChangeListener(listener, handler).build()
        if (audio.requestAudioFocus(req) != AudioManager.AUDIOFOCUS_REQUEST_GRANTED) return false
        focus = req
        return true
    }
    private fun dropFocus() {
        val previous = focus
        focus = null
        previous?.let { audio.abandonAudioFocusRequest(it) }
    }
    private fun pauseRun(transient: Boolean = false) {
        val current = run ?: return
        resumeOnGain = transient
        if (runCatching { current.output.pause() }.isFailure) {
            finishRun("音频输出不可用，请重新开始朗读")
            return
        }
        if (!transient) dropFocus()
        handler.removeCallbacks(guard)
        if (wake.isHeld) wake.release()
        publish(Phase.PAUSED)
    }
    private fun resumeRun() {
        val current = run ?: return
        if (state.value.phase != Phase.PAUSED) return
        resumeOnGain = false
        if (!requestFocus(current)) return
        supervise(current)
        if (runCatching { current.output.resume() }.isFailure) {
            finishRun("音频输出不可用，请重新开始朗读")
            return
        }
        publish(if (current.gotPcm) Phase.PLAYING else Phase.BUFFERING)
    }
    private fun releaseRun() {
        val old = run
        handler.removeCallbacks(guard)
        run = null
        resumeOnGain = false
        old?.let {
            runCatching { it.output.close() }
            it.worker?.interrupt()
            // HttpURLConnection.disconnect can block; never run network teardown on main.
            thread(name = "speech-cancel", isDaemon = true) { it.token.cancel() }
        }
        dropFocus()
        if (wake.isHeld) wake.release()
    }
    private fun finishRun(error: String? = null) {
        releaseRun()
        updates.value = if (error == null) PlayerState() else PlayerState(Phase.ERROR, error)
        session.isActive = false
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }
    private fun publish(phase: Phase) {
        val current = run ?: return
        val voice = current.request.voice
        val message = when (phase) {
            Phase.PAUSED -> "已暂停"
            Phase.BUFFERING -> if (current.request.preview) "正在准备试听" else "正在合成"
            else -> if (current.request.preview) "正在试听" else "正在朗读"
        }
        updates.value = PlayerState(
            phase = phase,
            message = message + " · " + voice.name,
            voiceKey = voice.provider to voice.id,
            voiceName = voice.name,
            preview = current.request.preview,
            progress = current.progress,
            segment = current.segment,
        )
        val osState = when (phase) {
            Phase.PAUSED -> PlaybackState.STATE_PAUSED
            Phase.BUFFERING -> PlaybackState.STATE_BUFFERING
            else -> PlaybackState.STATE_PLAYING
        }
        session.setPlaybackState(PlaybackState.Builder().setActions(PlaybackState.ACTION_PLAY or
            PlaybackState.ACTION_PAUSE or PlaybackState.ACTION_PLAY_PAUSE or PlaybackState.ACTION_STOP)
            .setState(osState, PlaybackState.PLAYBACK_POSITION_UNKNOWN, if (phase == Phase.PAUSED) 0f else current.speed)
            .build())
        foreground()
    }
    private fun foreground() {
        val paused = state.value.phase == Phase.PAUSED
        fun action(code: Int, icon: Int, label: String, cmd: String): Notification.Action {
            val intent = Intent(this, PlaybackService::class.java).setAction(cmd)
            return Notification.Action.Builder(icon, label, PendingIntent.getService(
                this, code, intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            )).build()
        }
        val note = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_media_play).setContentTitle("声读 · Legado TTS")
            .setContentText(state.value.message.ifBlank { "准备朗读" })
            .setContentIntent(packageManager.getLaunchIntentForPackage(packageName)?.let {
                PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_IMMUTABLE)
            }).setOnlyAlertOnce(true).setOngoing(!paused)
            .setStyle(Notification.MediaStyle().setMediaSession(session.sessionToken).setShowActionsInCompactView(0, 1))
            .addAction(if (paused) action(1, android.R.drawable.ic_media_play, "继续", RESUME)
                else action(2, android.R.drawable.ic_media_pause, "暂停", PAUSE))
            .addAction(action(3, android.R.drawable.ic_menu_close_clear_cancel, "停止", STOP)).build()
        if (Build.VERSION.SDK_INT >= 29) startForeground(ID, note, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK)
        else startForeground(ID, note)
    }
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() {
        if (activeService === this) activeService = null
        if (run != null) updates.value = PlayerState()
        releaseRun()
        unregisterReceiver(unplug)
        session.release()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "spoken-audio"
        private const val ID = 201
        private const val STALL_MS = 120_000L
        private const val LEASE_MS = 180_000L
        private const val GUARD_MS = 30_000L
        private const val PLAY = "play"
        private const val PAUSE = "pause"
        private const val RESUME = "resume"
        private const val STOP = "stop"
        private val pending = AtomicReference<Request?>()
        @Volatile private var activeService: PlaybackService? = null
        private val updates = MutableStateFlow(PlayerState())
        val state: StateFlow<PlayerState> = updates
        fun play(context: Context, text: String, voice: Voice, speed: Float, expectedAddress: String,
                 preview: Boolean = false) {
            if (!preview && text.isBlank()) return
            // No credentials or arbitrarily long reading text in Binder bundles.
            val request = Request(text, voice, speed.coerceIn(.25f, 4f), expectedAddress, preview)
            pending.set(request)
            try { context.startForegroundService(Intent(context, PlaybackService::class.java).setAction(PLAY)) }
            catch (_: RuntimeException) {
                pending.compareAndSet(request, null)
                updates.value = PlayerState(Phase.ERROR, "系统未允许启动后台播放器")
            }
        }
        fun pause(context: Context) { if (activeService != null) command(context, PAUSE) }
        fun resume(context: Context) { if (activeService != null) command(context, RESUME) }
        fun stop(context: Context) {
            pending.set(null)
            if (activeService != null) command(context, STOP) else updates.value = PlayerState()
        }
        fun clearError() {
            if (updates.value.phase == Phase.ERROR) updates.value = PlayerState()
        }
        fun setSpeed(@Suppress("UNUSED_PARAMETER") context: Context, value: Float) {
            if (!value.isFinite()) return
            val speed = value.coerceIn(.5f, 2f)
            pending.updateAndGet { it?.copy(speed = speed) }
            val service = activeService ?: return
            service.handler.post {
                val current = service.run ?: return@post
                current.speed = speed
                if (runCatching { current.output.setSpeed(speed) }.isFailure) {
                    service.finishRun("音频设备不支持该语速")
                } else if (state.value.phase == Phase.PLAYING) {
                    service.publish(Phase.PLAYING)
                }
            }
        }
        private fun command(context: Context, cmd: String) {
            runCatching { context.startService(Intent(context, PlaybackService::class.java).setAction(cmd)) }
        }
        private fun safeMessage(e: Exception) = when (e) {
            is ClientException -> e.message ?: "合成请求失败"
            is java.net.SocketTimeoutException -> "连接超时，请检查网络后重试"
            is java.io.IOException -> "网络连接异常，请检查服务端后重试"
            else -> "朗读失败，请检查服务配置及音频输出"
        }
    }
}
