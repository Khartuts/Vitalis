package app.vitalis.companion.sync

import android.content.Context
import android.os.Build
import app.vitalis.companion.BuildConfig
import app.vitalis.companion.engine.EngineHost
import app.vitalis.companion.engine.PhoneEngine
import app.vitalis.companion.hc.HcSerializer
import app.vitalis.companion.hc.HealthConnectSource
import app.vitalis.companion.samsung.SamsungSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.util.UUID

data class SyncResult(val ok: Boolean, val message: String, val records: Int = 0, val batches: Int = 0)

/** Records sent per type (label → count), in reading order. */
typealias SyncCounts = LinkedHashMap<String, Int>

fun SyncCounts.summary(): String =
    if (isEmpty() || values.all { it == 0 }) "no new records"
    else entries.filter { it.value > 0 }.joinToString(", ") { "${it.key} ${it.value}" }

/** Builds vitalis.sync.v1 batches and delivers them to the paired desktop (or into an encrypted file). */
class SyncRepository(private val context: Context) {
    val store = SecureStore(context)
    val hc = HealthConnectSource(context)
    private val json = Json { encodeDefaults = true }

    companion object {
        const val MAX_BATCH_RECORDS = 4000
        const val WINDOW_DAYS = 30L
        /** History reads start at the oldest readable record, but never more than this far back. */
        const val MAX_HISTORY_DAYS = 3650L

        fun chunk(items: List<JsonObject>, size: Int = MAX_BATCH_RECORDS): List<List<JsonObject>> =
            if (items.isEmpty()) emptyList() else items.chunked(size)

        fun windows(start: Instant, end: Instant, days: Long = WINDOW_DAYS): List<Pair<Instant, Instant>> {
            val out = mutableListOf<Pair<Instant, Instant>>()
            var s = start
            while (s < end) {
                val e = minOf(end, s.plus(Duration.ofDays(days)))
                out += s to e
                s = e
            }
            return out
        }
    }

    // ------------------------------------------------------------------------------------------ pairing state
    fun pairing(): PairingInfo? = store.get("pairing")?.let { runCatching { Json.decodeFromString(PairingInfo.serializer(), it) }.getOrNull() }
    fun token(): String? = store.get("token")
    fun isPaired() = pairing() != null && token() != null

    suspend fun pair(info: PairingInfo): String = withContext(Dispatchers.IO) {
        val client = SyncClient(info.hosts, info.port, info.fp)
        client.hello()
        val (deviceId, token) = client.pair(info.code, "${Build.MANUFACTURER} ${Build.MODEL}")
        store.put("pairing", Json.encodeToString(PairingInfo.serializer(), info))
        store.put("token", token)
        store.put("device_id", deviceId)
        deviceId
    }

    fun unpair() {
        store.clearAll()
    }

    private fun client(): SyncClient {
        val p = pairing() ?: throw IllegalStateException("Not paired with a desktop.")
        return SyncClient(p.hosts, p.port, p.fp)
    }

    private fun payload(records: List<JsonObject>, deletions: List<String>, seq: Int, cursor: String): String =
        payloadObject(records, deletions, seq, cursor).toString()

    fun payloadObject(records: List<JsonObject>, deletions: List<String>, seq: Int, cursor: String): JsonObject = buildJsonObject {
        put("schema", "vitalis.sync.v1")
        put("batch_id", UUID.randomUUID().toString())
        put("source", "health_connect")
        put("seq", seq)
        put("cursor", cursor)
        put("app_version", BuildConfig.VERSION_NAME)
        putJsonObject("device") { put("manufacturer", Build.MANUFACTURER); put("model", Build.MODEL); put("android", Build.VERSION.SDK_INT) }
        put("records", JsonArray(records))
        putJsonArray("deletions") { deletions.forEach { id -> add(buildJsonObject { put("id", id) }) } }
    }

    // ------------------------------------------------------------------------------------------ collection
    /**
     * Collects records to send. `deliver` is invoked per batch; cursors are saved only after it returns.
     * full = true ignores saved change tokens and re-reads everything Health Connect lets this app read, starting at
     * the oldest record of each type (the desktop recognizes records it already has, so this is safe to repeat).
     */
    private suspend fun collect(full: Boolean, sinceDays: Long?, deliver: suspend (records: List<JsonObject>, deletions: List<String>, cursor: String) -> Unit,
                                progress: (String) -> Unit): SyncCounts = collectWithCursors("", full, sinceDays, deliver, progress)

    /**
     * Same as [collect], with its own change tokens: `cursorPrefix` separates the phone's own engine ("local_") from the
     * desktop sync (""), so each receives every change exactly once.
     */
    suspend fun collectWithCursors(cursorPrefix: String, full: Boolean, sinceDays: Long?,
                                   deliver: suspend (records: List<JsonObject>, deletions: List<String>, cursor: String) -> Unit,
                                   progress: (String) -> Unit): SyncCounts {
        val granted = hc.granted()
        val zone = ZoneId.systemDefault()
        val now = Instant.now()
        val floor = now.minus(Duration.ofDays(MAX_HISTORY_DAYS))
        val counts = SyncCounts()
        suspend fun readFrom(spec: app.vitalis.companion.hc.TypeSpec): Instant? =
            if (sinceDays != null) now.minus(Duration.ofDays(sinceDays))
            else try { hc.oldest(spec)?.let { maxOf(it, floor) } } catch (ex: SecurityException) { null }
        for (spec in HealthConnectSource.TYPES) {
            if (spec.permission !in granted || !hc.featureAvailable(spec)) continue
            if (spec.name in HealthConnectSource.AGGREGATED) continue
            counts.putIfAbsent(spec.label, 0)
            val tokenKey = "${cursorPrefix}chg_${spec.name}"
            val existing = if (full) null else store.get(tokenKey)
            if (existing == null) {
                val newToken = hc.changesToken(spec) // taken before reading so nothing between read and token is lost
                val start = readFrom(spec) // null: Health Connect has no record of this type for this app
                if (start != null) for ((s, e) in windows(start, now)) {
                    progress("${spec.label}: reading ${s.atZone(zone).toLocalDate()}…")
                    val recs = try { hc.readRaw(spec, s, e) } catch (ex: Exception) {
                        if (ex is SecurityException) emptyList() else throw ex
                    }
                    val ser = recs.mapNotNull { HcSerializer.serialize(it) }
                    for (part in chunk(ser)) { deliver(part, emptyList(), "${spec.name}:history"); counts[spec.label] = counts.getValue(spec.label) + part.size }
                }
                if (sinceDays == null) store.put(tokenKey, newToken)
            } else {
                progress("${spec.label}: checking changes…")
                val ch = hc.changesSince(existing)
                if (ch.expired) {
                    store.put(tokenKey, null) // token unused >30 days: re-read history next time
                    progress("${spec.label}: change history expired, will re-read")
                    continue
                }
                val ser = ch.upserts.mapNotNull { HcSerializer.serialize(it) }
                val parts = chunk(ser).ifEmpty { if (ch.deletions.isNotEmpty()) listOf(emptyList()) else emptyList() }
                parts.forEachIndexed { i, part ->
                    deliver(part, if (i == 0) ch.deletions else emptyList(), "${spec.name}:changes")
                    counts[spec.label] = counts.getValue(spec.label) + part.size
                }
                store.put(tokenKey, ch.nextToken)
            }
        }
        // De-duplicated activity totals. Incremental: re-read from (last end − 2 days); otherwise from the oldest record.
        val lastAggEnd = if (full || sinceDays != null) null else store.get("${cursorPrefix}agg_end")?.let { Instant.parse(it) }
        val aggStart = when {
            sinceDays != null -> now.minus(Duration.ofDays(sinceDays))
            lastAggEnd != null -> lastAggEnd.minus(Duration.ofDays(2))
            else -> HealthConnectSource.TYPES.filter { it.name in HealthConnectSource.AGGREGATED && it.permission in granted }
                .mapNotNull { readFrom(it) }.minOrNull()
        }
        if (aggStart != null) {
            counts.putIfAbsent("Activity totals", 0)
            for ((s, e) in windows(aggStart, now)) {
                progress("Activity totals ${s.atZone(zone).toLocalDate()}…")
                val agg = hc.readAggregates(granted, s, e, zone)
                for (part in chunk(agg)) { deliver(part, emptyList(), "aggregates"); counts["Activity totals"] = counts.getValue("Activity totals") + part.size }
            }
        }
        if (sinceDays == null) store.put("${cursorPrefix}agg_end", now.toString())
        return counts
    }

    /** full = true: "Re-read all history" (ignores change tokens; see [collect]). */
    suspend fun syncNow(full: Boolean = false, progress: (String) -> Unit = {}): SyncResult = withContext(Dispatchers.IO) {
        val token = token() ?: return@withContext SyncResult(false, "Pair with your desktop first.")
        val client = client()
        var seq = 0
        try {
            client.hello()
            val counts = collect(full = full, sinceDays = null, deliver = { recs, dels, cursor ->
                progress("Sending batch ${seq + 1} (${recs.size} records)…")
                client.sendBatch(payload(recs, dels, seq++, cursor), token)
            }, progress = progress)
            sendSamsung(client, token, progress)
            val journal = exchangeJournal(client, token, progress)
            val n = counts.values.sum()
            val msg = (if (full) "Re-read all history: " else "Synced ") + "$n records in $seq batches (${counts.summary()})." +
                (journal?.let { " $it" } ?: "")
            store.put("last_sync", Instant.now().toString()); store.put("last_result", msg)
            SyncResult(true, msg, n, seq)
        } catch (e: SyncHttpException) {
            if (e.status == 401) SyncResult(false, "The desktop no longer accepts this phone (pairing revoked or expired). Please pair again.")
            else SyncResult(false, "Desktop rejected the data: ${e.message}")
        } catch (e: Exception) {
            SyncResult(false, e.message ?: e.javaClass.simpleName)
        }
    }

    /**
     * Journal and experiments both ways with the PC (phone app with the on-phone engine only): the phone sends what it
     * changed since the last exchange, applies what the PC changed, and keeps both bookmarks. Newer records win.
     */
    private suspend fun exchangeJournal(client: SyncClient, token: String, progress: (String) -> Unit): String? {
        if (!EngineHost.AVAILABLE) return null
        progress("Journal: exchanging with the PC…")
        return try {
            val eng = PhoneEngine.get(context)
            val local = eng.call("journal.export_changes", buildJsonObject { put("since", store.get("journal_sent_until")) }).jsonObject
            val resp = client.exchangeJournal(buildJsonObject { put("since", store.get("journal_pc_cursor")); put("changes", local) }.toString(), token)
            val pcChanges = resp["changes"]!!.jsonObject
            val applied = eng.call("journal.import_changes", buildJsonObject { put("changes", pcChanges) }).jsonObject
            store.put("journal_sent_until", local["now"]!!.jsonPrimitive.content)
            store.put("journal_pc_cursor", pcChanges["now"]!!.jsonPrimitive.content)
            val sent = listOf("entries", "entry_tombstones", "habits", "experiments").sumOf { local[it]?.jsonArray?.size ?: 0 }
            val got = listOf("entries", "deleted_entries", "habits", "experiments", "deleted").sumOf { applied[it]?.jsonPrimitive?.intOrNull ?: 0 }
            "Journal: $sent change(s) sent, $got received."
        } catch (e: SyncHttpException) {
            if (e.status == 404) "Journal sync needs VITALIS 0.5 or newer on the PC." else "Journal sync failed: ${e.message}"
        }
    }

    private suspend fun sendSamsung(client: SyncClient, token: String, progress: (String) -> Unit) {
        val samsung = SamsungSource.create(context)
        if (!samsung.isConnected()) return
        progress("Samsung Health: reading…")
        val since = store.get("samsung_since")?.let { Instant.parse(it) } ?: Instant.now().minus(Duration.ofDays(30))
        val recs = samsung.readSince(since)
        for (part in chunk(recs)) {
            val body = buildJsonObject {
                put("schema", "vitalis.sync.v1"); put("batch_id", UUID.randomUUID().toString()); put("source", "samsung_sdk")
                put("app_version", BuildConfig.VERSION_NAME); put("records", JsonArray(part)); putJsonArray("deletions") {}
            }.toString()
            client.sendBatch(body, token)
        }
        store.put("samsung_since", Instant.now().minus(Duration.ofDays(2)).toString())
    }

    suspend fun sendAvailability(): JsonObject = withContext(Dispatchers.IO) {
        val report = hc.availabilityProbe()
        val t = token()
        if (t != null) runCatching { client().sendAvailability(report.toString(), t) }
        report
    }

    /** Encrypted snapshot of the last [days] days for transfer by USB / cloud drive when the network path is unavailable. */
    suspend fun exportEncrypted(days: Long, progress: (String) -> Unit): ByteArray = withContext(Dispatchers.IO) {
        val key = pairing()?.fileKey() ?: throw IllegalStateException("Pair (scan the QR) first — the file key comes from the desktop.")
        val all = mutableListOf<JsonObject>()
        val n = collect(full = true, sinceDays = days, deliver = { recs, _, _ -> all += recs }, progress = progress).values.sum()
        progress("Encrypting $n records…")
        FileCrypto.encrypt(key, payload(all, emptyList(), 0, "file:$days").toByteArray(Charsets.UTF_8))
    }
}
