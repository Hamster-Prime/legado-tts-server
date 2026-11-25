package io.github.legadotts.client.data

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import java.util.UUID

/** Run with the instrumentation targetContext; never read, change or remove user settings. */
internal object SettingsChecks {
    fun run(context: Context) {
        val tag = "test-" + UUID.randomUUID().toString()
        val store = ClientSettings(context, tag)
        val voice = Voice("edge", "id1", "音色一")
        val key = "not-a-real-api-key-" + UUID.randomUUID()
        val file = File(context.noBackupFilesDir, "$tag.json")
        val first = Endpoint.of("https://first.invalid/service", key)
        try {
            check(store.endpoint() == null && store.selectedVoice() == null)
            store.saveConnection(first, voice)
            store.favorites(setOf(voice.provider to voice.id))
            store.setSpeed(1.5f)
            check(store.endpoint()?.key == key && store.endpoint()?.base == first.base)
            check(store.hasKey() && store.selectedVoice() == voice && store.speed() == 1.5f)
            val snapshot = file.readText()
            check(!snapshot.contains(key))
            val oldIv = JSONObject(snapshot).getString("iv")
            store.saveConnection(first, voice)
            check(JSONObject(file.readText()).getString("iv") != oldIv)
            check(store.favorites() == setOf("edge" to "id1"))

            // Moving an encrypted value to a different destination must fail authentication.
            file.writeText(JSONObject(snapshot).put("base", "https://wrong.invalid").toString())
            check(runCatching { store.endpoint() }.isFailure)
            file.writeText(snapshot)

            store.saveConnection(Endpoint.of("http://second.invalid"), Voice("new", "v", "音色二"))
            check(!store.hasKey() && store.endpoint()?.key == "" && store.favorites().isEmpty())
        } finally {
            file.delete()
            KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
                .deleteEntry("io.github.legadotts.client.$tag")
        }
    }
}
