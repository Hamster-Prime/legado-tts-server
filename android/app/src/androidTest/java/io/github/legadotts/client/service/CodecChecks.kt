package io.github.legadotts.client.service

import android.content.Context
import io.github.legadotts.client.data.CancellationToken
import java.io.ByteArrayInputStream

/** Device-only check against the actual platform MP3 decoder. The elementary stream is
 * generated in code: zero bit-reservoir offset, zero granule lengths and silent data.
 * No bundled audio recording, copyrighted book, network calls, or extra codec library. */
internal object CodecChecks {
    fun run(@Suppress("UNUSED_PARAMETER") context: Context) {
        val silence = ByteArray(144 * 64)
        for (offset in silence.indices step 144) {
            silence[offset] = 0xff.toByte() // MPEG-2 Layer III, 48 kb/s 24 kHz mono
            silence[offset + 1] = 0xf3.toByte()
            silence[offset + 2] = 0x64
            silence[offset + 3] = 0xc0.toByte()
        }
        var bytes = 0
        decodeMp3(ByteArrayInputStream(silence), CancellationToken()) { format, pcm ->
            check(format == PcmFormat(24000, 1))
            check(pcm.size % 2 == 0)
            bytes += pcm.size
        }
        check(bytes >= 576 * 2) { "设备未输出 PCM" }
    }
}
