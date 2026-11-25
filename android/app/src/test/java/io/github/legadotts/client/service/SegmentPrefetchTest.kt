package io.github.legadotts.client.service

import io.github.legadotts.client.data.CancellationToken
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

class SegmentPrefetchTest {
    @Test(timeout = 10_000) fun playsFirstBytesAndPrefetchesOneSegmentBeforeEof() {
        val releaseTail = CountDownLatch(1)
        val secondDownloaded = CountDownLatch(1)
        val started = Collections.synchronizedList(mutableListOf<String>())
        val heard = mutableListOf<Int>()
        streamSegments(sequenceOf("A", "B", "C"), CancellationToken(), { part, _ ->
            started.add(part)
            when (part) {
                "A" -> object : ByteArrayInputStream(byteArrayOf(1, 2)) {
                    override fun read(b: ByteArray, off: Int, len: Int): Int {
                        if (available() == 1 && !releaseTail.await(5, TimeUnit.SECONDS))
                            throw IOException("reader waited for EOF")
                        return super.read(b, off, minOf(len, 1))
                    }
                }
                "B" -> object : ByteArrayInputStream(byteArrayOf(3)) {
                    override fun close() = secondDownloaded.countDown()
                }
                else -> ByteArrayInputStream(byteArrayOf(4))
            }
        }) { part, input ->
            if (part == "A") {
                heard.add(input.read())
                assertTrue(secondDownloaded.await(5, TimeUnit.SECONDS))
                assertEquals(listOf("A", "B"), started.toList())
                releaseTail.countDown()
            }
            input.readBytes().forEach { heard.add(it.toInt()) }
        }
        assertEquals(listOf(1, 2, 3, 4), heard)
    }

    @Test(timeout = 10_000) fun failedFirstRequestDoesNotStartLookahead() {
        val started = mutableListOf<String>()
        val failure = assertThrows(IOException::class.java) {
            streamSegments(sequenceOf("A", "B"), CancellationToken(), { part, _ ->
                started += part
                throw IOException("rejected")
            }) { _, _ -> fail("unexpected audio") }
        }
        assertEquals("rejected", failure.message)
        assertEquals(listOf("A"), started)
    }

    @Test(timeout = 10_000) fun stoppingDisconnectsBothInflightReaders() {
        val stop = CancellationToken()
        val opened = CountDownLatch(2)
        val disconnected = CountDownLatch(2)
        val closed = CountDownLatch(2)
        assertThrows(IOException::class.java) {
            streamSegments(sequenceOf("A", "B", "C"), stop, { _, transfer ->
                val hook = transfer.onCancel { disconnected.countDown() }
                opened.countDown()
                object : InputStream() {
                    var first = true
                    override fun read(): Int = error("expected block read")
                    override fun read(b: ByteArray, off: Int, len: Int): Int {
                        if (first) { first = false; b[off] = 1; return 1 }
                        // Like a blocking socket read, interrupt alone is not sufficient.
                        while (disconnected.count != 0L) {
                            try { disconnected.await() } catch (_: InterruptedException) { }
                        }
                        return -1
                    }
                    override fun close() { hook.close(); closed.countDown() }
                }
            }) { _, input ->
                assertEquals(1, input.read())
                assertTrue(opened.await(5, TimeUnit.SECONDS))
                stop.cancel()
                input.read()
            }
        }
        assertTrue(disconnected.await(5, TimeUnit.SECONDS))
        assertTrue(closed.await(5, TimeUnit.SECONDS))
    }
}
