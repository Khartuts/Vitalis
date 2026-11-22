package app.vitalis.companion

import app.vitalis.companion.ai.OpenAiResponses
import app.vitalis.companion.ai.ProviderException
import kotlinx.serialization.json.*
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** The phone analyst's OpenAI Responses client against a mock server (same wire format as the desktop). */
class OpenAiResponsesTest {
    private val server = MockWebServer()

    @Before fun start() { server.start() }
    @After fun stop() { server.close() }

    private fun client() = OpenAiResponses({ "sk-test-0000000000000000000000" }, server.url("/v1").toString().trimEnd('/'))

    private fun sse(vararg events: JsonObject) = MockResponse.Builder().code(200).addHeader("content-type", "text/event-stream")
        .body(events.joinToString("") { "event: ${it["type"]!!.jsonPrimitive.content}\ndata: $it\n\n" }).build()

    @Test fun streamsTextAndCollectsToolCalls() {
        server.enqueue(sse(
            buildJsonObject { put("type", "response.output_text.delta"); put("delta", "Hi ") },
            buildJsonObject { put("type", "response.output_item.done"); putJsonObject("item") {
                put("type", "function_call"); put("call_id", "c1"); put("name", "get_baseline"); put("arguments", "{\"metric\":\"rhr\"}"); put("namespace", "vitalis") } },
            buildJsonObject { put("type", "response.completed") }))
        val c = client()
        val tools = buildJsonArray { add(buildJsonObject { put("name", "get_baseline"); put("description", "d"); put("parameters", buildJsonObject { put("type", "object") }) }) }
        val items = c.startConversation(listOf("user" to "earlier", "assistant" to "answer"), "How is my RHR?")
        val body = c.buildBody("system prompt", items, "gpt-test", tools)
        val deltas = mutableListOf<String>()
        val round = c.send(body) { deltas += it }
        assertEquals("Hi ", round.text); assertEquals(listOf("Hi "), deltas)
        assertEquals("get_baseline", round.calls.single().name)
        val req = server.takeRequest()
        assertEquals("/v1/responses", req.url.encodedPath)
        assertEquals("Bearer sk-test-0000000000000000000000", req.headers["authorization"])
        val sent = Json.parseToJsonElement(req.body!!.utf8()).jsonObject
        assertEquals(false, sent["store"]!!.jsonPrimitive.boolean)
        assertEquals(true, sent["stream"]!!.jsonPrimitive.boolean)
        assertEquals("namespace", sent["tools"]!!.jsonArray[0].jsonObject["type"]!!.jsonPrimitive.content)
        assertEquals(3, sent["input"]!!.jsonArray.size)
        // The tool result goes back as function_call_output after the call.
        c.appendRound(items, round, listOf("c1" to "{\"ok\":true}"))
        assertEquals("function_call_output", items.last().jsonObject["type"]!!.jsonPrimitive.content)
        assertEquals("function_call", items[items.size - 2].jsonObject["type"]!!.jsonPrimitive.content)
    }

    @Test fun mapsErrorsAndInterruptedStreams() {
        server.enqueue(MockResponse.Builder().code(429).body("{\"error\":{\"code\":\"rate_limit_exceeded\",\"message\":\"Slow down\"}}").build())
        val e = runCatching { client().send(buildJsonObject { put("model", "x") }) {} }.exceptionOrNull() as ProviderException
        assertEquals("rate_limit_exceeded", e.code); assertEquals(429, e.status)
        server.enqueue(sse(buildJsonObject { put("type", "response.output_text.delta"); put("delta", "partial") }))
        val cut = runCatching { client().send(buildJsonObject { put("model", "x") }) {} }.exceptionOrNull() as ProviderException
        assertEquals("stream_interrupted", cut.code)
    }

    @Test fun listsOnlyChatModels() {
        server.enqueue(MockResponse.Builder().code(200).body("{\"data\":[{\"id\":\"gpt-test\"},{\"id\":\"dall-e-3\"},{\"id\":\"o4-mini\"}]}").build())
        assertEquals(listOf("gpt-test", "o4-mini"), client().models())
    }
}
