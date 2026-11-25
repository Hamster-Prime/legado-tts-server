package io.github.legadotts.client.service

import io.github.legadotts.client.data.ClientException
import java.io.DataInputStream
import java.io.EOFException
import java.io.InputStream

internal data class Mp3Frame(
    val bytes: ByteArray, val rate: Int, val channels: Int, val samples: Int,
)

/**
 * The chunked endpoint carries an MP3 elementary stream, not a seekable media file.
 * Frame it for the platform Layer III decoder; network packet boundaries are unrelated
 * to MPEG frame boundaries. No temporary files or local HTTP proxy.
 */
internal class Mp3Frames(stream: InputStream) {
    private val input = DataInputStream(stream.buffered(8192))
    private var frames = 0
    private val header = ByteArray(4)
    private val mpeg1 = intArrayOf(0,32,40,48,56,64,80,96,112,128,160,192,224,256,320)
    private val mpeg2 = intArrayOf(0,8,16,24,32,40,48,56,64,80,96,112,128,144,160)
    fun next(): Mp3Frame? {
        while (true) {
            val b0 = input.read()
            if (b0 < 0) {
                if (frames == 0) throw ClientException("服务返回了空音频")
                return null
            }
            header[0] = b0.toByte()
            input.readFully(header, 1, 3)
            if (header[0] == 'I'.code.toByte() && header[1] == 'D'.code.toByte() && header[2] == '3'.code.toByte()) {
                input.readUnsignedByte() // revision
                val flags = input.readUnsignedByte()
                var length = 0
                repeat(4) {
                    val byte = input.readUnsignedByte()
                    if (byte >= 128) throw ClientException("MP3 标签无效")
                    length = (length shl 7) or byte
                }
                if (length > 1_048_576) throw ClientException("MP3 标签过大")
                val footer = if (header[3].toInt() == 4 && flags and 16 != 0) 10 else 0
                var remaining = length + footer
                while (remaining > 0) {
                    val n = input.skipBytes(remaining)
                    if (n <= 0) throw EOFException()
                    remaining -= n
                }
                continue
            }
            // ID3v1 is a 128-byte trailer, never an audio frame.
            if (frames > 0 && header[0] == 84.toByte() && header[1] == 65.toByte() && header[2] == 71.toByte()) {
                input.readFully(ByteArray(124))
                return null
            }
            val b1 = header[1].toInt() and 255
            val b2 = header[2].toInt() and 255
            val version = (b1 ushr 3) and 3
            val layer = (b1 ushr 1) and 3
            val bitIndex = b2 ushr 4
            val freqIndex = (b2 ushr 2) and 3
            if (b0 != 255 || b1 and 0xe0 != 0xe0 || version == 1 || layer != 1 ||
                bitIndex !in 1..14 || freqIndex == 3) throw ClientException("服务未返回有效的 MP3 帧")
            val bitrate = if (version == 3) mpeg1[bitIndex] else mpeg2[bitIndex]
            val freq = when (freqIndex) { 0 -> 44100; 1 -> 48000; else -> 32000 } /
                when (version) { 3 -> 1; 2 -> 2; else -> 4 }
            val size = (if (version == 3) 144000 else 72000) * bitrate / freq + (b2 ushr 1 and 1)
            val bytes = ByteArray(size)
            header.copyInto(bytes)
            input.readFully(bytes, 4, size - 4)
            frames++
            return Mp3Frame(bytes, freq, if (header[3].toInt() and 0xc0 == 0xc0) 1 else 2,
                if (version == 3) 1152 else 576)
        }
    }
}
