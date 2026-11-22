package app.vitalis.companion

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import app.vitalis.companion.ai.OpenAiResponses
import app.vitalis.companion.ai.PhoneOrchestrator
import app.vitalis.companion.engine.EngineHost
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.sync.SecureStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * The phone's AI analyst end to end on the device: consent before each round (nothing sent until approved),
 * a read-only engine tool call, the disclosure log and the stored answer — against a local HTTPS mock of the
 * Responses API (the app itself only talks to api.openai.com).
 */
@RunWith(AndroidJUnit4::class)
class AnalystOnDeviceTest {
    private fun sse(vararg events: JsonObject) = MockResponse.Builder().code(200).addHeader("content-type", "text/event-stream")
        .body(events.joinToString("") { "event: ${it["type"]!!.jsonPrimitive.content}\ndata: $it\n\n" }).build()

    @Test
    fun analystAsksForConsentRunsAToolAndStoresTheAnswer(): Unit = runBlocking {
        assertTrue("built without -Pvitalis.engine=true", EngineHost.AVAILABLE)
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val cert = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val server = MockWebServer().apply { useHttps(HandshakeCertificates.Builder().heldCertificate(cert).build().sslSocketFactory()); start() }
        val trust = HandshakeCertificates.Builder().addTrustedCertificate(cert.certificate).build()
        val http = OkHttpClient.Builder().sslSocketFactory(trust.sslSocketFactory(), trust.trustManager).build()
        server.enqueue(sse(
            buildJsonObject { put("type", "response.output_item.done"); putJsonObject("item") {
                put("type", "function_call"); put("call_id", "c1"); put("name", "get_available_metrics"); put("arguments", "{}") } },
            buildJsonObject { put("type", "response.completed") }))
        server.enqueue(sse(
            buildJsonObject { put("type", "response.output_text.delta"); put("delta", "### Answer\nYou have data.") },
            buildJsonObject { put("type", "response.completed") }))
        try {
            SecureStore(ctx).put(PhoneOrchestrator.KEY, "sk-test-0000000000000000000000")
            val eng = PhoneEngine.get(ctx)
            val thread = eng.call("chat.thread_create", buildJsonObject { put("title", "Device test"); put("provider", "openai_api") }).jsonObject["id"]!!.jsonPrimitive.content
            val before = eng.call("ai.disclosures").jsonArray.size
            val events = LinkedBlockingQueue<JsonObject>()
            lateinit var orch: PhoneOrchestrator
            orch = PhoneOrchestrator(ctx, { e ->
                events.put(e)
                if (e["type"]!!.jsonPrimitive.content == "consent") {
                    // The exact request body is shown before anything is sent.
                    assertTrue(e["payload"]!!.jsonPrimitive.content.contains("\"store\": false"))
                    if (e["round"]!!.jsonPrimitive.int == 1) assertEquals("nothing may be sent before approval", 0, server.requestCount)
                    orch.decide(e["requestId"]!!.jsonPrimitive.content, true, false)
                }
            }) { key -> OpenAiResponses(key, server.url("/v1").toString().trimEnd('/'), http) }
            orch.ask(CoroutineScope(SupervisorJob() + Dispatchers.IO), buildJsonObject {
                put("threadId", thread); put("message", "What data do I have?"); put("provider", "openai_api"); put("model", "gpt-test")
            })
            var done: JsonObject? = null
            val seen = mutableListOf<String>()
            while (done == null) {
                val e = events.poll(120, TimeUnit.SECONDS) ?: break
                seen += e["type"]!!.jsonPrimitive.content
                if (e["type"]!!.jsonPrimitive.content == "error") fail("analyst error: $e")
                if (e["type"]!!.jsonPrimitive.content == "done") done = e
            }
            assertNotNull("no answer; events: $seen", done)
            assertEquals(2, seen.count { it == "consent" })              // one consent per round
            assertTrue(seen.contains("tool_call"))
            assertEquals(1, done!!["toolCalls"]!!.jsonPrimitive.int)
            assertEquals(2, server.requestCount)
            val round1 = Json.parseToJsonElement(server.takeRequest().body!!.utf8()).jsonObject
            assertEquals(false, round1["store"]!!.jsonPrimitive.boolean)
            val round2 = Json.parseToJsonElement(server.takeRequest().body!!.utf8()).jsonObject
            assertTrue(round2["input"]!!.jsonArray.any { it.jsonObject["type"]?.jsonPrimitive?.contentOrNull == "function_call_output" })
            assertEquals(before + 2, eng.call("ai.disclosures").jsonArray.size)  // both approved requests logged (hash only)
            val msgs = eng.call("chat.messages", buildJsonObject { put("thread_id", thread) }).jsonArray
            assertEquals("assistant", msgs.last().jsonObject["role"]!!.jsonPrimitive.content)
        } finally {
            SecureStore(ctx).put(PhoneOrchestrator.KEY, null)
            server.close()
        }
    }
}
