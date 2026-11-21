package app.vitalis.companion.web

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.view.Gravity
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import app.vitalis.companion.BuildConfig
import app.vitalis.companion.engine.EngineException
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.sync.summary
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

/**
 * Full VITALIS on the phone: the desktop's React screens (built with `npm run build:android`, bundled under assets/web)
 * in a WebView, served from the app's own assets at https://appassets.androidplatform.net. The page talks to the
 * on-phone engine only through an origin-restricted message channel and only with the desktop's allow-listed methods.
 */
class WebAppActivity : ComponentActivity() {
    companion object {
        const val ORIGIN = "https://appassets.androidplatform.net"
        const val START_URL = "$ORIGIN/assets/web/index.html"
    }

    private val scope = MainScope()
    private val json = Json { ignoreUnknownKeys = true }
    private lateinit var web: WebView
    private lateinit var status: TextView
    private var page: JavaScriptReplyProxy? = null
    private val allowed: Set<String> by lazy {
        assets.open("web/engine-methods.json").use { s -> json.parseToJsonElement(s.reader().readText()).jsonArray.map { it.jsonPrimitive.content }.toSet() }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        status = TextView(this).apply { setPadding(24, 12, 24, 12); gravity = Gravity.CENTER; textSize = 12f; visibility = TextView.GONE }
        web = WebView(this)
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(status, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
            addView(web, LinearLayout.LayoutParams(MATCH_PARENT, 0, 1f))
        })
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true // remembered theme / date range
            allowFileAccess = false
            allowContentAccess = false
            setSupportMultipleWindows(false)
        }
        web.webViewClient = object : WebViewClientCompat() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                loader.shouldInterceptRequest(request.url)
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                !request.url.toString().startsWith("$ORIGIN/") // never navigate away from the bundled app
        }
        check(WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) { "This WebView is too old for VITALIS." }
        WebViewCompat.addWebMessageListener(web, "VitalisAndroid", setOf(ORIGIN)) { _, message, _, isMainFrame, reply ->
            if (!isMainFrame) return@addWebMessageListener
            val msg = runCatching { json.parseToJsonElement(message.data ?: "").jsonObject }.getOrNull() ?: return@addWebMessageListener
            page = reply
            if (msg["kind"]?.jsonPrimitive?.contentOrNull == "invoke") {
                val id = msg["id"]?.jsonPrimitive?.intOrNull ?: return@addWebMessageListener
                val channel = msg["channel"]?.jsonPrimitive?.contentOrNull ?: ""
                val payload = msg["payload"] ?: JsonNull
                scope.launch {
                    val result = handle(channel, payload)
                    reply.postMessage(buildJsonObject { put("kind", "resolve"); put("id", id); put("result", result) }.toString())
                }
            }
        }
        web.loadUrl(START_URL)
        scope.launch { startEngineAndImport() }
    }

    private suspend fun startEngineAndImport() {
        try {
            val eng = PhoneEngine.get(this)
            scope.launch { eng.events.collect { ev -> emit("engine:event", ev) } }
            showStatus("Reading Health Connect…")
            val counts = PhoneEngine.importHealthConnect(this) { showStatus(it) }
            showStatus(if (counts.values.sum() > 0) "Imported: ${counts.summary()}" else null)
        } catch (e: Exception) {
            showStatus("Could not start the analysis engine: ${e.message}")
        }
    }

    @androidx.annotation.VisibleForTesting
    internal fun webViewForTest(): WebView = web

    private fun showStatus(text: String?) = runOnUiThread {
        status.text = text ?: ""
        status.visibility = if (text.isNullOrEmpty()) TextView.GONE else TextView.VISIBLE
    }

    private fun emit(channel: String, payload: JsonElement) = runOnUiThread {
        page?.postMessage(buildJsonObject { put("kind", "emit"); put("channel", channel); put("payload", payload) }.toString())
    }

    private fun ok(data: JsonElement) = buildJsonObject { put("ok", true); put("data", data) }
    private fun fail(code: String, message: String) = buildJsonObject {
        put("ok", false); putJsonObject("error") { put("code", code); put("message", message) }
    }

    /** The renderer's IPC channels that make sense on the phone; everything else is reported as desktop-only. */
    private suspend fun handle(channel: String, payload: JsonElement): JsonObject = try {
        when (channel) {
            "app:info" -> ok(buildJsonObject { put("version", BuildConfig.VERSION_NAME); put("platform", "android"); put("packaged", true) })
            "profiles:list" -> {
                val p = buildJsonObject { put("id", "phone"); put("name", PhoneEngine.profileName()); put("demo", false); put("timezone", PhoneEngine.timezone()) }
                ok(buildJsonObject { putJsonArray("profiles") { add(p) }; put("active", p); put("last", "phone") })
            }
            "engine:call" -> {
                val o = payload.jsonObject
                val method = o["method"]?.jsonPrimitive?.contentOrNull ?: ""
                if (method !in allowed) fail("method_not_allowed", "Not available to the app screens: $method")
                else ok(PhoneEngine.get(this).call(method, (o["params"] as? JsonObject) ?: JsonObject(emptyMap())))
            }
            "app:openExternal" -> {
                val url = payload.jsonObject["url"]?.jsonPrimitive?.contentOrNull ?: ""
                if (!url.startsWith("https://")) fail("invalid_url", "Only https links can be opened.")
                else { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))); ok(JsonNull) }
            }
            else -> fail("not_available_on_phone", "This is available in the VITALIS desktop app.")
        }
    } catch (e: EngineException) {
        fail(e.code, e.message ?: "engine error")
    } catch (e: Exception) {
        fail("error", e.message ?: e.javaClass.simpleName)
    }

    override fun onDestroy() {
        scope.cancel()
        web.destroy()
        super.onDestroy()
    }
}
