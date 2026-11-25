package io.github.legadotts.client.data

import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.io.FilterInputStream
import java.io.IOException
import java.io.InputStream
import java.io.InterruptedIOException
import java.net.HttpURLConnection
import java.net.Proxy
import java.net.URI
import java.net.URL
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.roundToInt

/** Controlled, user-facing I/O error whose message is safe to display verbatim in UI. */
open class ClientException(message: String) : IOException(message)

/** Origin + optional reverse proxy path. Never serialize the credential-bearing object. */
class Endpoint private constructor(val base: String, val key: String) {
    companion object {
        fun of(base: String, key: String = ""): Endpoint {
            val address = base.trim()
            val uri = try { URI(address) } catch (_: Exception) { null }
            require(uri != null && uri.scheme in setOf("http", "https") &&
                !uri.host.isNullOrEmpty() && uri.port in -1..65535 && uri.port != 0 &&
                uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null &&
                uri.normalize().rawPath == uri.rawPath) {
                "请输入完整的 HTTP(S) 服务地址，不要包含账号、查询参数或片段"
            }
            val secret = key.trim()
            require(secret.isEmpty() || uri.scheme == "https") { "携带密钥时必须使用 HTTPS" }
            require(secret.all { it.code in 0x20..0x7e }) { "密钥包含不支持的字符" }
            return Endpoint(address.trimEnd('/'), secret)
        }
    }
    override fun toString() = "Endpoint(已隐藏)"
}

data class Voice(val provider: String, val id: String, val name: String)

/** One token per operation/run; multiple concurrent prefetched requests can share it. */
class CancellationToken {
    @Volatile private var cancelled = false
    private val hooks = mutableSetOf<() -> Unit>()
    fun cancel() {
        val pending = synchronized(hooks) {
            if (cancelled) return
            cancelled = true
            hooks.toList().also { hooks.clear() }
        }
        pending.forEach { runCatching(it) }
    }
    fun isCancelled() = cancelled
    fun check() { if (cancelled) throw InterruptedIOException("操作已取消") }

    fun onCancel(action: () -> Unit): Closeable {
        synchronized(hooks) {
            if (!cancelled) {
                hooks += action
                return Closeable { synchronized(hooks) { hooks.remove(action) } }
            }
        }
        action()
        return Closeable { }
    }
}

/** Blocking I/O; run off the main thread. No API key, text, or response body is logged. */
class ClientApi(private val endpoint: Endpoint) {
    class ApiException(val status: Int) : ClientException(when (status) {
        401, 403 -> "认证失败，请检查服务访问密钥（HTTP $status）"
        429 -> "请求过于频繁或已达到服务配额（HTTP 429）"
        in 300..399 -> "已拒绝重定向，请直接填写服务的最终地址（HTTP $status）"
        400 -> "请求被拒绝，请检查音色及单段文本长度（HTTP 400）"
        404 -> "服务接口不存在，请检查地址与反向代理路径（HTTP 404）"
        in 500..599 -> "合成服务或上游暂时不可用（HTTP $status）"
        else -> "服务返回了意外的状态（HTTP $status）"
    })

    /** Check authentication without touching a provider, consuming character quota, or sending reader text. */
    fun probe(token: CancellationToken = CancellationToken()) {
        open(STREAM, JSONObject(), token).use { call ->
            val status = call.conn.responseCode
            if (status == 400) {
                val error = call.conn.errorStream?.use { json(it, token, 8192) }?.optJSONObject("error")
                if (error?.optString("message") == "Missing text" &&
                    error.optString("type") == "invalid_request_error") return
            }
            if (status in 200..299 || status == 400) throw ClientException("返回内容与朗读服务的接口不匹配")
            throw ApiException(status)
        }
    }

    fun catalog(token: CancellationToken = CancellationToken()): List<Voice> =
        open("/api/voices/all", null, token).use { call ->
            call.requireSuccess()
            val root = json(call.conn.inputStream, token)
            val voices = buildList {
                for (provider in root.keys()) {
                    val items = root.optJSONArray(provider) ?: continue
                    for (i in 0 until items.length()) {
                        val item = items.optJSONObject(i) ?: continue
                        val id = (item.opt("id") as? String)?.takeIf { it.isNotBlank() } ?: continue
                        val name = (item.opt("name") as? String)?.takeIf { it.isNotBlank() }
                        add(Voice(provider, id, name ?: id))
                    }
                }
            }
            if (voices.isEmpty()) throw ClientException("服务未返回可用音色")
            voices.distinctBy { it.provider to it.id }
        }

    /**
     * This instance's configured per-request Unicode code point limit, cached briefly per base URL
     * so per-paragraph synthesis (e.g. from system TTS readers) avoids an extra HTTP round-trip.
     */
    fun maxTextLength(token: CancellationToken = CancellationToken()): Int {
        val now = System.nanoTime()
        maxLenCache[endpoint.base]?.let { (fetchedAt, value) ->
            if (now - fetchedAt < MAX_LEN_TTL_NS) return value
        }
        return open("/api/info", null, token).use { call ->
            call.requireSuccess()
            val value = json(call.conn.inputStream, token).optJSONObject("config")?.optInt("max_text_length")
                ?.takeIf { it > 0 } ?: throw ClientException("未能读取服务端文本长度上限")
            maxLenCache[endpoint.base] = now to value
            value
        }
    }

    fun synthesis(
        text: String, voice: Voice, speed: Float, preview: Boolean = false,
        token: CancellationToken = CancellationToken(),
    ): InputStream {
        val body = JSONObject().put("voice", voice.id).put("provider", voice.provider)
        if (!preview) {
            require(text.isNotBlank()) { "文本不能为空" }
            require(speed.isFinite()) { "语速无效" }
            val percent = ((speed.coerceIn(.25f, 4f) - 1f) * 100f).roundToInt()
            body.put("text", text).put("rate", if (percent >= 0) "+$percent%" else "$percent%")
        }
        val call = open(if (preview) "/api/tts/preview" else STREAM, body, token)
        try {
            call.requireSuccess()
            if (call.conn.getHeaderField("X-TTS-Fallback").equals("true", ignoreCase = true)) {
                throw ClientException("所选音色不可用，服务已回退至其他音色；请选择可用音色")
            }
            token.check()
            if (preview) return call.use {
                val encoded = json(call.conn.inputStream, token).optString("audio")
                val bytes = try { Base64.getDecoder().decode(encoded) }
                    catch (_: IllegalArgumentException) { throw ClientException("试听音频格式错误") }
                if (bytes.isEmpty()) throw ClientException("试听音频为空")
                token.check()
                ByteArrayInputStream(bytes)
            }
            if (!call.conn.contentType.orEmpty().substringBefore(';').trim().equals("audio/mpeg", true))
                throw ClientException("服务未返回 MP3 音频")
            return object : FilterInputStream(call.conn.inputStream) {
                override fun read(): Int { token.check(); return super.read() }
                override fun read(b: ByteArray, off: Int, len: Int): Int {
                    token.check(); return super.read(b, off, len)
                }
                override fun close() { try { super.close() } finally { call.close() } }
            }
        } catch (e: Throwable) { call.close(); throw e }
    }

    private class Call(val conn: HttpURLConnection, token: CancellationToken) : Closeable {
        private val hook = token.onCancel { conn.disconnect() }
        fun requireSuccess() {
            val status = conn.responseCode
            if (status !in 200..299) throw ApiException(status)
        }
        override fun close() { hook.close(); conn.disconnect() }
    }

    private fun open(path: String, body: JSONObject?, token: CancellationToken): Call {
        token.check()
        val conn = URL(endpoint.base + path).openConnection(Proxy.NO_PROXY) as HttpURLConnection
        val call = Call(conn, token)
        try {
            conn.instanceFollowRedirects = false // Never forward either text or Bearer credentials.
            conn.useCaches = false
            conn.connectTimeout = 10_000
            conn.readTimeout = 90_000
            conn.setRequestProperty("Accept", if (path == STREAM) "audio/mpeg, application/json" else "application/json")
            if (endpoint.key.isNotEmpty()) conn.setRequestProperty("Authorization", "Bearer " + endpoint.key)
            if (body != null) {
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                conn.setFixedLengthStreamingMode(bytes.size)
                token.check()
                conn.outputStream.use { it.write(bytes) }
            }
            token.check()
            return call
        } catch (e: Throwable) { call.close(); throw e }
    }

    private fun json(stream: InputStream, token: CancellationToken, limit: Int = 4 * 1024 * 1024): JSONObject {
        val raw = ByteArrayOutputStream()
        val buffer = ByteArray(8192)
        stream.use {
            while (true) {
                token.check()
                val n = it.read(buffer)
                if (n < 0) break
                if (n > limit - raw.size()) throw ClientException("服务响应超过大小限制")
                raw.write(buffer, 0, n)
            }
        }
        // Do not surface parser messages (some include the entire response).
        return try { JSONObject(raw.toString("UTF-8")) } catch (_: JSONException) {
            throw ClientException("服务返回了无效的 JSON")
        }
    }

    private companion object {
        const val STREAM = "/speech/stream/chunked"
        const val MAX_LEN_TTL_NS = 10L * 60L * 1_000_000_000L
        val maxLenCache = ConcurrentHashMap<String, Pair<Long, Int>>()
    }
}
