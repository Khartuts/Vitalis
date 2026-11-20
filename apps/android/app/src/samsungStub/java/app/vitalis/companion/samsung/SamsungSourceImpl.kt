package app.vitalis.companion.samsung

import android.app.Activity
import android.content.Context
import kotlinx.serialization.json.JsonObject
import java.time.Instant

/** Used when the Samsung Health Data SDK AAR is not present at build time. */
class SamsungSourceImpl(@Suppress("unused") private val context: Context) : SamsungSource {
    override suspend fun status() = "Samsung Health Data SDK is not bundled in this build. " +
        "Health Connect still provides Samsung Health data that Samsung shares there. See INSTALLATION.md to build with the SDK."
    override fun isBundled() = false
    override suspend fun isConnected() = false
    override suspend fun connect(activity: Activity): List<String> =
        throw UnsupportedOperationException("This build does not include the Samsung Health Data SDK.")
    override suspend fun readSince(since: Instant): List<JsonObject> = emptyList()
}
