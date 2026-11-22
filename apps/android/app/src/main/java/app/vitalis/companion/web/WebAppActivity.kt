package app.vitalis.companion.web

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.OpenableColumns
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.widget.FrameLayout
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.health.connect.client.HealthConnectClient
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import app.vitalis.companion.BuildConfig
import app.vitalis.companion.MainActivity
import app.vitalis.companion.engine.EngineException
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.engine.ThermalReminderWorker
import app.vitalis.companion.hc.HealthConnectSource
import app.vitalis.companion.sync.SyncRepository
import app.vitalis.companion.sync.summary
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * Full VITALIS on the phone: the desktop's React screens (built with `npm run build:android`, bundled under assets/web)
 * in a WebView, served from the app's own assets at https://appassets.androidplatform.net. The page talks to the
 * on-phone engine only through an origin-restricted message channel and only with the desktop's allow-listed methods;
 * phone actions (Health Connect import, desktop sync, file import, reminder, delete) are separate `phone:*` channels.
 */
class WebAppActivity : ComponentActivity() {
    companion object {
        const val ORIGIN = "https://appassets.androidplatform.net"
        const val START_URL = "$ORIGIN/assets/web/index.html"
        const val EXTRA_ROUTE = "route"
    }

    private val scope = MainScope()
    private val json = Json { ignoreUnknownKeys = true }
    private lateinit var web: WebView
    private var page: JavaScriptReplyProxy? = null
    private val imports = ConcurrentHashMap<String, File>()
    private var pickResult: CompletableDeferred<Uri?>? = null
    private var notifyResult: CompletableDeferred<Boolean>? = null
    private val pickFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { pickResult?.complete(it) }
    private val askNotifications = registerForActivityResult(ActivityResultContracts.RequestPermission()) { notifyResult?.complete(it) }
    private val allowed: Set<String> by lazy {
        assets.open("web/engine-methods.json").use { s -> json.parseToJsonElement(s.reader().readText()).jsonArray.map { it.jsonPrimitive.content }.toSet() }
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        web = WebView(this)
        val root = FrameLayout(this).apply { setBackgroundColor(Color.parseColor("#0e1116")); addView(web, FrameLayout.LayoutParams(MATCH_PARENT, MATCH_PARENT)) }
        setContentView(root)
        // Edge-to-edge (Android 15+): keep the app's own header and tabs clear of the status and navigation bars.
        WindowCompat.getInsetsController(window, root).isAppearanceLightStatusBars = false
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout() or WindowInsetsCompat.Type.ime())
            v.setPadding(b.left, b.top, b.right, b.bottom)
            WindowInsetsCompat.CONSUMED
        }
        val loader = WebViewAssetLoader.Builder().addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this)).build()
        web.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true // remembered theme / background / date range
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
        val route = intent.getStringExtra(EXTRA_ROUTE)?.takeIf { it.matches(Regex("^[a-z]{2,20}$")) }
        web.loadUrl(if (route != null) "$START_URL#/$route" else START_URL)
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (web.canGoBack()) web.goBack() else { isEnabled = false; onBackPressedDispatcher.onBackPressed() }
            }
        })
        scope.launch { startEngineAndImport() }
    }

    private suspend fun startEngineAndImport() {
        try {
            val eng = PhoneEngine.get(this)
            scope.launch { eng.events.collect { ev -> emit("engine:event", ev) } }
            runImport(full = false)
        } catch (e: Exception) {
            emit("phone:event", buildJsonObject { put("type", "import_error"); put("message", "Could not start the analysis engine: ${e.message}") })
        }
    }

    /** Health Connect → this phone's engine, with progress shown inside the app. */
    private suspend fun runImport(full: Boolean): JsonObject {
        val counts = PhoneEngine.importHealthConnect(this, full) { text ->
            emit("phone:event", buildJsonObject { put("type", "import_progress"); put("text", text) })
        }
        val n = counts.values.sum()
        emit("phone:event", buildJsonObject { put("type", "import_done"); put("records", n); put("summary", counts.summary()) })
        return buildJsonObject { put("records", n); put("summary", counts.summary()) }
    }

    private fun emit(channel: String, payload: JsonElement) = runOnUiThread {
        page?.postMessage(buildJsonObject { put("kind", "emit"); put("channel", channel); put("payload", payload) }.toString())
    }

    @androidx.annotation.VisibleForTesting
    internal fun webViewForTest(): WebView = web

    private fun ok(data: JsonElement) = buildJsonObject { put("ok", true); put("data", data) }
    private fun fail(code: String, message: String) = buildJsonObject {
        put("ok", false); putJsonObject("error") { put("code", code); put("message", message) }
    }

    private fun JsonElement.obj(): JsonObject = this as? JsonObject ?: JsonObject(emptyMap())
    private fun JsonObject.bool(k: String) = this[k]?.jsonPrimitive?.booleanOrNull ?: false
    private fun JsonObject.str(k: String) = this[k]?.jsonPrimitive?.contentOrNull

    /** The renderer's IPC channels that make sense on the phone; everything else is reported as desktop-only. */
    private suspend fun handle(channel: String, payload: JsonElement): JsonObject = try {
        val p = payload.obj()
        when (channel) {
            "app:info" -> ok(buildJsonObject { put("version", BuildConfig.VERSION_NAME); put("platform", "android"); put("packaged", true) })
            "profiles:list" -> {
                val prof = buildJsonObject { put("id", "phone"); put("name", "This phone"); put("demo", false); put("timezone", PhoneEngine.timezone()) }
                ok(buildJsonObject { putJsonArray("profiles") { add(prof) }; put("active", prof); put("last", "phone") })
            }
            "engine:call" -> {
                val method = p.str("method") ?: ""
                if (method !in allowed) fail("method_not_allowed", "Not available to the app screens: $method")
                else ok(PhoneEngine.get(this).call(method, p["params"].let { it as? JsonObject } ?: JsonObject(emptyMap())))
            }
            "app:openExternal" -> {
                val url = p.str("url") ?: ""
                if (!url.startsWith("https://")) fail("invalid_url", "Only https links can be opened.")
                else { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))); ok(JsonNull) }
            }
            "phone:status" -> ok(status())
            "phone:sync" -> ok(runImport(p.bool("full")))
            "phone:probe" -> {
                val repo = SyncRepository(this)
                val report = repo.hc.availabilityProbe()
                val payloadWithReport = JsonObject(repo.payloadObject(emptyList(), emptyList(), 0, "probe") + ("availability" to report))
                PhoneEngine.get(this).call("ingest.local_batch", buildJsonObject { put("payload", payloadWithReport) })
                ok(report)
            }
            "phone:pcSync" -> {
                val r = SyncRepository(this).syncNow(full = p.bool("full")) { emit("phone:event", buildJsonObject { put("type", "import_progress"); put("text", it) }) }
                emit("phone:event", buildJsonObject { put("type", if (r.ok) "import_done" else "import_error"); put("records", 0); put("summary", r.message); put("message", r.message) })
                if (r.ok) ok(JsonPrimitive(r.message)) else fail("pc_sync_failed", r.message)
            }
            "phone:openCompanion" -> { startActivity(Intent(this, MainActivity::class.java)); ok(JsonNull) }
            "phone:reminder" -> {
                val enabled = p.bool("enabled")
                val time = p.str("time")?.takeIf { it.matches(Regex("^([01]\\d|2[0-3]):[0-5]\\d$")) } ?: "21:00"
                if (enabled && Build.VERSION.SDK_INT >= 33 &&
                    ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    notifyResult = CompletableDeferred()
                    askNotifications.launch(Manifest.permission.POST_NOTIFICATIONS)
                    if (!notifyResult!!.await()) return fail("notifications_denied", "Notifications are off for VITALIS; allow them in Android settings.")
                }
                val store = SyncRepository(this).store
                store.put("thermal_reminder", if (enabled) "1" else "0"); store.put("thermal_reminder_time", time)
                ThermalReminderWorker.schedule(this, enabled, time)
                ok(JsonNull)
            }
            "phone:wipe" -> {
                if (p.str("confirm") != "DELETE") fail("confirm_required", "Type DELETE to confirm.")
                else {
                    PhoneEngine.wipe(this)
                    scope.launch { delay(300); recreate() } // start again with a fresh, empty database
                    ok(JsonNull)
                }
            }
            "import:pick" -> {
                if (p.str("kind") != "samsung_export") fail("not_available_on_phone", "Only the Samsung Health export can be imported on the phone.")
                else {
                    pickResult = CompletableDeferred()
                    pickFile.launch(arrayOf("application/zip", "text/csv", "text/comma-separated-values", "application/octet-stream"))
                    val uri = pickResult!!.await()
                    if (uri == null) ok(JsonNull) else {
                        val file = copyToCache(uri)
                        val preview = PhoneEngine.get(this).call("import.preview", buildJsonObject { put("kind", "samsung_export"); put("path", file.absolutePath) })
                        val token = UUID.randomUUID().toString()
                        imports[token] = file
                        ok(buildJsonObject { put("token", token); put("preview", preview); put("fileName", file.name) })
                    }
                }
            }
            "import:commit" -> {
                val file = imports.remove(p.str("token") ?: "") ?: return fail("expired", "Import expired; choose the file again.")
                try {
                    emit("phone:event", buildJsonObject { put("type", "import_progress"); put("text", "Importing ${file.name}…") })
                    val r = PhoneEngine.get(this).call("import.commit", buildJsonObject { put("kind", "samsung_export"); put("path", file.absolutePath) }, timeoutMs = 1_800_000)
                    emit("phone:event", buildJsonObject { put("type", "import_done"); put("records", 1); put("summary", "from ${file.name}") })
                    ok(r)
                } finally { file.delete() }
            }
            else -> fail("not_available_on_phone", "This is available in the VITALIS desktop app.")
        }
    } catch (e: EngineException) {
        fail(e.code, e.message ?: "engine error")
    } catch (e: Exception) {
        fail("error", e.message ?: e.javaClass.simpleName)
    }

    private suspend fun status(): JsonObject = withContext(Dispatchers.IO) {
        val repo = SyncRepository(this@WebAppActivity)
        val available = repo.hc.sdkStatus() == HealthConnectClient.SDK_AVAILABLE
        val granted = if (available) repo.hc.granted() else emptySet()
        val (lastAt, lastSummary) = PhoneEngine.lastImport(this@WebAppActivity)
        buildJsonObject {
            putJsonObject("healthConnect") {
                put("available", available)
                put("granted", (granted - setOf(HealthConnectSource.HISTORY_PERMISSION, HealthConnectSource.BACKGROUND_PERMISSION)).size)
                put("history", HealthConnectSource.HISTORY_PERMISSION in granted)
                put("background", HealthConnectSource.BACKGROUND_PERMISSION in granted)
            }
            put("importing", PhoneEngine.importing)
            putJsonObject("lastImport") { put("at", lastAt); put("summary", lastSummary) }
            putJsonObject("pc") {
                put("paired", repo.isPaired()); put("name", repo.pairing()?.name)
                put("lastSync", repo.store.get("last_sync")); put("lastResult", repo.store.get("last_result"))
            }
            putJsonObject("reminder") {
                put("enabled", repo.store.get("thermal_reminder") == "1"); put("time", repo.store.get("thermal_reminder_time") ?: "21:00")
            }
        }
    }

    /** Copies a picked document into app-private cache (the engine reads files by path). */
    private suspend fun copyToCache(uri: Uri): File = withContext(Dispatchers.IO) {
        val name = contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        }?.replace(Regex("[^A-Za-z0-9._-]"), "_")?.take(120) ?: "import.zip"
        val dir = File(cacheDir, "imports").apply { mkdirs() }
        val out = File(dir, "${UUID.randomUUID()}-$name")
        contentResolver.openInputStream(uri)!!.use { input -> out.outputStream().use { input.copyTo(it) } }
        out
    }

    override fun onDestroy() {
        scope.cancel()
        imports.values.forEach { it.delete() }
        web.destroy()
        super.onDestroy()
    }
}
