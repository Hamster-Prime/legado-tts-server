package io.github.legadotts.client.service

import io.github.legadotts.client.ui.VoiceItem
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream

class StreamingAudioTest {
    private fun texts(text: String, max: Int, streaming: Boolean = false, single: Boolean = false) =
        speechSegments(text, max, streaming = streaming, singleUtterance = single).map { it.text }.toList()

    @Test fun chunksAreLazyShortFirstAndBoundedByCodePoints() {
        val input = "开头。第二句包含一个苹果🍎。".repeat(80)
        val parts = texts(input, 73)
        assertEquals(input, parts.joinToString(""))
        assertTrue(parts.first().codePointCount(0, parts.first().length) in 1..48)
        assertTrue(parts.all { it.codePointCount(0, it.length) in 1..73 && it.endsWith('。') })
        assertEquals(listOf("🍎", "𠮷", "字"), texts("🍎𠮷字", 1))
        assertEquals(emptyList<String>(), texts("  \t\n", 2))
        assertTrue(texts("a".repeat(5000), 99999).all { it.length in 1..240 })
    }

    @Test fun streamingAndSingleUtteranceAvoidUnnecessarySplits() {
        val paragraph = "这是一段正常长度的中文小说段落，大约有八十个字左右。如果被强行在四十八字处切成两段，听感上就会在段落中间多出一次网络请求与明显停顿，这在听书时会很突兀。"
        // Single-utterance mode (e.g. Legado via SystemSpeechService) keeps normal paragraphs whole.
        val singleBuffered = texts(paragraph, 5000, streaming = false, single = true)
        assertEquals(listOf(paragraph), singleBuffered)
        val singleStreaming = texts(paragraph, 5000, streaming = true, single = true)
        assertEquals(listOf(paragraph), singleStreaming)
        // Streaming providers in App reader also keep up to 200 code points in the first request.
        val appStreaming = texts(paragraph, 5000, streaming = true, single = false)
        assertEquals(listOf(paragraph), appStreaming)
    }

    @Test fun boundaryPausesFollowPunctuationAndKeepClosingQuotes() {
        val text = "他说：“走吧。”她点头。\n\n第二段开始，这里很长" + "很长".repeat(40) + "，结尾"
        val parts = speechSegments(text, 10).toList()
        assertEquals("他说：“走吧。”", parts[0].text)
        assertEquals(Pauses.SENTENCE, parts[0].pauseMs)
        assertEquals("她点头。", parts[1].text)
        assertEquals(Pauses.PARAGRAPH, parts[1].pauseMs)
        assertEquals("第二段开始，", parts[2].text)
        assertEquals(Pauses.COMMA, parts[2].pauseMs)
        assertTrue(parts.none { it.text.startsWith("”") })
        assertEquals(text.filterNot { it.isWhitespace() }, parts.joinToString("") { it.text })
    }

    private fun pcm(vararg runs: Pair<Int, Int>): ByteArray {
        // (frames, amplitude) runs of 16-bit mono PCM.
        val out = ByteArrayOutputStream()
        for ((frames, amp) in runs) repeat(frames) { i ->
            val v = if (amp == 0) 0 else if (i % 2 == 0) amp else -amp
            out.write(v and 0xff); out.write((v shr 8) and 0xff)
        }
        return out.toByteArray()
    }

    @Test fun joinerReplacesPaddingAtBoundariesAndKeepsNormalInnerPauses() {
        val f = PcmFormat(1000, 1) // 1 frame = 1 ms, window = 10 ms (10 frames)
        val out = ByteArrayOutputStream()
        val joiner = SpeechJoiner { format, bytes -> assertEquals(f, format); out.write(bytes) }
        // Segment 1: 300 ms lead pad, 100 ms speech, 200 ms normal inner pause, 100 ms speech, 900 ms tail pad.
        joiner.beginSegment()
        joiner.write(f, pcm(300 to 0, 100 to 5000, 200 to 0, 100 to 5000))
        // Also add low-level room tone (amp 80 < threshold) in trailing silence — must still be trimmed!
        joiner.write(f, pcm(900 to 80))
        joiner.endSegment(Pauses.SENTENCE)
        // Segment 2: 250 ms lead pad (with a 1-sample click), 100 ms speech, 600 ms tail pad.
        joiner.beginSegment()
        joiner.write(f, pcm(120 to 0, 1 to 600, 129 to 0, 100 to 5000, 600 to 0))
        joiner.endSegment(Pauses.SENTENCE)
        joiner.finish()
        val ms = out.size() / 2
        val lead = SpeechJoiner.LEAD_KEEP_MS
        val tail = SpeechJoiner.TAIL_KEEP_MS
        // lead + speech + inner pause + speech + exact SENTENCE pause + speech + tail
        assertEquals(lead + 100 + 200 + 100 + Pauses.SENTENCE + 100 + tail, ms)
    }

    @Test fun joinerCapsExcessiveIntraSegmentDeadAir() {
        val f = PcmFormat(1000, 1)
        val out = ByteArrayOutputStream()
        val joiner = SpeechJoiner(maxInnerSilenceMs = 460) { _, bytes -> out.write(bytes) }
        // Provider emitted 1200 ms of dead air inside one request between two paragraphs.
        joiner.beginSegment()
        joiner.write(f, pcm(200 to 0, 100 to 5000, 1200 to 0, 100 to 5000, 500 to 0))
        joiner.endSegment(Pauses.PARAGRAPH)
        joiner.finish()
        val ms = out.size() / 2
        assertEquals(SpeechJoiner.LEAD_KEEP_MS + 100 + 460 + 100 + SpeechJoiner.TAIL_KEEP_MS, ms)
    }

    @Test fun joinerEmitsSomethingForSilentInput() {
        val f = PcmFormat(1000, 1)
        var total = 0
        val joiner = SpeechJoiner { _, bytes -> total += bytes.size }
        joiner.beginSegment()
        joiner.write(f, pcm(500 to 0))
        joiner.endSegment(Pauses.SENTENCE)
        joiner.finish()
        assertEquals(SpeechJoiner.EMPTY_UTTERANCE_MS * 2, total)
    }

    @Test fun voiceItemSplitsDisplayNameAndTag() {
        val edge = VoiceItem("edge", "zh-CN-XiaoxiaoNeural", "晓晓 - 女声")
        assertEquals("晓晓", edge.displayName)
        assertEquals("女声", edge.tag)
        val doubao = VoiceItem("doubao", "zh_female_cancan_uranus_bigtts", "知性灿灿 2.0")
        assertEquals("知性灿灿 2.0", doubao.displayName)
        assertEquals("", doubao.tag)
    }

    private fun mp3Frame() = ByteArray(144).also {
        // MPEG-2 Layer III, no CRC, 48 kb/s, 24 kHz, mono, no padding.
        it[0] = 0xff.toByte(); it[1] = 0xf3.toByte(); it[2] = 0x64; it[3] = 0xc0.toByte()
    }

    @Test fun returnsFirstFrameBeforeEndOfResponseAndSkipsId3() {
        val id3 = "ID3".toByteArray() + byteArrayOf(4, 0, 0, 0, 0, 0, 3, 11, 22, 33)
        val data = id3 + mp3Frame()
        // Upstream remains open and intentionally refuses further bytes: no need to
        // fetch EOF or the second frame before offering the first frame to MediaCodec.
        val fragmented = object : InputStream() {
            var index = 0
            override fun read(): Int = if (index < data.size) data[index++].toInt() and 255 else error("过度读取")
            override fun read(b: ByteArray, off: Int, len: Int): Int {
                val n = minOf(7, len, data.size - index)
                if (n == 0) error("过度读取")
                data.copyInto(b, off, index, index + n)
                index += n
                return n
            }
        }
        val first = Mp3Frames(fragmented).next()!!
        assertEquals(24000, first.rate)
        assertEquals(1, first.channels)
        assertEquals(576, first.samples)
        assertArrayEquals(mp3Frame(), first.bytes)
    }

    @Test fun reportsTruncatedPayloadAndMalformedAudioAsErrors() {
        for (bad in listOf(byteArrayOf(), "<html>".toByteArray(), mp3Frame().copyOf(17))) {
            assertThrows(IOException::class.java) { Mp3Frames(ByteArrayInputStream(bad)).next() }
        }
        val reader = Mp3Frames(ByteArrayInputStream(mp3Frame() + mp3Frame()))
        assertNotNull(reader.next())
        assertNotNull(reader.next())
        assertNull(reader.next())
    }
}
