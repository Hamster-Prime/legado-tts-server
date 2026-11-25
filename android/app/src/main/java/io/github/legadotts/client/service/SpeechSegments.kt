package io.github.legadotts.client.service

/**
 * One synthesis request. [pauseMs] is the silence that should follow it at 1x speed,
 * matched to the punctuation (or paragraph break) the text was split at.
 */
internal data class SpeechSegment(val text: String, val pauseMs: Int)

internal object Pauses {
    const val PARAGRAPH = 360
    const val SENTENCE = 260
    const val CLAUSE = 180
    const val COMMA = 120
    const val WORD = 40
}

private const val SENTENCE_MARKS = "。！？!?\n；;…"
private const val WORD_MARKS = "、，,：:"
/** Closing quotes/brackets belong to the sentence they close, never to the next request. */
private const val CLOSERS = "”’\"'）)】」』》〉］]｝}"

private fun pauseAfter(chunk: String, paragraph: Boolean): Int {
    if (paragraph) return Pauses.PARAGRAPH
    val last = chunk.trimEnd { it in CLOSERS }.lastOrNull() ?: return Pauses.WORD
    return when (last) {
        '。', '！', '？', '!', '?', '.', '…' -> Pauses.SENTENCE
        '；', ';', '：', ':' -> Pauses.CLAUSE
        '，', ',', '、' -> Pauses.COMMA
        else -> Pauses.WORD
    }
}

/**
 * Work on Unicode code points (the server uses Python len); do not split a surrogate pair.
 *
 * - For buffered providers (default), the first request stays short (<= 48 code points) for fast
 *   first audio, then expands so there are fewer request boundaries in long text.
 * - For true-streaming providers (Edge, Volcengine), larger chunks stream from the first sentence
 *   with far fewer cross-request boundaries.
 * - For single-utterance system TTS requests (e.g. Legado reading paragraph by paragraph), normal
 *   paragraphs stay in one request instead of being chopped in half.
 */
internal fun speechSegments(
    text: String,
    serverMax: Int,
    streaming: Boolean = false,
    singleUtterance: Boolean = false,
): Sequence<SpeechSegment> = sequence {
    require(serverMax > 0)
    var start = 0
    var emitted = 0
    while (start < text.length) {
        val targetLimit = when {
            singleUtterance -> if (streaming) 480 else 280
            streaming -> if (emitted == 0) 200 else 420
            else -> when (emitted) { 0 -> 48; 1 -> 140; else -> 240 }
        }
        val limit = minOf(serverMax, targetLimit)
        var end = start
        var sentence = -1
        var sentenceCount = 0
        var word = -1
        var count = 0
        while (end < text.length && count < limit) {
            val cp = text.codePointAt(end)
            val before = end
            end += Character.charCount(cp)
            count++
            val c = cp.toChar()
            when {
                cp < 0x10000 && c in CLOSERS && (sentence == before || word == before) -> {
                    if (sentence == before) { sentence = end; sentenceCount = count }
                    else word = end
                }
                cp < 0x10000 && c in SENTENCE_MARKS || (cp == '.'.code &&
                    (end == text.length || text[end].isWhitespace())) -> {
                    sentence = end
                    sentenceCount = count
                }
                Character.isWhitespace(cp) || cp < 0x10000 && c in WORD_MARKS -> word = end
            }
        }
        if (end != text.length) {
            // Avoid an ultra-short stub (e.g. a 3-char chapter heading) when a later word boundary fits.
            val minReasonable = minOf(12, limit / 3)
            end = when {
                sentence > start && (sentenceCount >= minReasonable || word <= sentence) -> sentence
                word > start -> word
                sentence > start -> sentence
                else -> end
            }
        }
        val raw = text.substring(start, end)
        var paragraph = raw.trimEnd { it == ' ' || it == '\t' || it == '\u3000' }.endsWith('\n')
        var next = end
        while (next < text.length && text[next].isWhitespace()) {
            if (text[next] == '\n') paragraph = true
            next++
        }
        val chunk = raw.trim()
        start = end
        if (chunk.isNotEmpty()) {
            emitted++
            yield(SpeechSegment(chunk, pauseAfter(chunk, paragraph)))
        }
    }
}
