package io.github.legadotts.client.service

import kotlin.math.abs

/**
 * Joins the PCM of consecutive synthesis requests without the dead air between them.
 *
 * Every provider pads each response with leading and trailing silence (often 500-850 ms
 * at the tail plus 100-300 ms at the head), and MP3 encoders add priming/padding frames.
 * Played back to back, those pads stack into a long dead-air gap every time a request ends.
 *
 * This class evaluates 10 ms energy windows (immune to single-sample MP3 glitches and low
 * vocoder room-tone), trims excess leading/trailing pads with smooth anti-pop micro-fades,
 * inserts an exact punctuation-matched pause at segment boundaries, and gently caps
 * abnormally long dead silence inside a single response while leaving natural speech
 * pauses completely untouched.
 *
 * 16-bit little-endian interleaved PCM only. Not thread safe: one decoding thread.
 */
internal class SpeechJoiner(
    private val maxInnerSilenceMs: Int = DEFAULT_MAX_INNER_SILENCE_MS,
    private val sink: (PcmFormat, ByteArray) -> Unit,
) {
    private var format: PcmFormat? = null
    private var frameBytes = 2
    private var windowBytes = 2

    /** Voiced audio has already been emitted for this utterance. */
    private var started = false
    /** Voiced audio has been seen in the current segment. */
    private var voiced = false
    /** Exact pause owed before the next segment's voiced audio, in ms. */
    private var owedPauseMs = 0
    /** Tail bytes already emitted after the previous segment's last voiced window. */
    private var keptTailBytes = 0
    /** True when an intra-segment silence exceeded [maxInnerSilenceMs] and is being trimmed. */
    private var innerTrimmed = false

    /** Partial < 10 ms remainder across consecutive decoder output buffers. */
    private val pending = Buffer()
    /** Latest silence before the first voiced window (or after an inner silence trim). */
    private var lead = ByteArray(0)
    /** Silence accumulated after the latest voiced window; flushed verbatim if speech resumes soon. */
    private val hold = Buffer()
    private val out = Buffer()

    fun beginSegment() {
        voiced = false
        innerTrimmed = false
        pending.clear()
        lead = ByteArray(0)
        hold.clear()
    }

    fun write(f: PcmFormat, pcm: ByteArray) {
        if (format == null) {
            format = f
            frameBytes = 2 * f.channels
            windowBytes = maxOf(1, f.rate * WINDOW_MS / 1000) * frameBytes
        }
        val fmt = format!!
        if (fmt != f) {
            flushOut(fmt)
            sink(f, pcm)
            return
        }
        val alignedLen = pcm.size - pcm.size % frameBytes
        if (alignedLen <= 0) return

        // Combine with any partial window from the previous buffer.
        val data: ByteArray
        val totalLen: Int
        if (pending.size > 0) {
            pending.append(pcm, 0, alignedLen)
            data = pending.bytes
            totalLen = pending.size
        } else {
            data = pcm
            totalLen = alignedLen
        }

        val usable = totalLen - totalLen % windowBytes
        var offset = 0
        while (offset < usable) {
            processWindow(data, offset, windowBytes)
            offset += windowBytes
        }

        val rem = totalLen - usable
        if (pending.size > 0) {
            if (rem > 0) data.copyInto(pending.bytes, 0, usable, totalLen)
            pending.size = rem
        } else if (rem > 0) {
            pending.append(pcm, usable, rem)
        }

        flushOut(fmt)
    }

    /** [pauseMs] is owed before the next segment's speech, if any follows. */
    fun endSegment(pauseMs: Int) {
        val fmt = format ?: return
        if (pending.size >= frameBytes) {
            processWindow(pending.bytes, 0, pending.size - pending.size % frameBytes)
        }
        pending.clear()

        if (voiced) {
            if (!innerTrimmed) {
                val tailCap = bytesFor(TAIL_KEEP_MS)
                val keep = minOf(hold.size, tailCap)
                if (keep > 0) {
                    val startOut = out.size
                    out.append(hold.bytes, 0, keep)
                    // Fade out if we trimmed trailing silence after this point.
                    if (hold.size > keep) fadeOut(out.bytes, startOut, keep, bytesFor(FADE_OUT_MS))
                }
                keptTailBytes = keep
            }
            // When innerTrimmed is already true, TAIL_KEEP_MS was already emitted with fade-out
            // and recorded in keptTailBytes, so the next segment receives the exact remaining gap.
            owedPauseMs = pauseMs
        } else {
            owedPauseMs = maxOf(owedPauseMs, pauseMs)
        }
        hold.clear()
        lead = ByteArray(0)
        voiced = false
        innerTrimmed = false
        flushOut(fmt)
    }

    /** Called after the last segment. Guarantees valid output even if the input was all silence. */
    fun finish() {
        val fmt = format ?: return
        if (!started) {
            out.zeros(bytesFor(EMPTY_UTTERANCE_MS))
            started = true
        }
        flushOut(fmt)
    }

    private fun processWindow(buf: ByteArray, offset: Int, len: Int) {
        val leadLimit = bytesFor(LEAD_KEEP_MS)
        val innerCap = bytesFor(maxInnerSilenceMs)
        val loud = isWindowLoud(buf, offset, len)
        if (loud) {
            if (!voiced) {
                voiced = true
                if (started && owedPauseMs > 0) {
                    val gapBytes = maxOf(0, bytesFor(owedPauseMs) - keptTailBytes - lead.size)
                    out.zeros(gapBytes)
                }
                emitLeadWithFade()
                owedPauseMs = 0
                keptTailBytes = 0
                started = true
            } else if (innerTrimmed) {
                // Speech resumed after an abnormally long intra-segment silence.
                val targetGap = maxOf(0, innerCap - keptTailBytes - lead.size)
                out.zeros(targetGap)
                emitLeadWithFade()
                innerTrimmed = false
                keptTailBytes = 0
            } else if (hold.size > 0) {
                // Normal intra-segment pause: emit completely unchanged.
                out.append(hold.bytes, 0, hold.size)
                hold.clear()
            }
            out.append(buf, offset, len)
        } else if (!voiced) {
            lead = keepLast(lead, buf, offset, len, leadLimit)
        } else if (innerTrimmed) {
            // Keep rolling onset window so speech resuming after long silence stays smooth.
            lead = keepLast(lead, buf, offset, len, leadLimit)
        } else {
            hold.append(buf, offset, len)
            if (maxInnerSilenceMs > 0 && hold.size >= innerCap) {
                // Emit natural decay tail now, and hold further silence until speech resumes.
                val tailCap = bytesFor(TAIL_KEEP_MS)
                val keep = minOf(hold.size, tailCap)
                if (keep > 0) {
                    val startOut = out.size
                    out.append(hold.bytes, 0, keep)
                    fadeOut(out.bytes, startOut, keep, bytesFor(FADE_OUT_MS))
                }
                keptTailBytes = keep
                lead = keepLast(ByteArray(0), hold.bytes, 0, hold.size, leadLimit)
                hold.clear()
                innerTrimmed = true
            }
        }
    }

    private fun emitLeadWithFade() {
        if (lead.isEmpty()) return
        val startOut = out.size
        out.append(lead, 0, lead.size)
        fadeIn(out.bytes, startOut, lead.size, bytesFor(FADE_IN_MS))
        lead = ByteArray(0)
    }

    private fun fadeIn(buf: ByteArray, offset: Int, len: Int, maxFadeBytes: Int) {
        val fadeFrames = minOf(len, maxFadeBytes) / frameBytes
        if (fadeFrames <= 1) return
        val channels = frameBytes / 2
        for (frame in 0 until fadeFrames) {
            val scale = frame.toFloat() / fadeFrames
            val base = offset + frame * frameBytes
            for (ch in 0 until channels) {
                val idx = base + ch * 2
                val sample = ((buf[idx].toInt() and 0xff) or (buf[idx + 1].toInt() shl 8)).toShort().toInt()
                val scaled = (sample * scale).toInt().coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
                buf[idx] = (scaled and 0xff).toByte()
                buf[idx + 1] = ((scaled ushr 8) and 0xff).toByte()
            }
        }
    }

    private fun fadeOut(buf: ByteArray, offset: Int, len: Int, maxFadeBytes: Int) {
        val fadeFrames = minOf(len, maxFadeBytes) / frameBytes
        if (fadeFrames <= 1) return
        val channels = frameBytes / 2
        val startFrame = len / frameBytes - fadeFrames
        for (i in 0 until fadeFrames) {
            val scale = (fadeFrames - 1 - i).toFloat() / fadeFrames
            val base = offset + (startFrame + i) * frameBytes
            for (ch in 0 until channels) {
                val idx = base + ch * 2
                val sample = ((buf[idx].toInt() and 0xff) or (buf[idx + 1].toInt() shl 8)).toShort().toInt()
                val scaled = (sample * scale).toInt().coerceIn(Short.MIN_VALUE.toInt(), Short.MAX_VALUE.toInt())
                buf[idx] = (scaled and 0xff).toByte()
                buf[idx + 1] = ((scaled ushr 8) and 0xff).toByte()
            }
        }
    }

    private fun isWindowLoud(buf: ByteArray, offset: Int, len: Int): Boolean {
        var sum = 0L
        var peak = 0
        var samples = 0
        var i = offset
        val end = offset + len
        while (i + 1 < end) {
            val raw = (buf[i].toInt() and 0xff) or (buf[i + 1].toInt() shl 8)
            val amp = abs(raw.toShort().toInt())
            sum += amp
            if (amp > peak) peak = amp
            samples++
            i += 2
        }
        if (samples == 0) return false
        val meanAbs = sum / samples
        return meanAbs >= MEAN_THRESHOLD || (peak >= PEAK_THRESHOLD && meanAbs >= SOFT_MEAN_THRESHOLD)
    }

    private fun flushOut(f: PcmFormat) {
        if (out.size == 0) return
        val bytes = out.bytes.copyOf(out.size)
        out.clear()
        sink(f, bytes)
    }

    private fun bytesFor(ms: Int): Int {
        val f = format ?: return 0
        return (f.rate.toLong() * ms / 1000).toInt() * frameBytes
    }

    private fun keepLast(old: ByteArray, src: ByteArray, off: Int, len: Int, limit: Int): ByteArray {
        if (limit <= 0) return ByteArray(0)
        if (len >= limit) return src.copyOfRange(off + len - limit, off + len)
        val total = minOf(limit, old.size + len)
        val result = ByteArray(total)
        val fromOld = total - len
        old.copyInto(result, 0, old.size - fromOld, old.size)
        src.copyInto(result, fromOld, off, off + len)
        return result
    }

    private class Buffer {
        var bytes = ByteArray(8192)
        var size = 0
        fun clear() { size = 0 }
        private fun ensure(extra: Int) {
            if (size + extra > bytes.size) bytes = bytes.copyOf(maxOf(size + extra, bytes.size * 2))
        }
        fun append(src: ByteArray, off: Int, len: Int) {
            if (len <= 0) return
            ensure(len)
            src.copyInto(bytes, size, off, off + len)
            size += len
        }
        fun zeros(len: Int) {
            if (len <= 0) return
            ensure(len)
            bytes.fill(0, size, size + len)
            size += len
        }
    }

    companion object {
        const val WINDOW_MS = 10
        /** Mean absolute amplitude (~ -44 dBFS) for steady speech / fricatives. */
        const val MEAN_THRESHOLD = 200L
        /** Peak + softer mean catches brief plosive onsets while rejecting single-sample clicks. */
        const val PEAK_THRESHOLD = 500
        const val SOFT_MEAN_THRESHOLD = 100L
        const val LEAD_KEEP_MS = 20
        const val TAIL_KEEP_MS = 40
        const val FADE_IN_MS = 5
        const val FADE_OUT_MS = 10
        /** Cap abnormally long dead silence inside a single provider response (~normal paragraph breath). */
        const val DEFAULT_MAX_INNER_SILENCE_MS = 460
        const val EMPTY_UTTERANCE_MS = 60
    }
}
