package io.github.legadotts.client.service

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.SystemClock
import io.github.legadotts.client.data.CancellationToken
import io.github.legadotts.client.data.ClientException

internal val speechAttributes: AudioAttributes
    get() = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA)
        .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build()

/**
 * A single AudioTrack across sentence boundaries. Non-blocking writes allow transport
 * commands from the main thread to pause/flush immediately, including during buffering.
 */
internal class SpeechOutput(private val token: CancellationToken, private var speed: Float) {
    private var track: AudioTrack? = null
    private var format: PcmFormat? = null
    private var paused = false
    private var closed = false
    private var submitted = 0L
    var hasAudio = false
        private set

    @Synchronized fun pause() { paused = true; track?.pause() }
    @Synchronized fun resume() { paused = false; track?.play() }
    @Synchronized fun setSpeed(value: Float) {
        if (closed || speed == value) return
        speed = value
        track?.let {
            // A positive playback speed can resume a paused AudioTrack on some devices.
            it.playbackParams = it.playbackParams.setSpeed(value).setPitch(1f)
            if (paused) it.pause()
        }
    }
    @Synchronized fun close() {
        closed = true
        track?.let { t ->
            try { t.pause(); t.flush() } finally { t.release() }
        }
        track = null
    }

    @Synchronized private fun writeStep(f: PcmFormat, pcm: ByteArray, offset: Int): Int {
        token.check()
        if (closed) throw ClientException("已停止")
        if (paused) return 0
        if (track == null) {
            val mask = when (f.channels) {
                1 -> AudioFormat.CHANNEL_OUT_MONO
                2 -> AudioFormat.CHANNEL_OUT_STEREO
                else -> throw ClientException("设备不支持该声道数")
            }
            val minimum = AudioTrack.getMinBufferSize(f.rate, mask, AudioFormat.ENCODING_PCM_16BIT)
            if (minimum <= 0) throw ClientException("设备不支持该采样率")
            track = AudioTrack.Builder().setAudioAttributes(speechAttributes)
                .setAudioFormat(AudioFormat.Builder().setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(f.rate).setChannelMask(mask).build())
                // ~0.75 s (16-bit PCM = 2 bytes/sample): absorbs decoder reset & silence hold gaplessly.
                .setBufferSizeInBytes(maxOf(minimum * 2, f.rate * f.channels * 2 * 3 / 4))
                .setTransferMode(AudioTrack.MODE_STREAM).build()
            format = f
            track!!.let {
                if (speed != 1f) it.playbackParams = it.playbackParams.setSpeed(speed).setPitch(1f)
                it.play()
            }
        }
        if (format != f) throw ClientException("服务端合成中途改变了音频格式")
        val n = track!!.write(pcm, offset, pcm.size - offset, AudioTrack.WRITE_NON_BLOCKING)
        if (n < 0) throw ClientException("手机音频输出不可用")
        submitted += n / (2 * f.channels)
        if (n > 0) hasAudio = true
        return n
    }

    fun write(f: PcmFormat, bytes: ByteArray, firstWrite: () -> Unit) {
        var offset = 0
        var lastProgress = SystemClock.elapsedRealtime()
        while (offset < bytes.size) {
            val previous = hasAudio
            val n = writeStep(f, bytes, offset)
            if (!previous && n > 0) firstWrite()
            val holding = synchronized(this) { paused }
            val now = SystemClock.elapsedRealtime()
            if (n > 0 || holding) lastProgress = now
            if (now - lastProgress > 15_000L) throw ClientException("音频输出无响应")
            if (n == 0) Thread.sleep(if (holding) 80 else 12)
            offset += n
        }
    }

    fun drain() {
        // STREAM tracks can wait for their start threshold after an underrun (or
        // an utterance shorter than one hardware buffer). Pad only the hardware
        // tail and still wait for exactly the last *speech* frame.
        val padding = synchronized(this) {
            val t = track ?: return
            val frameBytes = (format ?: return).channels * 2
            ByteArray(t.bufferCapacityInFrames * frameBytes)
        }
        var padOffset = 0
        var lastHead = -1
        var lastProgress = SystemClock.elapsedRealtime()
        // playbackHeadPosition is an unsigned 32 bit frame counter. Track its wraps in
        // the comparison as our final hardware-buffer backlog is always much < 2^31.
        while (true) {
            token.check()
            val done = synchronized(this) {
                val t = track ?: return
                if (!paused && padOffset < padding.size) {
                    val n = t.write(padding, padOffset, padding.size - padOffset, AudioTrack.WRITE_NON_BLOCKING)
                    if (n < 0) throw ClientException("手机音频输出不可用")
                    padOffset += n
                }
                val head = t.playbackHeadPosition
                val now = SystemClock.elapsedRealtime()
                if (head != lastHead || paused) lastProgress = now
                lastHead = head
                if (now - lastProgress > 15_000L) throw ClientException("音频输出未能播放完毕")
                (submitted.toInt() - head) <= 0
            }
            if (done) return
            Thread.sleep(18)
        }
    }
}
