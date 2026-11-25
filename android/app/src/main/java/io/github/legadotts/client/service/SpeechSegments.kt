package io.github.legadotts.client.service

private const val SENTENCE_MARKS = "。！？!?\n；;…"
private const val WORD_MARKS = "、，,：:"
/** Closing quotes/brackets belong to the sentence they close, never to the next request. */
private const val CLOSERS = "”’\"'）)】」』》〉］]｝}"

/**
 * Work on Unicode code points (the server uses Python len); do not split a surrogate pair.
 *
 * - For buffered providers (default), the first request stays short (<= 48 code points) for fast
 *   first audio, then expands so there are fewer request boundaries in long text.
 * - For true-streaming providers (Edge, Volcengine), larger chunks stream continuously inside one
 *   request without creating artificial paragraph boundaries.
 * - For single-utterance system TTS requests (e.g. Legado reading paragraph by paragraph), normal
 *   paragraphs stay whole instead of being split at 48 code points.
 */
internal fun speechSegments(
    text: String,
    serverMax: Int,
    provider: String? = null,
    singleUtterance: Boolean = false,
): Sequence<String> = sequence {
    require(serverMax > 0)
    var start = 0
    var emitted = 0
    while (start < text.length) {
        val targetLimit = when {
            singleUtterance -> when (provider) {
                "edge" -> 1200
                "doubao" -> 480
                else -> 280
            }
            provider == "edge" -> 1200
            provider == "doubao" -> 480
            else -> when (emitted) { 0 -> 48; 1 -> 160; else -> 240 }
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
            val minReasonable = minOf(12, limit / 3)
            end = when {
                sentence > start && (sentenceCount >= minReasonable || word <= sentence) -> sentence
                word > start -> word
                sentence > start -> sentence
                else -> end
            }
        }
        val chunk = text.substring(start, end).trim()
        start = end
        if (chunk.isNotEmpty()) {
            emitted++
            yield(chunk)
        }
    }
}
