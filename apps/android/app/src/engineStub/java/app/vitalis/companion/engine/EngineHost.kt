package app.vitalis.companion.engine

import android.content.Context
import app.vitalis.companion.sync.SecureStore
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import java.io.Closeable
import java.io.File

class EngineException(val code: String, message: String) : Exception(message)

/** Build without the on-phone engine (built with -Pvitalis.engine=true it is the real EngineHost). */
class EngineHost(@Suppress("UNUSED_PARAMETER") context: Context) : Closeable {
    val events: SharedFlow<JsonObject> = MutableSharedFlow()
    suspend fun open(dbFile: File, keyHex: String, demo: Boolean, timezone: String, profileName: String): JsonObject = unavailable()
    suspend fun call(method: String, params: JsonObject = JsonObject(emptyMap()), timeoutMs: Long = 0): JsonElement = unavailable()
    override fun close() {}

    companion object {
        const val AVAILABLE = false
        fun databaseKey(@Suppress("UNUSED_PARAMETER") store: SecureStore): String = unavailable()
        private fun unavailable(): Nothing = throw EngineException("not_available", "This build does not include the on-phone engine.")
    }
}
