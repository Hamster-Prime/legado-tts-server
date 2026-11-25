package io.github.legadotts.client.service

import android.media.AudioFormat
import android.media.MediaCodec
import android.media.MediaFormat
import android.os.SystemClock
import io.github.legadotts.client.data.CancellationToken
import io.github.legadotts.client.data.ClientApi
import io.github.legadotts.client.data.ClientException
import io.github.legadotts.client.data.Voice
import java.io.InputStream

internal data class PcmFormat(val rate: Int, val channels: Int)

/** Called on the AudioTrack / framework synthesis worker, never the main or binder thread. */
internal fun streamSpeech(
    api: ClientApi,
    text: String,
    voice: Voice,
    speed: Float,
    preview: Boolean,
    token: CancellationToken,
    singleUtterance: Boolean = false,
    onProgress: (spokenChars: Int, totalChars: Int, segmentIndex: Int) -> Unit = { _, _, _ -> },
    sink: (PcmFormat, ByteArray) -> Unit,
) {
    val parts = if (preview) sequenceOf("")
        else speechSegments(text, api.maxTextLength(token), voice.provider, singleUtterance)
    val totalChars = text.trim().length
    var spokenChars = 0
    var segmentIndex = 0
    streamSegments(parts, token, { part, transfer -> api.synthesis(part, voice, speed, preview, transfer) }) { part, pipe ->
        segmentIndex++
        if (!preview) onProgress(spokenChars, totalChars, segmentIndex)
        decodeMp3(pipe, token, sink)
        spokenChars = minOf(totalChars, spokenChars + part.length)
        if (!preview) onProgress(spokenChars, totalChars, segmentIndex)
    }
}

internal fun decodeMp3(
    stream: InputStream, token: CancellationToken, sink: (PcmFormat, ByteArray) -> Unit,
) {
    val frames = Mp3Frames(stream)
    val first = frames.next() ?: throw ClientException("音频为空")
    val decoder = MediaCodec.createDecoderByType("audio/mpeg")
    try {
        val format = MediaFormat.createAudioFormat("audio/mpeg", first.rate, first.channels)
        format.setInteger(MediaFormat.KEY_PCM_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
        decoder.configure(format, null, null, 0)
        decoder.start()
        var pending: Mp3Frame? = first
        var samples = 0L
        var endQueued = false
        var output = PcmFormat(first.rate, first.channels)
        var produced = false
        var codecProgress = SystemClock.elapsedRealtime()
        val info = MediaCodec.BufferInfo()
        while (true) {
            token.check()
            if (SystemClock.elapsedRealtime() - codecProgress > 30_000L)
                throw ClientException("设备音频解码无响应")
            if (!endQueued) {
                val index = decoder.dequeueInputBuffer(1000)
                if (index >= 0) {
                    val frame = pending ?: frames.next()
                    pending = null
                    val pts = samples * 1_000_000 / first.rate
                    if (frame == null) {
                        decoder.queueInputBuffer(index, 0, 0, pts, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        endQueued = true
                    } else {
                        val buffer = checkNotNull(decoder.getInputBuffer(index))
                        buffer.clear()
                        buffer.put(frame.bytes)
                        decoder.queueInputBuffer(index, 0, frame.bytes.size, pts, 0)
                        samples += frame.samples
                    }
                    codecProgress = SystemClock.elapsedRealtime()
                }
            }
            when (val idx = decoder.dequeueOutputBuffer(info, if (endQueued) 10_000 else 0)) {
                MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    val actual = decoder.outputFormat
                    val encoding = if (actual.containsKey(MediaFormat.KEY_PCM_ENCODING))
                        actual.getInteger(MediaFormat.KEY_PCM_ENCODING) else AudioFormat.ENCODING_PCM_16BIT
                    if (encoding != AudioFormat.ENCODING_PCM_16BIT) throw ClientException("设备解码格式不兼容")
                    output = PcmFormat(actual.getInteger(MediaFormat.KEY_SAMPLE_RATE),
                        actual.getInteger(MediaFormat.KEY_CHANNEL_COUNT))
                }
                in 0..Int.MAX_VALUE -> {
                    val bytes = if (info.size > 0) ByteArray(info.size).also {
                        val buf = checkNotNull(decoder.getOutputBuffer(idx))
                        buf.position(info.offset)
                        buf.limit(info.offset + info.size)
                        buf.get(it)
                    } else null
                    decoder.releaseOutputBuffer(idx, false)
                    if (bytes != null) {
                        produced = true
                        sink(output, bytes)
                    }
                    codecProgress = SystemClock.elapsedRealtime()
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) break
                }
            }
        }
        if (!produced) throw ClientException("未能解码服务端音频")
    } finally {
        decoder.release()
    }
}
