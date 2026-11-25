package io.github.legadotts.client.service

import io.github.legadotts.client.data.CancellationToken
import io.github.legadotts.client.data.ClientException
import java.io.InputStream
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/** A slow or paused decoder applies backpressure independently to each HTTP reader. */
private class SegmentPipe(private val token: CancellationToken) : InputStream() {
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
            while (queuedBytes + chunk.size > MAX_BUFFERED_BYTES && failure == null && !ended) {
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

    // Called with the lock held. Deliver already received bytes before a late failure.
    private fun awaitData(): Boolean {
        while (queue.isEmpty()) {
            token.check()
            failure?.let { throw it }
            if (ended) return false
            notEmpty.await(150, TimeUnit.MILLISECONDS)
        }
        token.check()
        return true
    }

    fun awaitFirstAudio() = lock.withLock {
        if (!awaitData()) throw ClientException("音频为空")
    }

    private fun nextBlock(): ByteArray? = lock.withLock {
        if (!awaitData()) return null
        val item = queue.removeFirst()
        queuedBytes -= item.size
        notFull.signal()
        item
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

/** Start N+1 after N has audio, not after N reaches EOF. Only consume pipes in text order. */
internal fun streamSegments(
    parts: Sequence<String>, token: CancellationToken,
    fetch: (String, CancellationToken) -> InputStream,
    consume: (String, InputStream) -> Unit,
) {
    val readers = Executors.newFixedThreadPool(2) { r -> Thread(r, "speech-network").apply { isDaemon = true } }
    val transfers = CancellationToken()
    fun stopReaders() {
        readers.shutdownNow()
        transfers.cancel() // also disconnect reads blocked in HttpURLConnection
    }
    val hook = token.onCancel(::stopReaders)
    fun start(part: String): SegmentPipe {
        token.check()
        val pipe = SegmentPipe(transfers)
        readers.execute {
            try {
                transfers.check()
                fetch(part, transfers).use { input ->
                    val buffer = ByteArray(16384)
                    while (true) {
                        transfers.check()
                        val n = input.read(buffer)
                        if (n < 0) break
                        if (n > 0) pipe.offer(buffer.copyOf(n))
                    }
                }
                pipe.end()
            } catch (e: Exception) {
                pipe.fail(e)
            }
        }
        return pipe
    }
    try {
        val partsIterator = parts.iterator()
        if (!partsIterator.hasNext()) return
        var part = partsIterator.next()
        var current = start(part)
        while (true) {
            current.awaitFirstAudio()
            val nextPart = if (partsIterator.hasNext()) partsIterator.next() else null
            val next = nextPart?.let(::start)
            consume(part, current)
            part = nextPart ?: break
            current = checkNotNull(next)
        }
    } finally {
        hook.close()
        stopReaders()
    }
}
