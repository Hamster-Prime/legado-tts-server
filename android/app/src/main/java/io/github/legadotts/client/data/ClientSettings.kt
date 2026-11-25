package io.github.legadotts.client.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** All users (UI, foreground service, system engine) run in the app's default process. */
class ClientSettings internal constructor(context: Context, namespace: String) {
    constructor(context: Context) : this(context, "reader-v1")

    init { require(namespace.matches(Regex("[a-zA-Z0-9_-]+"))) }
    private val file = File(context.applicationContext.noBackupFilesDir, "$namespace.json")
    private val alias = "io.github.legadotts.client.$namespace"

    private fun load(): JSONObject = if (file.exists()) {
        try { JSONObject(file.readText()) }
        catch (_: Exception) { throw ClientException("本机配置不可读，请重新验证连接") }
    } else JSONObject()

    /** Temp + fsync + same-filesystem rename. Readers never see half of an address/key switch. */
    private fun save(json: JSONObject) {
        val dest = file.toPath()
        val tmp = Files.createTempFile(dest.parent, "reader-", ".new")
        try {
            FileOutputStream(tmp.toFile()).use {
                it.write(json.toString().toByteArray(Charsets.UTF_8))
                it.fd.sync()
            }
            Files.move(tmp, dest, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally {
            runCatching { Files.deleteIfExists(tmp) }
        }
    }

    private fun key(create: Boolean): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(alias, null) as? SecretKey)?.let { return it }
        if (!create) throw ClientException("无法读取本机密钥，请重新填写访问密钥")
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").run {
            init(KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setRandomizedEncryptionRequired(true).build())
            generateKey()
        }
    }
    fun endpoint(): Endpoint? = synchronized(lock) {
        val config = load()
        val base = config.optString("base")
        if (base.isEmpty()) return null
        val secret = if (config.has("secret")) {
            try {
                val cipher = Cipher.getInstance("AES/GCM/NoPadding")
                val iv = Base64.getDecoder().decode(config.getString("iv"))
                cipher.init(Cipher.DECRYPT_MODE, key(false), GCMParameterSpec(128, iv))
                cipher.updateAAD(base.toByteArray(Charsets.UTF_8))
                val bytes = cipher.doFinal(Base64.getDecoder().decode(config.getString("secret")))
                try { bytes.toString(Charsets.UTF_8) } finally { bytes.fill(0) }
            } catch (_: Exception) {
                throw ClientException("本机密钥不可用，请重新填写并验证服务")
            }
        } else ""
        Endpoint.of(base, secret)
    }
    fun address(): String = synchronized(lock) { load().optString("base") }
    fun hasKey(): Boolean = synchronized(lock) { load().has("secret") }

    private fun JSONObject.setVoice(voice: Voice) {
        put("voice", JSONArray().put(voice.provider).put(voice.id).put(voice.name))
    }
    fun selectedVoice(): Voice? = synchronized(lock) {
        val v = load().optJSONArray("voice") ?: return null
        if (v.length() == 3) Voice(v.getString(0), v.getString(1), v.getString(2)) else null
    }

    /** Public voice catalog cache so the app opens immediately without flashing empty lists. */
    fun cachedVoices(): List<Voice> = synchronized(lock) {
        val entries = load().optJSONArray("catalog") ?: return emptyList()
        buildList {
            for (i in 0 until entries.length()) {
                val row = entries.optJSONArray(i) ?: continue
                if (row.length() == 3) add(Voice(row.getString(0), row.getString(1), row.getString(2)))
            }
        }
    }
    fun cacheVoices(voices: List<Voice>) = synchronized(lock) {
        val entries = JSONArray()
        for (v in voices) entries.put(JSONArray().put(v.provider).put(v.id).put(v.name))
        save(load().put("catalog", entries))
    }

    fun saveConnection(endpoint: Endpoint, voice: Voice): Unit = synchronized(lock) {
        // Explicitly reconfiguring after a successful probe can repair unreadable local state.
        val config = try { load() } catch (_: IOException) { JSONObject() }
        if (config.optString("base") != endpoint.base) {
            config.remove("favorites")
            config.remove("catalog")
        }
        config.put("base", endpoint.base)
        config.setVoice(voice)
        if (endpoint.key.isEmpty()) {
            config.remove("secret")
            config.remove("iv")
        } else {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding")
            cipher.init(Cipher.ENCRYPT_MODE, key(true))
            cipher.updateAAD(endpoint.base.toByteArray(Charsets.UTF_8))
            val plain = endpoint.key.toByteArray(Charsets.UTF_8)
            val encrypted = try { cipher.doFinal(plain) } finally { plain.fill(0) }
            config.put("iv", Base64.getEncoder().encodeToString(cipher.iv))
            config.put("secret", Base64.getEncoder().encodeToString(encrypted))
        }
        save(config)
    }
    fun select(voice: Voice) = synchronized(lock) {
        save(load().apply { setVoice(voice) })
    }
    fun speed(): Float = synchronized(lock) { load().optDouble("speed", 1.0).toFloat().coerceIn(.5f, 2f) }
    fun setSpeed(value: Float) = synchronized(lock) {
        require(value.isFinite())
        save(load().put("speed", value.coerceIn(.5f, 2f).toDouble()))
    }
    fun favorites(): Set<Pair<String, String>> = synchronized(lock) {
        val entries = load().optJSONArray("favorites") ?: return emptySet()
        buildSet {
            for (i in 0 until entries.length()) {
                val pair = entries.optJSONArray(i) ?: continue
                if (pair.length() == 2) add(pair.getString(0) to pair.getString(1))
            }
        }
    }
    fun favorites(values: Set<Pair<String, String>>) = synchronized(lock) {
        val entries = JSONArray()
        for ((provider, voice) in values) entries.put(JSONArray().put(provider).put(voice))
        save(load().put("favorites", entries))
    }

    private companion object { val lock = Any() }
}
