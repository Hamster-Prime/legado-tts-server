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
import java.util.ArrayDeque
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.thread
import kotlin.concurrent.withLock

internal data class PcmFormat(val rate: Int, val channels: Int)

private val STREAMING_PROVIDERS = setOf("edge", "doubao")
private val MULTI_BLANK_LINES = Regex("\n{2,}")

private sealed interface Event<out T> {
    data class Data<T>(val item: T) : Event<T>
    data class Failure(val error: Exception) : Event<Nothing>
    object End : Event<Nothing>
}

private fun <T> ArrayBlockingQueue<Event<T>>.next(token: CancellationToken): T? {
    while (true) {
        token.check()
        when (val event = poll(150, TimeUnit.MILLISECONDS)) {
            is Event.Data -> return event.item
            is Event.Failure -> throw event.error
            Event.End -> return null
            null -> continue
        }
    }
}

/**
 * Byte-bounded compressed-audio pipe for one segment.
 *
 * Unlike a small packet-count queue (which stalls on streaming providers that emit 500-1400 byte
 * chunks), a 2 MB byte budget holds several minutes of MP3 so the network producer completes the
 * current request and starts prefetching the next segment well before playback reaches the boundary.
 */
private class SegmentPipe(
    private val token: CancellationToken,
    val pauseMs: Int,
    val charCount: Int,
) : InputStream() {
    private val lock = ReentrantLock()
    private val notEmpty = lock.newCondition()
    private val notFull = lock.newCondition()
    private val queue = ArrayDeque<ByteArray>()
    private var queuedBytes = 0
    private var ended = false
    private var failure: Exception? = null

    private var block = ByteArray(0)
    private var index = 0
    private var eof = false

    fun offer(chunk: ByteArray) {
        if (chunk.isEmpty()) return
        lock.withLock {
            while (queuedBytes >= MAX_BUFFERED_BYTES && failure == null && !ended) {
                token.check()
                notFull.await(150, TimeUnit.MILLISECONDS)
            }
            token.check()
            if (failure != null || ended) return
            queue.addLast(chunk)
            queuedBytes += chunk.size
            notEmpty.signal()
        }
    }

    fun end() = lock.withLock {
        ended = true
        notEmpty.signalAll()
        notFull.signalAll()
    }

    fun fail(error: Exception) = lock.withLock {
        if (failure == null) failure = error
        notEmpty.signalAll()
        notFull.signalAll()
    }

    private fun nextBlock(): ByteArray? {
        lock.withLock {
            while (queue.isEmpty()) {
                token.check()
                failure?.let { throw it }
                if (ended) return null
                notEmpty.await(150, TimeUnit.MILLISECONDS)
            }
            val item = queue.removeFirst()
            queuedBytes -= item.size
            notFull.signal()
            return item
        }
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (len == 0) return 0
        if (eof) return -1
        if (index == block.size) {
            val next = nextBlock()
            if (next == null) { eof = true; return -1 }
            block = next
            index = 0
        }
        val n = minOf(block.size - index, len)
        block.copyInto(b, off, index, index + n)
        index += n
        return n
    }

    override fun read(): Int {
        val one = ByteArray(1)
        return if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 255
    }

    companion object {
        const val MAX_BUFFERED_BYTES = 2 * 1024 * 1024
    }
}

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
    // Up to 2 prefetched segments ahead of the segment currently being decoded.
    val segments = ArrayBlockingQueue<Event<SegmentPipe>>(2)
    val streaming = voice.provider in STREAMING_PROVIDERS
    // Fish Audio ignores server-side `rate` and scales playback via output sample rate instead.
    val pauseSpeed = if (voice.provider == "fishaudio") 1f else speed.coerceIn(.25f, 4f)
    val producer = thread(name = "speech-network", isDaemon = true) {
        var current: SegmentPipe? = null
        try {
            val requests = if (preview) sequenceOf(SpeechSegment("", 0))
            else speechSegments(text, api.maxTextLength(token), streaming = streaming, singleUtterance = singleUtterance)
            for (part in requests) {
                token.check()
                val pipe = SegmentPipe(token, (part.pauseMs / pauseSpeed).toInt(), part.text.length)
                segments.put(Event.Data(pipe))
                current = pipe
                // Collapse multi-blank-line runs so upstream engines don't emit double paragraph dead air.
                val sendText = if (preview) "" else MULTI_BLANK_LINES.replace(part.text, "\n")
                api.synthesis(sendText, voice, speed, preview, token).use { stream ->
                    val scratch = ByteArray(16384)
                    while (true) {
                        token.check()
                        val length = stream.read(scratch)
                        if (length == -1) break
                        if (length > 0) pipe.offer(scratch.copyOf(length))
                    }
                }
                pipe.end()
                current = null
            }
            segments.put(Event.End)
        } catch (e: Exception) {
            if (!token.isCancelled() && e !is InterruptedException) {
                try {
                    current?.fail(e) ?: segments.put(Event.Failure(e))
                } catch (_: InterruptedException) { /* stopped during delivery */ }
            }
        }
    }
    val hook = token.onCancel { producer.interrupt() }
    try {
        val maxInnerMs = (SpeechJoiner.DEFAULT_MAX_INNER_SILENCE_MS / pauseSpeed).toInt().coerceIn(220, 720)
        val joiner = SpeechJoiner(maxInnerSilenceMs = maxInnerMs, sink = sink)
        val totalChars = text.trim().length
        var spokenChars = 0
        var segmentIndex = 0
        while (true) {
            val pipe = segments.next(token) ?: break
            segmentIndex++
            if (!preview) onProgress(spokenChars, totalChars, segmentIndex)
            joiner.beginSegment()
            decodeMp3(pipe, token, joiner::write)
            joiner.endSegment(pipe.pauseMs)
            spokenChars = minOf(totalChars, spokenChars + pipe.charCount)
            if (!preview) onProgress(spokenChars, totalChars, segmentIndex)
        }
        joiner.finish()
    } catch (e: Exception) {
        producer.interrupt()
        throw e
    } finally {
        hook.close()
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
