package io.github.legadotts.client.service

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.media.AudioFormat
import android.os.Bundle
import android.speech.tts.*
import io.github.legadotts.client.data.ClientSettings
import io.github.legadotts.client.data.ClientApi
import io.github.legadotts.client.data.CancellationToken
import java.util.Locale
import java.util.concurrent.atomic.AtomicReference
import kotlin.math.roundToInt

private fun selectedLocale(context: Context): Locale {
    val voice = runCatching { ClientSettings(context).selectedVoice() }.getOrNull()
    if (voice?.provider == "edge") {
        // zh-CN-XiaoxiaoNeural, en-US-AriaNeural, etc. The curated catalogs for other
        // providers have no machine-readable locale; advertise Chinese rather than all locales.
        val parts = voice.id.split('-')
        if (parts.size >= 3 && parts[0].length in 2..3 && parts[1].length == 2) {
            Locale.forLanguageTag(parts.take(2).joinToString("-")).let {
                if (runCatching { it.isO3Language.isNotEmpty() && it.isO3Country.isNotEmpty() }
                        .getOrDefault(false)) return it
            }
        }
    }
    return Locale.SIMPLIFIED_CHINESE
}

private fun localeFields(locale: Locale) = arrayOf(locale.isO3Language, locale.isO3Country, "")

/**
 * Android owns playback (or file synthesis) here. The engine never plays its own
 * AudioTrack, and never passes MP3 headers/data directly to a PCM callback.
 */
class SystemSpeechService : TextToSpeechService() {
    private val active = AtomicReference<CancellationToken?>()
    override fun onGetLanguage() = localeFields(selectedLocale(this))
    override fun onIsLanguageAvailable(language: String?, country: String?, variant: String?): Int {
        val loc = selectedLocale(this)
        if (!language.equals(loc.isO3Language, true) && !language.equals(loc.language, true))
            return TextToSpeech.LANG_NOT_SUPPORTED
        return if (!country.isNullOrBlank() && (country.equals(loc.isO3Country, true) ||
            country.equals(loc.country, true))) TextToSpeech.LANG_COUNTRY_AVAILABLE else TextToSpeech.LANG_AVAILABLE
    }
    override fun onLoadLanguage(language: String?, country: String?, variant: String?) =
        onIsLanguageAvailable(language, country, variant)
    override fun onGetFeaturesForLanguage(lang: String?, country: String?, variant: String?) =
        mutableSetOf(TextToSpeech.Engine.KEY_FEATURE_NETWORK_SYNTHESIS)
    override fun onGetDefaultVoiceNameFor(lang: String?, country: String?, variant: String?): String? =
        if (onIsLanguageAvailable(lang, country, variant) >= TextToSpeech.LANG_AVAILABLE) VOICE else null
    override fun onGetVoices(): MutableList<android.speech.tts.Voice> = mutableListOf(
        android.speech.tts.Voice(VOICE, selectedLocale(this), android.speech.tts.Voice.QUALITY_NORMAL,
            android.speech.tts.Voice.LATENCY_HIGH, true, setOf(TextToSpeech.Engine.KEY_FEATURE_NETWORK_SYNTHESIS)))
    override fun onIsValidVoiceName(voiceName: String?) =
        if (voiceName == VOICE) TextToSpeech.SUCCESS else TextToSpeech.ERROR
    override fun onLoadVoice(voiceName: String?) = onIsValidVoiceName(voiceName)

    override fun onSynthesizeText(request: SynthesisRequest, callback: SynthesisCallback) {
        val token = CancellationToken()
        active.set(token)
        try {
            val settings = ClientSettings(applicationContext)
            val endpoint = settings.endpoint()
            val voice = settings.selectedVoice()
            if (voice == null || endpoint == null) {
                callback.error(TextToSpeech.ERROR_SERVICE)
                return
            }
            val text = request.charSequenceText.toString()
            if (text.isBlank()) {
                if (callback.start(24000, AudioFormat.ENCODING_PCM_16BIT, 1) == TextToSpeech.SUCCESS) callback.done()
                return
            }
            val speed = (settings.speed() * request.speechRate / 100f).coerceIn(.25f, 4f)
            var format: PcmFormat? = null
            streamSpeech(ClientApi(endpoint), text, voice, speed, false, token, singleUtterance = true) { actual, pcm ->
                token.check()
                if (format == null) {
                    format = actual
                    val rate = if (voice.provider == "fishaudio") (actual.rate * speed).roundToInt() else actual.rate
                    require(callback.start(rate, AudioFormat.ENCODING_PCM_16BIT, actual.channels) == TextToSpeech.SUCCESS)
                }
                require(format == actual)
                var offset = 0
                val frame = actual.channels * 2
                val max = (callback.maxBufferSize / frame) * frame
                require(max >= frame)
                while (offset < pcm.size) {
                    token.check()
                    val n = minOf(pcm.size - offset, max)
                    if (callback.audioAvailable(pcm, offset, n) != TextToSpeech.SUCCESS)
                        throw IllegalStateException("系统已终止朗读")
                    offset += n
                }
            }
            token.check()
            if (format == null) callback.error(TextToSpeech.ERROR_SYNTHESIS) else callback.done()
        } catch (e: Exception) {
            if (!token.isCancelled()) callback.error(when (e) {
                is java.net.SocketTimeoutException -> TextToSpeech.ERROR_NETWORK_TIMEOUT
                is java.io.IOException -> TextToSpeech.ERROR_NETWORK
                else -> TextToSpeech.ERROR_SYNTHESIS
            })
        } finally {
            // Framework file and playback callbacks need the final done after error()
            // as well. Skip it when onStop has already cancelled this request.
            if (!token.isCancelled() && !callback.hasFinished()) callback.done()
            active.compareAndSet(token, null)
            token.cancel()
        }
    }
    override fun onStop() { active.getAndSet(null)?.cancel() }
    override fun onDestroy() { onStop(); super.onDestroy() }
    companion object { private const val VOICE = "legado-current-network" }
}

/** Legacy TTS settings probes. No upstream calls and no disclosure of service credentials. */
class EngineInfoActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val result = Intent()
        when (intent.action) {
            TextToSpeech.Engine.ACTION_CHECK_TTS_DATA -> {
                val loc = selectedLocale(this)
                val tag = "${loc.isO3Language}-${loc.isO3Country}"
                val ready = runCatching { ClientSettings(this).selectedVoice() != null }.getOrDefault(false)
                result.putStringArrayListExtra(TextToSpeech.Engine.EXTRA_AVAILABLE_VOICES,
                    if (ready) arrayListOf(tag) else arrayListOf())
                result.putStringArrayListExtra(TextToSpeech.Engine.EXTRA_UNAVAILABLE_VOICES,
                    if (ready) arrayListOf() else arrayListOf(tag))
                setResult(if (ready) TextToSpeech.Engine.CHECK_VOICE_DATA_PASS else TextToSpeech.Engine.CHECK_VOICE_DATA_MISSING_DATA, result)
            }
            TextToSpeech.Engine.ACTION_GET_SAMPLE_TEXT -> setResult(TextToSpeech.LANG_AVAILABLE,
                result.putExtra(TextToSpeech.Engine.EXTRA_SAMPLE_TEXT, "你好，正在使用服务端音色朗读这段示例文字。"))
            else -> setResult(RESULT_CANCELED)
        }
        finish()
    }
}
