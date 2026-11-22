package app.vitalis.companion

import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.vitalis.companion.engine.EngineHost
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.web.WebAppActivity
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** The phone screens (bundled web build) running against the on-phone engine through the origin-restricted bridge. */
@RunWith(AndroidJUnit4::class)
class WebAppOnDeviceTest {

    private fun pageText(sc: ActivityScenario<WebAppActivity>): String {
        val latch = CountDownLatch(1)
        var text = ""
        sc.onActivity { act ->
            act.webViewForTest().evaluateJavascript("document.body ? document.body.innerText : ''") { r ->
                text = runCatching { Json.parseToJsonElement(r).jsonPrimitive.content }.getOrDefault("")
                latch.countDown()
            }
        }
        latch.await(10, TimeUnit.SECONDS)
        return text
    }

    private fun waitFor(sc: ActivityScenario<WebAppActivity>, timeoutMs: Long, what: String, cond: (String) -> Boolean): String {
        val end = System.currentTimeMillis() + timeoutMs
        var last = ""
        while (System.currentTimeMillis() < end) {
            last = pageText(sc)
            if (cond(last)) return last
            Thread.sleep(500)
        }
        throw AssertionError("Timed out waiting for $what. Page text:\n${last.take(1500)}")
    }

    @Test
    fun phoneScreensRunOnTheOnPhoneEngine() {
        assertTrue("built without -Pvitalis.engine=true", EngineHost.AVAILABLE)
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        ActivityScenario.launch(WebAppActivity::class.java).use { sc ->
            waitFor(sc, 240_000, "the first screen") { it.contains("No health data yet") || it.contains("Morning recovery estimate") }

            // One night of sleep through the same import path as Health Connect data.
            val wake = Instant.now().minus(2, ChronoUnit.HOURS).truncatedTo(ChronoUnit.MINUTES)
            val start = wake.minus(8, ChronoUnit.HOURS)
            fun stage(a: Long, b: Long, s: String) = buildJsonObject {
                put("startTime", start.plus(a, ChronoUnit.MINUTES).toString()); put("endTime", start.plus(b, ChronoUnit.MINUTES).toString()); put("stage", s)
            }
            val sleep = buildJsonObject {
                put("type", "SleepSessionRecord"); put("id", "test-sleep-1"); put("origin", "com.sec.android.app.shealth")
                put("recordingMethod", "automatically_recorded")
                putJsonObject("device") { put("type", "WATCH"); put("manufacturer", "Samsung"); put("model", "SM-R960") }
                put("startTime", start.toString()); put("endTime", wake.toString())
                put("startZoneOffset", "+01:00"); put("endZoneOffset", "+01:00")
                putJsonArray("stages") {
                    add(stage(0, 15, "awake")); add(stage(15, 120, "light")); add(stage(120, 200, "deep"))
                    add(stage(200, 300, "light")); add(stage(300, 390, "rem")); add(stage(390, 480, "light"))
                }
            }
            val batch = buildJsonObject {
                put("schema", "vitalis.sync.v1"); put("batch_id", UUID.randomUUID().toString()); put("source", "health_connect")
                putJsonObject("device") { put("model", "emulator") }
                putJsonArray("records") { add(sleep) }; putJsonArray("deletions") {}
            }
            val summary = runBlocking { PhoneEngine.get(ctx).call("ingest.local_batch", buildJsonObject { put("payload", batch) }) }
            assertTrue("not imported: $summary", summary.jsonObject["inserted"]!!.jsonPrimitive.int >= 1 ||
                summary.jsonObject["already_imported"]?.jsonPrimitive?.booleanOrNull == true)

            waitFor(sc, 180_000, "the dashboard after import") { it.contains("Last night's sleep") }
            sc.onActivity { it.webViewForTest().evaluateJavascript("location.hash = '#/sleep'", null) }
            waitFor(sc, 120_000, "the Sleep Lab") { it.contains("Sleep Lab") && it.contains("Total sleep time") }
            // Phone settings: status comes from the app through the phone:* channels.
            sc.onActivity { it.webViewForTest().evaluateJavascript("location.hash = '#/settings'", null) }
            waitFor(sc, 60_000, "the phone settings") { it.contains("Health Connect → this phone") && it.contains("data types allowed") }
            sc.onActivity { it.webViewForTest().evaluateJavascript("location.hash = '#/temperature'", null) }
            waitFor(sc, 60_000, "the Temperature screen") { it.contains("Tonight's bedroom temperature") && it.contains("°C") }
        }
    }
}
