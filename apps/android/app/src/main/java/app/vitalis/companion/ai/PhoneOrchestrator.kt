package app.vitalis.companion.ai

import android.content.Context
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.sync.SecureStore
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * The AI analyst on the phone — same steps as the desktop (apps/desktop/electron/ai/orchestrator.ts):
 * consent preview of the exact request before every cloud round → provider round → validated read-only engine tools
 * → … → stored answer with evidence. Nothing is sent without the user's approval; approvals are logged (hash only).
 */
class PhoneOrchestrator(
    private val context: Context,
    private val emit: (JsonObject) -> Unit,
    /** Tests point this at a mock server; the app always uses api.openai.com. */
    private val clientFactory: (apiKey: () -> String) -> OpenAiResponses = { key -> OpenAiResponses(key) },
) {
    companion object {
        const val MAX_ROUNDS = 8
        const val KEY = "openai_api_key"
        private val pretty = Json { prettyPrint = true }

        fun categories(round: Int, toolNames: List<String>): List<String> = buildList {
            add("Your question and conversation history from this thread"); add("Analyst instructions")
            add("Tool definitions (metric and non-private habit names)")
            if (round > 1) add("Results of read-only analytics tools: ${toolNames.distinct().joinToString(", ")}")
        }
    }

    private val consents = ConcurrentHashMap<String, CompletableDeferred<Pair<Boolean, Boolean>>>()
    private val running = ConcurrentHashMap<String, Pair<Job, OpenAiResponses>>()

    fun decide(requestId: String, approve: Boolean, approveRemaining: Boolean) {
        consents.remove(requestId)?.complete(approve to approveRemaining)
    }

    fun cancel(requestId: String) {
        running[requestId]?.let { (job, provider) -> provider.cancel(); job.cancel() }
        decide(requestId, false, false)
    }

    private fun ev(type: String, requestId: String, block: JsonObjectBuilder.() -> Unit = {}) =
        emit(buildJsonObject { put("type", type); put("requestId", requestId); block() })

    /** Starts a turn; returns its request id at once. Events go to [emit] as the desktop's AiEvent objects. */
    fun ask(scope: CoroutineScope, req: JsonObject): String {
        val requestId = UUID.randomUUID().toString()
        val provider = req["provider"]?.jsonPrimitive?.contentOrNull
        val model = req["model"]?.jsonPrimitive?.contentOrNull ?: ""
        val threadId = req["threadId"]!!.jsonPrimitive.content
        val message = req["message"]!!.jsonPrimitive.content.take(8000)
        val chart = req["chartContext"]?.takeIf { it !is JsonNull }
        val client = clientFactory { SecureStore(context).get(KEY) ?: throw ProviderException("no_api_key", "Add an OpenAI API key under Settings → AI.") }
        val job = scope.launch(Dispatchers.IO) {
            try {
                if (provider != "openai_api") throw ProviderException("not_available_on_phone", "On the phone the analyst uses an OpenAI API key.")
                ev("status", requestId) { put("text", "Preparing analysis…") }
                val eng = PhoneEngine.get(context)
                val prompt = eng.call("ai.system_prompt", buildJsonObject { put("provider_kind", "openai_api"); put("chart_context", chart ?: JsonNull) })
                    .jsonObject["prompt"]!!.jsonPrimitive.content
                val tools = eng.call("ai.tools", buildJsonObject { put("cloud", true) }).jsonArray
                val prior = eng.call("chat.messages", buildJsonObject { put("thread_id", threadId) }).jsonArray.map { it.jsonObject }
                val history = prior.filter { it["role"]?.jsonPrimitive?.content in setOf("user", "assistant") }.takeLast(16)
                    .map { it["role"]!!.jsonPrimitive.content to it["content"]!!.jsonPrimitive.content.take(12000) }
                eng.call("chat.append", buildJsonObject { put("thread_id", threadId); put("role", "user"); put("content", message) })
                if (prior.isEmpty()) eng.call("chat.thread_rename", buildJsonObject { put("id", threadId); put("title", message.take(80)) })
                val items = client.startConversation(history, message)
                val evidence = LinkedHashMap<String, JsonElement>()
                val toolLog = mutableListOf<JsonObject>()
                var approveAll = false
                var finalText = ""
                for (round in 1..MAX_ROUNDS) {
                    val body = client.buildBody(prompt, items, model, tools)
                    val payload = pretty.encodeToString(JsonObject.serializer(), body)
                    val bytes = payload.toByteArray(Charsets.UTF_8)
                    val names = toolLog.map { it["name"]!!.jsonPrimitive.content }
                    if (!approveAll) {
                        val decision = CompletableDeferred<Pair<Boolean, Boolean>>()
                        consents[requestId] = decision // registered before emitting so an immediate decision is not lost
                        ev("consent", requestId) {
                            put("round", round); put("provider", "openai_api"); put("model", model); put("payload", payload)
                            putJsonArray("categories") { categories(round, names).forEach { add(it) } }; put("bytes", bytes.size)
                        }
                        val (approve, remaining) = decision.await()
                        if (!approve) {
                            ev("error", requestId) { put("code", "declined"); put("message", "Nothing was sent. The request was cancelled at your choice.") }
                            return@launch
                        }
                        approveAll = remaining
                    }
                    eng.call("ai.disclosure_log", buildJsonObject {
                        put("thread_id", threadId); put("provider", "openai_api"); put("model", model)
                        putJsonArray("categories") { categories(round, names).forEach { add(it) } }
                        put("payload_sha256", MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) })
                        put("payload_bytes", bytes.size)
                    })
                    ev("status", requestId) { put("text", if (round == 1) "Thinking…" else "Interpreting results…") }
                    val res = client.send(body) { d -> ev("delta", requestId) { put("text", d) } }
                    if (res.calls.isEmpty()) { finalText = res.text; break }
                    val outputs = mutableListOf<Pair<String, String>>()
                    for (call in res.calls) {
                        val args = runCatching { Json.parseToJsonElement(call.arguments.ifEmpty { "{}" }) }.getOrElse { JsonPrimitive(call.arguments) }
                        ev("tool_call", requestId) { put("name", call.name); put("arguments", args) }
                        val r = eng.call("ai.call", buildJsonObject { put("name", call.name); put("arguments", call.arguments.ifEmpty { "{}" }); put("cloud", true) }).jsonObject
                        val ok = r["ok"]?.jsonPrimitive?.booleanOrNull == true
                        val err = r["error"]?.jsonObject?.get("message")?.jsonPrimitive?.contentOrNull
                        toolLog += buildJsonObject { put("name", call.name); put("arguments", args); put("ok", ok); err?.let { put("error", it) } }
                        ev("tool_call", requestId) { put("name", call.name); put("arguments", args); put("ok", ok); err?.let { put("error", it) } }
                        if (ok) r["result"]?.jsonObject?.get("evidence")?.jsonArray?.forEach { e -> evidence[e.jsonObject["id"]!!.jsonPrimitive.content] = e }
                        outputs += call.callId to (if (ok) r["result"]!! else buildJsonObject { put("error", r["error"] ?: JsonNull) }).toString()
                    }
                    client.appendRound(items, res, outputs)
                    if (round == MAX_ROUNDS) finalText = res.text.ifEmpty { "I could not finish the analysis within the allowed number of steps." }
                }
                val successful = toolLog.count { it["ok"]?.jsonPrimitive?.booleanOrNull == true }
                val unverified = successful == 0 && Regex("\\d").containsMatchIn(finalText.replace(Regex("\\[\\[ev:[^\\]]+\\]\\]"), ""))
                val saved = eng.call("chat.append", buildJsonObject {
                    put("thread_id", threadId); put("role", "assistant"); put("content", finalText)
                    put("tool_calls", JsonArray(toolLog)); put("evidence", JsonArray(evidence.values.toList()))
                    put("provider", "openai_api"); put("model", model)
                }).jsonObject
                ev("done", requestId) {
                    put("messageId", saved["id"]!!); put("content", finalText); put("evidence", JsonArray(evidence.values.toList()))
                    put("toolCalls", successful); put("unverifiedNumbers", unverified)
                }
            } catch (e: ProviderException) {
                ev("error", requestId) { put("code", e.code); put("message", e.message ?: "error"); put("manageUsage", false) }
            } catch (e: kotlinx.coroutines.CancellationException) {
                ev("error", requestId) { put("code", "cancelled"); put("message", "Cancelled.") }
            } catch (e: Exception) {
                ev("error", requestId) { put("code", (e as? app.vitalis.companion.engine.EngineException)?.code ?: "error"); put("message", e.message ?: e.javaClass.simpleName) }
            } finally {
                running.remove(requestId)
                consents.remove(requestId)
            }
        }
        running[requestId] = job to client
        return requestId
    }

    suspend fun models(): JsonArray = withContext(Dispatchers.IO) {
        val client = clientFactory { SecureStore(context).get(KEY) ?: throw ProviderException("no_api_key", "Add an OpenAI API key first.") }
        JsonArray(client.models().map { buildJsonObject { put("id", it); put("label", it) } })
    }
}
