package app.vitalis.companion

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import app.vitalis.companion.hc.HealthConnectSource
import app.vitalis.companion.samsung.SamsungSource
import app.vitalis.companion.sync.Pairing
import app.vitalis.companion.sync.SyncRepository
import app.vitalis.companion.sync.SyncWorker
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

private val Bg = Color(0xFF0E1116)
private val Surface1 = Color(0xFF151A21)
private val Line = Color(0xFF262E39)
private val Fg = Color(0xFFE8EBF0)
private val Fg2 = Color(0xFFA9B1BD)
private val Accent = Color(0xFF14A294)
private val Warn = Color(0xFFFAB219)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme(primary = Accent, background = Bg, surface = Surface1, onSurface = Fg, onBackground = Fg)) {
                Surface(Modifier.fillMaxSize(), color = Bg) { CompanionScreen(this) }
            }
        }
    }
}

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().padding(vertical = 6.dp).background(Surface1, RoundedCornerShape(12.dp)).padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(title, color = Fg, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
        content()
    }
}

@Composable
fun CompanionScreen(activity: ComponentActivity) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val repo = remember { SyncRepository(ctx.applicationContext) }
    val samsung = remember { SamsungSource.create(ctx) }
    var paired by remember { mutableStateOf(repo.isPaired()) }
    var sdkStatus by remember { mutableIntStateOf(repo.hc.sdkStatus()) }
    var granted by remember { mutableStateOf<Set<String>>(emptySet()) }
    var selected by remember { mutableStateOf(HealthConnectSource.TYPES.map { it.permission }.toSet() + HealthConnectSource.HISTORY_PERMISSION) }
    var busy by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf<String?>(null) }
    var pasted by remember { mutableStateOf("") }
    var probe by remember { mutableStateOf<JsonObject?>(null) }
    var samsungStatus by remember { mutableStateOf("…") }
    var exportDays by remember { mutableIntStateOf(30) }
    var pendingExport by remember { mutableStateOf<ByteArray?>(null) }
    var autoSync by remember { mutableStateOf(repo.store.get("auto_sync") == "1") }

    suspend fun refreshGranted() {
        if (sdkStatus == HealthConnectClient.SDK_AVAILABLE) granted = runCatching { repo.hc.granted() }.getOrDefault(emptySet())
    }
    LaunchedEffect(Unit) { refreshGranted(); samsungStatus = samsung.status() }

    val permLauncher = rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) { result ->
        granted = result
        status = "Health Connect: ${result.size} permissions granted. Denied types are simply skipped."
    }
    val doPair: (String) -> Unit = { text ->
        scope.launch {
            busy = true
            status = try {
                val info = Pairing.parse(text)
                repo.pair(info)
                paired = true
                "Paired with ${info.name}. Certificate pinned."
            } catch (e: Exception) { e.message ?: "Pairing failed" }
            busy = false
        }
    }
    val scanLauncher = rememberLauncherForActivityResult(ScanContract()) { r -> r.contents?.let(doPair) }
    val createDoc = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri: Uri? ->
        val data = pendingExport
        if (uri != null && data != null) {
            ctx.contentResolver.openOutputStream(uri)?.use { it.write(data) }
            status = "Encrypted file saved (${data.size / 1024} KB). Import it on the desktop under Settings → Imports."
        }
        pendingExport = null
    }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp)) {
        Text("VITALIS", color = Fg, fontSize = 22.sp, fontWeight = FontWeight.SemiBold, letterSpacing = 4.sp)
        Text("Companion · reads your health data with your permission and sends it only to your paired PC.", color = Fg2, fontSize = 12.sp)
        Spacer(Modifier.height(8.dp))
        status?.let { Text(it, color = Accent, fontSize = 13.sp) }
        if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())

        Section("Health Connect") {
            when (sdkStatus) {
                HealthConnectClient.SDK_AVAILABLE -> Text("Available · ${granted.size} permissions granted", color = Fg2, fontSize = 13.sp)
                HealthConnectClient.SDK_UNAVAILABLE_PROVIDER_UPDATE_REQUIRED -> {
                    Text("Health Connect needs to be installed or updated.", color = Warn, fontSize = 13.sp)
                    Button(onClick = {
                        ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.google.android.apps.healthdata")))
                    }) { Text("Open Play Store") }
                    TextButton(onClick = { sdkStatus = repo.hc.sdkStatus() }) { Text("Check again") }
                }
                else -> Text("Health Connect is not available on this device.", color = Warn, fontSize = 13.sp)
            }
            Text("In Samsung Health → Settings → Health Connect, allow Samsung Health to share data with Health Connect.", color = Fg2, fontSize = 12.sp)
        }

        Section(if (paired) "Paired with ${repo.pairing()?.name ?: "desktop"}" else "Pair with your PC") {
            if (!paired) {
                Text("On the PC: Settings → Phone sync → Enable → Pair a phone. Then scan the QR code.", color = Fg2, fontSize = 12.sp)
                Button(enabled = !busy, onClick = {
                    scanLauncher.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the VITALIS pairing code").setBeepEnabled(false))
                }) { Text("Scan QR code") }
                OutlinedTextField(pasted, { pasted = it }, label = { Text("…or paste the pairing text") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                OutlinedButton(enabled = pasted.isNotBlank() && !busy, onClick = { doPair(pasted) }) { Text("Pair") }
            } else {
                Text("Last sync: ${repo.store.get("last_sync") ?: "never"}\n${repo.store.get("last_result") ?: ""}", color = Fg2, fontSize = 12.sp)
                Button(enabled = !busy && granted.isNotEmpty(), onClick = {
                    scope.launch { busy = true; val r = repo.syncNow { status = it }; status = r.message; busy = false }
                }) { Text("Sync now") }
                OutlinedButton(enabled = !busy && granted.isNotEmpty(), onClick = {
                    scope.launch { busy = true; val r = repo.syncNow(full = true) { status = it }; status = r.message; busy = false }
                }) { Text("Re-read all history") }
                Text("Reads everything Health Connect lets this app see again, from the oldest record. Use it after granting " +
                    "\"Access past data\" or after Samsung Health starts sharing. Records the PC already has are skipped.",
                    color = Fg2, fontSize = 11.sp)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Switch(checked = autoSync, onCheckedChange = {
                        autoSync = it; repo.store.put("auto_sync", if (it) "1" else "0"); SyncWorker.schedule(ctx, it)
                    }, enabled = HealthConnectSource.BACKGROUND_PERMISSION in granted)
                    Spacer(Modifier.width(8.dp))
                    Text(if (HealthConnectSource.BACKGROUND_PERMISSION in granted) "Sync hourly in the background" else "Hourly sync needs the background-read permission",
                        color = Fg2, fontSize = 12.sp)
                }
            }
        }

        Section("Permissions (read-only, each optional)") {
            Text("Choose what VITALIS may read. You can change this any time in Health Connect.", color = Fg2, fontSize = 12.sp)
            HealthConnectSource.TYPES.forEach { spec ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = spec.permission in selected, onCheckedChange = { on -> selected = if (on) selected + spec.permission else selected - spec.permission })
                    Text(spec.label + if (spec.permission in granted) "  ✓" else "", color = Fg, fontSize = 13.sp)
                }
            }
            listOf(HealthConnectSource.HISTORY_PERMISSION to "Access past data (more than 30 days)",
                HealthConnectSource.BACKGROUND_PERMISSION to "Read in the background (for hourly sync)").forEach { (p, l) ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(checked = p in selected, onCheckedChange = { on -> selected = if (on) selected + p else selected - p })
                    Text(l + if (p in granted) "  ✓" else "", color = Fg, fontSize = 13.sp)
                }
            }
            Button(enabled = sdkStatus == HealthConnectClient.SDK_AVAILABLE && selected.isNotEmpty(), onClick = { permLauncher.launch(selected) }) {
                Text("Request selected permissions")
            }
        }

        Section("Check what my phone shares") {
            Text("Counts the records of each type in the last 30 days, shows which apps wrote them and the oldest record " +
                "Health Connect lets VITALIS read — so you know exactly what your watch shares and how far back.", color = Fg2, fontSize = 12.sp)
            OutlinedButton(enabled = !busy && granted.isNotEmpty(), onClick = {
                scope.launch { busy = true; probe = repo.sendAvailability(); status = "Availability report ${if (paired) "sent to the PC" else "created"}."; busy = false }
            }) { Text("Run availability probe") }
            probe?.get("types")?.jsonArray?.forEach { t ->
                val o = t.jsonObject
                val count = o["count"]?.jsonPrimitive?.int ?: 0
                val oldest = o["oldest"]?.jsonPrimitive?.contentOrNull?.take(10)
                val err = o["error"]?.jsonPrimitive?.contentOrNull
                val state = when (o["permission"]?.jsonPrimitive?.contentOrNull) {
                    "granted" -> if (count > 0) "$count records in 30 days" else "0 records in 30 days"
                    "feature_unavailable" -> "not available on this phone"
                    else -> "permission not granted"
                }
                val line = "${o["label"]?.jsonPrimitive?.content}: $state" + (oldest?.let { " · oldest $it" } ?: "") + (err?.let { " · error: $it" } ?: "")
                Text(line, color = if (count > 0) Fg else Fg2, fontSize = 12.sp)
            }
        }

        Section("No network? Export an encrypted file") {
            Text("Creates an AES-256-GCM encrypted .vitalis-sync file (key from pairing) for USB or cloud-drive transfer.", color = Fg2, fontSize = 12.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf(7, 30, 90, 365).forEach { d -> FilterChip(selected = exportDays == d, onClick = { exportDays = d }, label = { Text("$d d") }) }
            }
            OutlinedButton(enabled = paired && !busy && granted.isNotEmpty(), onClick = {
                scope.launch {
                    busy = true
                    try {
                        pendingExport = repo.exportEncrypted(exportDays.toLong()) { status = it }
                        createDoc.launch("vitalis-${java.time.LocalDate.now()}.vitalis-sync")
                    } catch (e: Exception) { status = e.message }
                    busy = false
                }
            }) { Text("Export encrypted file") }
        }

        Section("Samsung Health Data SDK (optional)") {
            Text(samsungStatus, color = Fg2, fontSize = 12.sp)
            if (samsung.isBundled()) {
                OutlinedButton(onClick = { scope.launch { status = runCatching { "Samsung: ${samsung.connect(activity).size} data types granted" }.getOrElse { it.message }; samsungStatus = samsung.status() } }) {
                    Text("Connect Samsung Health")
                }
            }
        }

        if (paired) {
            TextButton(onClick = { repo.unpair(); SyncWorker.schedule(ctx, false); paired = false; status = "Unpaired. Pairing data and tokens removed from this phone." }) {
                Text("Unpair and forget this PC", color = Warn)
            }
        }
        TextButton(onClick = { ctx.startActivity(Intent(ctx, PermissionsRationaleActivity::class.java)) }) { Text("Privacy policy") }
        Spacer(Modifier.height(24.dp))
    }
}
