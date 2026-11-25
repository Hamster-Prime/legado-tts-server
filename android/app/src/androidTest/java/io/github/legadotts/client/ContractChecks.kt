package io.github.legadotts.client

import io.github.legadotts.client.data.CancellationToken
import io.github.legadotts.client.data.ClientApi
import io.github.legadotts.client.data.Endpoint
import io.github.legadotts.client.data.Voice
import io.github.legadotts.client.service.Mp3Frames
import io.github.legadotts.client.service.speechSegments
import org.json.JSONObject
import java.io.IOException
import java.io.InterruptedIOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread

/** Runs both on a device and on a host JVM (add org.json on the host). No external requests. */
internal object ContractChecks {
    private val voice = Voice("edge", "test-id", "新音色")
    fun offline() {
        // Address/key normalization and retention boundaries.
        val endpoint = Endpoint.of(" https://example.invalid/sub/ ", " test-secret ")
        check(endpoint.base == "https://example.invalid/sub" && endpoint.key == "test-secret")
        check("test-secret" !in endpoint.toString())
        for (base in listOf("https://a:b@host", "https://host/?api_key=test-secret", "ftp://host",
            "https://host#frag", "http://", "https://host:70000", "https://host/a/../x", "x")) {
            check(runCatching { Endpoint.of(base) }.isFailure)
        }
        check(runCatching { Endpoint.of("http://localhost", "secret") }.isFailure)
        check(runCatching { Endpoint.of("https://host", "a\r\nb") }.isFailure)

        val token = CancellationToken()
        var cancelled = 0
        token.onCancel { cancelled += 10 }.close()
        token.onCancel { cancelled++ }
        token.cancel(); token.cancel()
        token.onCancel { cancelled++ }
        check(cancelled == 2 && runCatching { token.check() }.exceptionOrNull() is InterruptedIOException)

        val text = "𠀀甲乙。短句！".repeat(120)
        val chunks = speechSegments(text, 77).map { it.text }.toList()
        check(chunks.size > 2 && chunks.joinToString("") == text)
        check(chunks.first().codePointCount(0, chunks.first().length) <= 48)
        check(chunks.all { it.codePointCount(0, it.length) in 1..77 })
        check(speechSegments("🚲a", 1).map { it.text }.toList() == listOf("🚲", "a"))

        fixture({ path, _, socket ->
            check(path == "/prefix/speech/stream/chunked")
            socket.reply(400, """{"error":{"type":"invalid_request_error","message":"Missing text"}}""")
        }) { it.probe() }
        for (status in listOf(401, 429, 307, 400, 200)) {
            fixture({ _, _, socket -> socket.reply(status, """{"error":{"message":"unrelated"}}""") }) {
                val error = runCatching { it.probe() }.exceptionOrNull()
                check(error is IOException && (status in listOf(400, 200) ||
                    (error as? ClientApi.ApiException)?.status == status))
            }
        }

        fixture({ path, _, socket ->
            check(path == "/prefix/api/voices/all")
            socket.reply(200, """{"newProvider":[{"id":"from-server","name":"新音色"}]}""")
        }) { check(it.catalog().single() == Voice("newProvider", "from-server", "新音色")) }

        // The fake upstream does not send the tail until the reader gets the first bytes.
        // Buffer-before-return would hang / fail this deterministic check.
        val gotFirst = CountDownLatch(1)
        fixture({ path, json, socket ->
            check(path == "/prefix/speech/stream/chunked" && json.getString("rate") == "+25%")
            check(json.getString("text") == "中文" && json.getString("voice") == voice.id &&
                json.getString("provider") == voice.provider)
            val out = socket.getOutputStream()
            out.write("HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nTransfer-Encoding: chunked\r\n\r\n1\r\na\r\n".toByteArray())
            out.flush()
            check(gotFirst.await(5, TimeUnit.SECONDS))
            out.write("1\r\nb\r\n0\r\n\r\n".toByteArray()); out.flush()
        }) { it.synthesis("中文", voice, 1.25f).use { input ->
            check(input.read() == 'a'.code)
            gotFirst.countDown()
            check(input.readBytes().contentEquals(byteArrayOf('b'.code.toByte())))
        } }

        fixture({ path, json, socket ->
            check(path == "/prefix/api/tts/preview" && !json.has("text"))
            socket.reply(200, """{"audio":"YQ=="}""")
        }) { check(it.synthesis("", voice, 1f, preview = true).use { stream -> stream.read() } == 'a'.code) }
    }

    /** Runs against a user-provided real instance. Never prints the address or the key. */
    fun real(endpoint: Endpoint, synthesize: Boolean): String {
        val api = ClientApi(endpoint)
        api.probe()
        val voices = api.catalog()
        val max = api.maxTextLength()
        check(max > 0 && voices.isNotEmpty())
        val audioResult = if (synthesize) {
            // Always select the free Edge provider for the optional audio check.
            val edge = voices.firstOrNull { it.provider == "edge" && it.id == "zh-CN-XiaoxiaoNeural" }
                ?: voices.first { it.provider == "edge" }
            val sample = "这是通过服务端合成的朗读测试。".repeat(4)
            var firstMs = -1L
            val start = System.nanoTime()
            var segmentCount = 0
            var frameCount = 0
            for (part in speechSegments(sample, max)) {
                segmentCount++
                api.synthesis(part.text, edge, 1f).use { input ->
                    val frames = Mp3Frames(input)
                    var count = 0
                    while (frames.next() != null) {
                        if (firstMs < 0) firstMs = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - start)
                        count++
                    }
                    check(count > 0); frameCount += count
                }
            }
            check(segmentCount >= 2)
            "，Edge ${segmentCount} 段 / ${frameCount} 帧，首个 MP3 帧 ${firstMs} ms（非扬声器延迟）"
        } else ""
        return "通过：鉴权探测，${voices.size} 项音色，单段上限 $max" + audioResult
    }

    private fun Socket.reply(status: Int, body: String) {
        val bytes = body.toByteArray(Charsets.UTF_8)
        val headers = "HTTP/1.1 $status Test\r\nContent-Type: application/json\r\n" +
            (if (status == 307) "Location: /should-not-follow\r\n" else "") +
            "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        getOutputStream().apply { write(headers.toByteArray()); write(bytes); flush() }
    }

    /** One local exchange. It never accepts connections from the LAN. */
    private fun fixture(handle: (String, JSONObject, Socket) -> Unit, run: (ClientApi) -> Unit) {
        ServerSocket(0, 1, InetAddress.getByName("127.0.0.1")).use { server ->
            val problem = AtomicReference<Throwable?>()
            val worker = thread(isDaemon = true) {
                try {
                    server.accept().use { socket ->
                        socket.soTimeout = 6000
                        val input = socket.getInputStream().buffered()
                        fun line(): String = buildString {
                            while (length < 8192) {
                                val b = input.read()
                                if (b < 0 || b == 10) break
                                if (b != 13) append(b.toChar())
                            }
                        }
                        val path = line().split(" ")[1]
                        val headers = generateSequence { line().takeIf { it.isNotEmpty() } }
                            .associate { it.substringBefore(':').lowercase() to it.substringAfter(':').trim() }
                        val body = ByteArray(headers["content-length"]?.toInt() ?: 0)
                        var offset = 0
                        while (offset < body.size) {
                            val n = input.read(body, offset, body.size - offset)
                            check(n > 0); offset += n
                        }
                        handle(path, if (body.isEmpty()) JSONObject() else JSONObject(String(body, Charsets.UTF_8)), socket)
                    }
                    // Reject any accidental redirect follow-up rather than wait for timeout.
                    server.close()
                } catch (e: Throwable) { problem.set(e) }
            }
            try {
                run(ClientApi(Endpoint.of("http://127.0.0.1:${server.localPort}/prefix")))
            } finally {
                server.close()
                worker.join(7000)
                problem.get()?.let { throw AssertionError("测试服务端断言失败", it) }
                check(!worker.isAlive)
            }
        }
    }
}
