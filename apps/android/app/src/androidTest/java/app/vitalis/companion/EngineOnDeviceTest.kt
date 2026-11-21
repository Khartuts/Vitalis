package app.vitalis.companion

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.vitalis.companion.engine.EngineException
import app.vitalis.companion.engine.EngineHost
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * Runs the real VITALIS engine on the device/emulator (requires a build with -Pvitalis.engine=true):
 * encrypted database, synthetic data, the full recompute and the overview — the same code as the desktop.
 */
@RunWith(AndroidJUnit4::class)
class EngineOnDeviceTest {
    private val tag = "VitalisEngineTest"

    @Test
    fun engineRunsOnTheDeviceWithAnEncryptedDatabase(): Unit = runBlocking {
        assertTrue("built without -Pvitalis.engine=true", EngineHost.AVAILABLE)
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val db = File(ctx.filesDir, "engine-test.db").apply { delete() }
        val key = (1..32).joinToString("") { "%02x".format(it) }

        EngineHost(ctx).use { eng ->
            var t = System.currentTimeMillis()
            val ready = eng.open(db, key, demo = true, timezone = "Europe/Zagreb", profileName = "Emulator")
            Log.i(tag, "open: ${System.currentTimeMillis() - t} ms, $ready")
            assertEquals(true, ready["demo"]?.jsonPrimitive?.boolean)

            t = System.currentTimeMillis()
            eng.call("demo.load", buildJsonObject { put("days", 60) }, timeoutMs = 900_000)
            Log.i(tag, "demo.load(60 days) incl. recompute: ${System.currentTimeMillis() - t} ms")

            val status = eng.call("system.status").jsonObject
            val today = status["today"]!!.jsonPrimitive.content
            t = System.currentTimeMillis()
            val ov = eng.call("overview.get", buildJsonObject { put("date", today) }).jsonObject
            Log.i(tag, "overview.get: ${System.currentTimeMillis() - t} ms")
            assertTrue("no main sleep in the overview", ov["last_sleep"] !is JsonNull)
            assertTrue(ov["comparisons"]!!.jsonArray.isNotEmpty())

            val explain = eng.call("explain.data", buildJsonObject {
                put("date", today); put("features", JsonArray(listOf(JsonPrimitive("rhr")))); put("scores", JsonArray(listOf(JsonPrimitive("recovery"))))
            }).jsonObject
            assertTrue(explain["features"]!!.jsonArray[0].jsonObject["days"]!!.jsonPrimitive.int > 30)

            val corr = eng.call("stats.correlate", buildJsonObject {
                put("x", "sleep_tst_min"); put("y", "rhr")
                put("start", status["data_range"]!!.jsonArray[0].jsonPrimitive.content); put("end", today)
            }).jsonObject
            Log.i(tag, "stats.correlate: $corr")

            // The phone does not host the LAN sync server.
            assertEquals(false, eng.call("sync.status").jsonObject["available"]!!.jsonPrimitive.boolean)
            val err = runCatching { eng.call("sync.start") }.exceptionOrNull() as EngineException
            assertEquals("not_available", err.code)
        }

        // The database file is encrypted: no plaintext SQLite header.
        val header = db.inputStream().use { it.readNBytes(16) }
        assertFalse(String(header, Charsets.ISO_8859_1).startsWith("SQLite format 3"))

        // A wrong key is refused.
        val wrong = (101..132).joinToString("") { "%02x".format(it) }
        EngineHost(ctx).use { eng ->
            val e = runCatching { eng.open(db, wrong, demo = true, timezone = "Europe/Zagreb", profileName = "x") }.exceptionOrNull()
            assertTrue("wrong key was accepted: $e", e is EngineException && e.code == "fatal")
        }
        db.delete()
    }
}
