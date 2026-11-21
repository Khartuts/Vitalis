package app.vitalis.companion.engine

import android.content.Context
import android.os.Build
import androidx.health.connect.client.HealthConnectClient
import app.vitalis.companion.hc.HealthConnectSource
import app.vitalis.companion.sync.SecureStore
import app.vitalis.companion.sync.SyncCounts
import app.vitalis.companion.sync.SyncRepository
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.io.File
import java.time.ZoneId

/** The phone's own VITALIS profile: one encrypted database in app-private storage, analysed by the on-phone engine. */
object PhoneEngine {
    private val lock = Mutex()
    private val importLock = Mutex()
    private var host: EngineHost? = null

    fun profileName(): String = "${Build.MANUFACTURER} ${Build.MODEL}".trim()
    fun timezone(): String = ZoneId.systemDefault().id

    suspend fun get(context: Context): EngineHost = lock.withLock {
        host ?: EngineHost(context.applicationContext).also { h ->
            h.open(File(context.filesDir, "vitalis-phone.db"), EngineHost.databaseKey(SecureStore(context)),
                demo = false, timezone = timezone(), profileName = profileName())
            host = h
        }
    }

    /**
     * Imports Health Connect data into the phone's engine: everything from the oldest readable record on the first run,
     * then only changes. Uses its own change tokens ("local_"), so the desktop sync is unaffected.
     */
    suspend fun importHealthConnect(context: Context, full: Boolean = false, progress: (String) -> Unit = {}): SyncCounts =
        importLock.withLock {
            val repo = SyncRepository(context)
            if (repo.hc.sdkStatus() != HealthConnectClient.SDK_AVAILABLE) return@withLock SyncCounts()
            val readable = repo.hc.granted() - setOf(HealthConnectSource.HISTORY_PERMISSION, HealthConnectSource.BACKGROUND_PERMISSION)
            if (readable.isEmpty()) return@withLock SyncCounts()
            val eng = get(context)
            var seq = 0
            repo.collectWithCursors("local_", full, null, { recs, dels, cursor ->
                progress("Importing ${recs.size} records…")
                eng.call("ingest.local_batch", buildJsonObject { put("payload", repo.payloadObject(recs, dels, seq++, cursor)) })
            }, progress)
        }
}
