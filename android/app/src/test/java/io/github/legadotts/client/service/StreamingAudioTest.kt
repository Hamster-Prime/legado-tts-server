package io.github.legadotts.client.service

import io.github.legadotts.client.ui.VoiceItem
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.IOException
import java.io.InputStream

class StreamingAudioTest {
    @Test fun chunksAreLazyShortFirstAndBoundedByCodePoints() {
        val input = "开头。第二句包含一个苹果🍎。".repeat(80)
        val parts = speechSegments(input, 73).toList()
        assertEquals(input, parts.joinToString(""))
        assertTrue(parts.first().codePointCount(0, parts.first().length) in 1..48)
        assertTrue(parts.all { it.codePointCount(0, it.length) in 1..73 && it.endsWith('。') })
        assertEquals(listOf("🍎", "𠮷", "字"), speechSegments("🍎𠮷字", 1).toList())
        assertEquals(emptyList<String>(), speechSegments("  \t\n", 2).toList())
        assertTrue(speechSegments("a".repeat(5000), 99999).all { it.length in 1..240 })
    }

    @Test fun streamingAndSingleUtteranceKeepNormalParagraphsWhole() {
        val paragraph = "这是一段正常长度的中文小说段落，大约有八十个字左右。如果被强行在四十八字处切成两段，听感上就会在段落中间多出一次网络请求与停顿。"
        assertEquals(listOf(paragraph), speechSegments(paragraph, 5000, provider = "tencent", singleUtterance = true).toList())
        assertEquals(listOf(paragraph), speechSegments(paragraph, 5000, provider = "edge", singleUtterance = false).toList())
        assertEquals(listOf(paragraph), speechSegments(paragraph, 5000, provider = "doubao", singleUtterance = false).toList())
    }

    @Test fun closingQuotesStayWithSentence() {
        val text = "他说：“走吧。”她点头。\n\n第二段开始，这里很长。"
        val parts = speechSegments(text, 10).toList()
        assertEquals("他说：“走吧。”", parts[0])
        assertEquals("她点头。", parts[1])
        assertTrue(parts.none { it.startsWith("”") })
        assertEquals(text.filterNot { it.isWhitespace() }, parts.joinToString(""))
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
