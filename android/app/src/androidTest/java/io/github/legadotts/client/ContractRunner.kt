package io.github.legadotts.client

import android.app.Activity
import android.app.Instrumentation
import android.content.Intent
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import io.github.legadotts.client.data.ClientSettings
import io.github.legadotts.client.data.Endpoint
import io.github.legadotts.client.data.SettingsChecks
import io.github.legadotts.client.service.CodecChecks
import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** Device integration checks using only the platform instrumentation API. */
class ContractRunner : Instrumentation() {
    private var args = Bundle()
    override fun onCreate(arguments: Bundle?) {
        super.onCreate(arguments)
        args = arguments ?: Bundle()
        start()
    }
    override fun onStart() {
        val results = mutableListOf<String>()
        var stage = ""
        fun step(name: String, run: () -> Unit) { stage = name; run(); results += "通过：$name" }
        val failure = runCatching {
            step("本机网络协议及长文本分段") { ContractChecks.offline() }
            step("Keystore 本机隔离存储") { SettingsChecks.run(targetContext) }
            step("设备 MP3 到 PCM 解码") { CodecChecks.run(targetContext) }
            step("系统 TTS 引擎发现与绑定") { bindEngine(args.getString("systemSynthesis") == "true") }
            val endpoint = when {
                args.getString("configured") == "true" ->
                    requireNotNull(ClientSettings(targetContext).endpoint())
                else -> args.getString("server")?.let { Endpoint.of(it) }
            }
            if (endpoint != null) step("真实服务端契约") {
                results += ContractChecks.real(endpoint, args.getString("synthesize") == "true")
            }
        }.exceptionOrNull()
        // Avoid dumping exceptions possibly raised by vendor implementations with private data.
        val report = if (failure == null) results + "OK (contract checks)" else
            results + "未通过：$stage（${failure.javaClass.simpleName}）" + "FAILURES!!!"
        finish(if (failure == null) Activity.RESULT_OK else Activity.RESULT_CANCELED,
            Bundle().apply { putString("stream", report.joinToString("\n", prefix = "\n", postfix = "\n")) })
    }
    private fun bindEngine(synthesize: Boolean) {
        val app = targetContext
        val query = Intent(TextToSpeech.Engine.INTENT_ACTION_TTS_SERVICE).setPackage(app.packageName)
        @Suppress("DEPRECATION")
        val info = app.packageManager.queryIntentServices(query, 0).single().serviceInfo
        check(info.exported)
        val init = CountDownLatch(1)
        val success = AtomicBoolean(false)
        val engine = TextToSpeech(app, { status ->
            success.set(status == TextToSpeech.SUCCESS); init.countDown()
        }, app.packageName)
        try {
            check(init.await(15, TimeUnit.SECONDS) && success.get())
            check(engine.voices.any { it.name == "legado-current-network" && it.isNetworkConnectionRequired })
            if (!synthesize) return
            // This opt-in check uses the selection set in the app, not command-line secrets.
            // Restrict to free Edge so a test can never charge a configured paid provider.
            check(ClientSettings(app).selectedVoice()?.provider == "edge")
            val done = CountDownLatch(1)
            val ok = AtomicBoolean(false)
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit
                override fun onDone(utteranceId: String?) { ok.set(true); done.countDown() }
                @Deprecated("Framework API") override fun onError(utteranceId: String?) { done.countDown() }
            })
            val file = File.createTempFile("tts-contract-", ".wav", app.cacheDir)
            try {
                check(engine.synthesizeToFile("朗读测试。", Bundle(), file, "tts-smoke") == TextToSpeech.SUCCESS)
                check(done.await(120, TimeUnit.SECONDS) && ok.get())
                check(file.length() > 44)
                check(file.inputStream().use {
                    val head = ByteArray(12)
                    java.io.DataInputStream(it).readFully(head)
                    String(head, 0, 4, Charsets.US_ASCII) == "RIFF" &&
                        String(head, 8, 4, Charsets.US_ASCII) == "WAVE"
                })
            } finally { file.delete() }
        } finally { engine.shutdown() }
    }
}
