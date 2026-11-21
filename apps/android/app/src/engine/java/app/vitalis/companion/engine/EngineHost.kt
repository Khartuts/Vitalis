package app.vitalis.companion.engine

import android.content.Context
import app.vitalis.companion.sync.SecureStore
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.*
import java.io.Closeable
import java.io.File
import java.security.SecureRandom
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.atomic.AtomicLong
import kotlin.concurrent.thread

class EngineException(val code: String, message: String) : Exception(message)

/**
 * The VITALIS analytics engine running inside the app (Chaquopy). It is the same Python engine as on the desktop and
 * speaks the same JSON-line protocol (see services/engine/vitalis_engine/embedded.py): requests carry an id, answers
 * are matched by id, everything else is an event.
 */
class EngineHost(private val context: Context) : Closeable {
    private val json = Json { ignoreUnknownKeys = true }
    private var engine: PyObject? = null
    private var reader: Thread? = null
    private val nextId = AtomicLong(1)
    private val pending = ConcurrentHashMap<Long, CompletableDeferred<JsonObject>>()
    private val ready = CompletableDeferred<JsonObject>()
    private val _events = MutableSharedFlow<JsonObject>(extraBufferCapacity = 256)
    val events: SharedFlow<JsonObject> = _events

    /** Opens (or creates) the encrypted database and starts the engine. Returns the "ready" message. */
    suspend fun open(dbFile: File, keyHex: String, demo: Boolean, timezone: String, profileName: String): JsonObject =
        withContext(Dispatchers.IO) {
            check(engine == null) { "engine already open" }
            val eng = python(context).getModule("vitalis_engine.embedded").callAttr("EmbeddedEngine")
            val init = buildJsonObject {
                put("type", "init"); put("db_path", dbFile.absolutePath); put("key", keyHex)
                put("demo", demo); put("tz", timezone); put("profile_name", profileName)
            }
            eng.callAttr("start", init.toString())
            engine = eng
            reader = thread(name = "vitalis-engine-reader", isDaemon = true) { readLoop(eng) }
            withTimeout(OPEN_TIMEOUT_MS) { ready.await() }
        }

    private fun readLoop(eng: PyObject) {
        while (true) {
            val item = eng.callAttr("receive", 30.0) ?: break // None: the engine has stopped
            val line = item.toString()
            if (line.isEmpty()) continue
            val msg = runCatching { json.parseToJsonElement(line).jsonObject }.getOrNull() ?: continue
            when (msg["type"]?.jsonPrimitive?.contentOrNull) {
                "ready" -> { ready.complete(msg); continue }
                "fatal" -> { ready.completeExceptionally(EngineException("fatal", msg["error"]?.jsonPrimitive?.contentOrNull ?: "engine failed to start")); continue }
                "event" -> { msg["event"]?.jsonObject?.let { _events.tryEmit(it) }; continue }
            }
            msg["id"]?.jsonPrimitive?.longOrNull?.let { pending.remove(it)?.complete(msg) }
        }
        val stopped = EngineException("stopped", "The engine has stopped.")
        if (!ready.isCompleted) ready.completeExceptionally(stopped)
        pending.values.forEach { it.completeExceptionally(stopped) }
        pending.clear()
    }

    /** Calls an engine method. Throws [EngineException] with the engine's error code on failure. */
    suspend fun call(method: String, params: JsonObject = JsonObject(emptyMap()), timeoutMs: Long = CALL_TIMEOUT_MS): JsonElement {
        val eng = engine ?: throw IllegalStateException("engine is not open")
        val id = nextId.getAndIncrement()
        val answer = CompletableDeferred<JsonObject>()
        pending[id] = answer
        withContext(Dispatchers.IO) {
            eng.callAttr("send", buildJsonObject { put("id", id); put("method", method); put("params", params) }.toString())
        }
        val msg = try { withTimeout(timeoutMs) { answer.await() } } finally { pending.remove(id) }
        msg["error"]?.jsonObject?.let { e ->
            throw EngineException(e["code"]?.jsonPrimitive?.contentOrNull ?: "error", e["message"]?.jsonPrimitive?.contentOrNull ?: "engine error")
        }
        return msg["result"] ?: JsonNull
    }

    override fun close() {
        engine?.callAttr("stop", 30.0)
        reader?.join(5_000)
        engine = null
    }

    companion object {
        const val AVAILABLE = true
        private const val OPEN_TIMEOUT_MS = 180_000L
        private const val CALL_TIMEOUT_MS = 300_000L

        fun python(context: Context): Python {
            if (!Python.isStarted()) Python.start(AndroidPlatform(context.applicationContext))
            return Python.getInstance()
        }

        /** The on-phone database key: 32 random bytes, stored AES-GCM-encrypted under a non-exportable Keystore key. */
        fun databaseKey(store: SecureStore): String {
            store.get("engine_db_key")?.let { return it }
            val key = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it) }
            store.put("engine_db_key", key)
            return key
        }
    }
}
