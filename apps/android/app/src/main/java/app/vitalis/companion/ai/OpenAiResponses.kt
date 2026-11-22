package app.vitalis.companion.ai

import kotlinx.serialization.json.*
import okhttp3.Call
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.time.Duration

class ProviderException(val code: String, message: String, val status: Int? = null) : Exception(message)

data class ToolCall(val callId: String, val name: String, val arguments: String)
data class Round(val text: String, val calls: List<ToolCall>, val raw: List<JsonObject>)

/**
 * OpenAI Responses API (POST /v1/responses), streaming, with the analyst's read-only tools grouped in one namespace —
 * the same wire format as the desktop app (apps/desktop/electron/ai/providers.ts). The full conversation is kept
 * client-side (store:false, no previous_response_id).
 */
class OpenAiResponses(
    private val apiKey: () -> String,
    private val baseUrl: String = "https://api.openai.com/v1",
    private val http: OkHttpClient = OkHttpClient.Builder().connectTimeout(Duration.ofSeconds(20)).readTimeout(Duration.ofMinutes(5))
        .callTimeout(Duration.ofMinutes(10)).build(),
) {
    private val json = Json { ignoreUnknownKeys = true }
    @Volatile private var current: Call? = null

    fun cancel() { current?.cancel() }

    fun startConversation(history: List<Pair<String, String>>, message: String): MutableList<JsonElement> =
        (history.map { (role, content) -> buildJsonObject { put("role", role); put("content", content) } } +
            buildJsonObject { put("role", "user"); put("content", message) }).toMutableList()

    fun buildBody(system: String, items: List<JsonElement>, model: String, tools: JsonArray): JsonObject = buildJsonObject {
        put("model", model)
        put("instructions", system)
        put("input", JsonArray(items))
        putJsonArray("tools") {
            add(buildJsonObject {
                put("type", "namespace"); put("name", "vitalis")
                put("description", "Read-only analytics over the user's own stored health data.")
                putJsonArray("tools") {
                    tools.forEach { t ->
                        val o = t.jsonObject
                        add(buildJsonObject {
                            put("type", "function"); put("name", o["name"]!!); put("description", o["description"]!!); put("parameters", o["parameters"]!!)
                        })
                    }
                }
            })
        }
        put("store", false)
        put("stream", true)
    }

    fun appendRound(items: MutableList<JsonElement>, round: Round, outputs: List<Pair<String, String>>) {
        if (round.text.isNotEmpty()) items += buildJsonObject { put("role", "assistant"); put("content", round.text) }
        items += round.raw
        outputs.forEach { (callId, output) -> items += buildJsonObject { put("type", "function_call_output"); put("call_id", callId); put("output", output) } }
    }

    private fun request(path: String, body: String?): Request {
        val b = Request.Builder().url("$baseUrl$path").header("authorization", "Bearer ${apiKey()}")
        return if (body == null) b.get().build()
        else b.header("accept", "text/event-stream").post(body.toRequestBody("application/json".toMediaType())).build()
    }

    private fun error(res: Response): ProviderException {
        var code = "http_${res.code}"
        var message = res.message.ifEmpty { "request failed" }
        runCatching {
            val e = json.parseToJsonElement(res.body.string()).jsonObject["error"]?.jsonObject
            if (e != null) {
                code = e["code"]?.jsonPrimitive?.contentOrNull ?: e["type"]?.jsonPrimitive?.contentOrNull ?: code
                message = e["message"]?.jsonPrimitive?.contentOrNull ?: message
            }
        }
        if (res.code == 401 && code == "http_401") message = "Authorization was not accepted (check the API key)."
        return ProviderException(code, message, res.code)
    }

    /** One streamed model round: text deltas go to [onDelta]; function calls are collected. */
    fun send(body: JsonObject, onDelta: (String) -> Unit): Round {
        val call = http.newCall(request("/responses", body.toString()))
        current = call
        call.execute().use { res ->
            if (!res.isSuccessful) throw error(res)
            val text = StringBuilder()
            val calls = mutableListOf<ToolCall>()
            val raw = mutableListOf<JsonObject>()
            var completed = false
            var event: String? = null
            val data = StringBuilder()
            fun dispatch() {
                val payload = data.toString()
                data.setLength(0)
                if (payload.isEmpty() || payload == "[DONE]") return
                val d = runCatching { json.parseToJsonElement(payload).jsonObject }.getOrNull() ?: return
                when (d["type"]?.jsonPrimitive?.contentOrNull ?: event) {
                    "response.output_text.delta" -> d["delta"]?.jsonPrimitive?.contentOrNull?.let { text.append(it); onDelta(it) }
                    "response.output_item.done" -> {
                        val it = d["item"]?.jsonObject
                        if (it?.get("type")?.jsonPrimitive?.contentOrNull == "function_call") {
                            val args = it["arguments"]?.jsonPrimitive?.contentOrNull ?: "{}"
                            val c = ToolCall(it["call_id"]!!.jsonPrimitive.content, it["name"]!!.jsonPrimitive.content, args)
                            calls += c
                            raw += buildJsonObject {
                                put("type", "function_call"); put("call_id", c.callId); put("name", c.name); put("arguments", args)
                                it["namespace"]?.let { ns -> put("namespace", ns) }
                            }
                        }
                    }
                    "response.completed" -> completed = true
                    "response.failed", "error" -> {
                        val e = d["response"]?.jsonObject?.get("error")?.jsonObject ?: d["error"]?.jsonObject ?: d
                        throw ProviderException(e["code"]?.jsonPrimitive?.contentOrNull ?: "response_failed",
                            e["message"]?.jsonPrimitive?.contentOrNull ?: "The model request failed.")
                    }
                    "response.incomplete" -> throw ProviderException("incomplete", "The response was incomplete.")
                }
            }
            val src = res.body.source()
            while (true) {
                val line = src.readUtf8Line() ?: break
                when {
                    line.isEmpty() -> { dispatch(); event = null }
                    line.startsWith(":") -> {}
                    line.startsWith("event:") -> event = line.substring(6).trim()
                    line.startsWith("data:") -> { if (data.isNotEmpty()) data.append('\n'); data.append(line.substring(5).removePrefix(" ")) }
                }
            }
            dispatch()
            if (!completed) throw ProviderException("stream_interrupted", "The response stream ended before completion.")
            return Round(text.toString(), calls, raw)
        }
    }

    /** Models available to this API key that the analyst can use. */
    fun models(): List<String> {
        http.newCall(request("/models", null)).execute().use { res ->
            if (!res.isSuccessful) throw ProviderException("http_${res.code}", "OpenAI API key check failed (${res.code}).", res.code)
            return json.parseToJsonElement(res.body.string()).jsonObject["data"]!!.jsonArray
                .mapNotNull { it.jsonObject["id"]?.jsonPrimitive?.contentOrNull }
                .filter { it.matches(Regex("^(gpt|o\\d).*")) }.sorted()
        }
    }
}
