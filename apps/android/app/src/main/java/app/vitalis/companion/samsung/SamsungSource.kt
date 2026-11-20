package app.vitalis.companion.samsung

import android.app.Activity
import android.content.Context
import kotlinx.serialization.json.JsonObject
import java.time.Instant

/**
 * Samsung Health Data SDK adapter. The real implementation (src/samsung) is compiled only when the developer has
 * downloaded `samsung-health-data-api.aar` into app/libs (requires a Samsung developer account; the AAR cannot be
 * redistributed). Otherwise the stub (src/samsungStub) reports that the SDK is not bundled.
 */
interface SamsungSource {
    /** Human-readable availability, e.g. "SDK not bundled", "Samsung Health not installed", "Ready". */
    suspend fun status(): String
    fun isBundled(): Boolean
    suspend fun isConnected(): Boolean
    /** Requests read permissions (shows Samsung's consent UI). Returns the granted data types. */
    suspend fun connect(activity: Activity): List<String>
    /** Records serialized as SAMSUNG_SDK.* for vitalis_engine/ingest/samsung_sdk.py. */
    suspend fun readSince(since: Instant): List<JsonObject>

    companion object {
        fun create(context: Context): SamsungSource = SamsungSourceImpl(context.applicationContext)
    }
}
