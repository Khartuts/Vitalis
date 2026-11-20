package app.vitalis.companion.samsung

// ============================================================================================================
// UNVERIFIED BUILD PATH — compiled only when app/libs/samsung-health-data-api.aar is present.
// The AAR and its API reference require a Samsung developer login, so this file could not be compiled or run in
// the VITALIS build environment. It uses ONLY the class/method names shown in Samsung's public programming guide
// and code labs (2026-11): HealthDataService.getStore, HealthDataStore.{getGrantedPermissions, requestPermissions,
// readData, aggregateData}, Permission.of, AccessType.READ, DataTypes.{SLEEP, HEART_RATE, BLOOD_OXYGEN,
// SKIN_TEMPERATURE, STEPS}, DataType.SleepType.{SLEEP_SCORE, DURATION, SESSIONS},
// DataType.BloodOxygenType.OXYGEN_SATURATION, DataType.StepsType.TOTAL, LocalTimeFilter, LocalTimeGroup,
// LocalTimeGroupUnit, Ordering, ResolvablePlatformException.resolve, HealthDataException.
// Field names that are only in the login-gated reference (heart-rate sample value, skin temperature, energy score,
// sleep-stage objects) are discovered by reflection at runtime and sent as generic "fields"; the desktop maps
// what it recognizes and reports the rest as unmapped. Expect to adjust imports when first compiling.
// ============================================================================================================

import android.app.Activity
import android.content.Context
import com.samsung.android.sdk.health.data.HealthDataService
import com.samsung.android.sdk.health.data.HealthDataStore
import com.samsung.android.sdk.health.data.error.HealthDataException
import com.samsung.android.sdk.health.data.error.ResolvablePlatformException
import com.samsung.android.sdk.health.data.permission.AccessType
import com.samsung.android.sdk.health.data.permission.Permission
import com.samsung.android.sdk.health.data.request.DataType
import com.samsung.android.sdk.health.data.request.DataTypes
import com.samsung.android.sdk.health.data.request.LocalTimeFilter
import com.samsung.android.sdk.health.data.request.LocalTimeGroup
import com.samsung.android.sdk.health.data.request.LocalTimeGroupUnit
import com.samsung.android.sdk.health.data.request.Ordering
import kotlinx.serialization.json.*
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId

class SamsungSourceImpl(private val context: Context) : SamsungSource {
    private val store: HealthDataStore by lazy { HealthDataService.getStore(context) }

    private val readTypes = listOf("SLEEP" to DataTypes.SLEEP, "HEART_RATE" to DataTypes.HEART_RATE,
        "BLOOD_OXYGEN" to DataTypes.BLOOD_OXYGEN, "SKIN_TEMPERATURE" to DataTypes.SKIN_TEMPERATURE, "STEPS" to DataTypes.STEPS)

    /** Additional documented data types looked up by name (reference is login-gated). */
    private fun optionalType(name: String): Any? = runCatching { DataTypes::class.java.getField(name).get(null) }.getOrNull()

    private fun permissions(): Set<Permission> {
        val types = readTypes.map { it.second } + listOfNotNull(optionalType("ENERGY_SCORE"), optionalType("EXERCISE"),
            optionalType("BODY_COMPOSITION"), optionalType("BLOOD_PRESSURE"))
        return types.map { Permission.of(it as DataType, AccessType.READ) }.toSet()
    }

    override fun isBundled() = true

    override suspend fun status(): String = try {
        val granted = store.getGrantedPermissions(permissions())
        if (granted.isEmpty()) "Ready — no Samsung Health permissions granted yet" else "Connected (${granted.size} data types)"
    } catch (e: ResolvablePlatformException) {
        "Samsung Health must be installed or updated (${e.message})"
    } catch (e: HealthDataException) {
        // e.g. 2003 "Permission not allowed due to SDK policy": enable Samsung Health developer mode for personal testing
        "Samsung Health Data SDK unavailable: ${e.message}. For personal testing enable Developer Mode for Data Read."
    }

    override suspend fun isConnected(): Boolean = runCatching { store.getGrantedPermissions(permissions()).isNotEmpty() }.getOrDefault(false)

    override suspend fun connect(activity: Activity): List<String> = try {
        store.requestPermissions(permissions(), activity).map { it.toString() }
    } catch (e: ResolvablePlatformException) {
        e.resolve(activity)
        emptyList()
    }

    override suspend fun readSince(since: Instant): List<JsonObject> {
        val zone = ZoneId.systemDefault()
        val filter = LocalTimeFilter.of(LocalDateTime.ofInstant(since, zone), LocalDateTime.now(zone))
        val out = mutableListOf<JsonObject>()
        val granted = store.getGrantedPermissions(permissions())
        fun has(t: Any) = granted.any { it.toString().contains(t.toString()) }

        if (has(DataTypes.SLEEP)) {
            val req = DataTypes.SLEEP.readDataRequestBuilder.setLocalTimeFilter(filter).setOrdering(Ordering.ASC).build()
            for (p in store.readData(req).dataList) {
                out += buildJsonObject {
                    put("type", "SAMSUNG_SDK.SLEEP"); put("uid", p.uid)
                    put("startTime", p.startTime.toString()); p.endTime?.let { put("endTime", it.toString()) }
                    p.zoneOffset?.let { put("zoneOffset", it.id) }
                    putJsonObject("fields") {
                        p.getValue(DataType.SleepType.SLEEP_SCORE)?.let { put("SLEEP_SCORE", it.toString().toDoubleOrNull()) }
                        p.getValue(DataType.SleepType.DURATION)?.let { put("DURATION_MS", it.toMillis()) }
                    }
                    putJsonArray("sessions") {
                        for (s in p.getValue(DataType.SleepType.SESSIONS) ?: emptyList()) add(reflectSession(s))
                    }
                }
            }
        }
        if (has(DataTypes.BLOOD_OXYGEN)) {
            val req = DataTypes.BLOOD_OXYGEN.readDataRequestBuilder.setLocalTimeFilter(filter).setOrdering(Ordering.ASC).build()
            for (p in store.readData(req).dataList) {
                out += buildJsonObject {
                    put("type", "SAMSUNG_SDK.BLOOD_OXYGEN"); put("uid", p.uid); put("startTime", p.startTime.toString())
                    p.zoneOffset?.let { put("zoneOffset", it.id) }
                    putJsonObject("fields") { p.getValue(DataType.BloodOxygenType.OXYGEN_SATURATION)?.let { put("OXYGEN_SATURATION", it.toString().toDoubleOrNull()) } }
                }
            }
        }
        if (has(DataTypes.STEPS)) {
            val req = DataType.StepsType.TOTAL.requestBuilder
                .setLocalTimeFilterWithGroup(filter, LocalTimeGroup.of(LocalTimeGroupUnit.HOURLY, 1)).setOrdering(Ordering.ASC).build()
            for (a in store.aggregateData(req).dataList) {
                out += buildJsonObject {
                    put("type", "SAMSUNG_SDK.STEPS_AGGREGATE"); put("uid", "steps:${a.startTime}")
                    put("startTime", a.startTime.toString()); put("endTime", a.endTime.toString())
                    putJsonObject("fields") { put("TOTAL", (a.value as? Number)?.toDouble()) }
                }
            }
        }
        for ((name, type) in listOf("HEART_RATE" to DataTypes.HEART_RATE, "SKIN_TEMPERATURE" to DataTypes.SKIN_TEMPERATURE)) {
            if (!has(type)) continue
            val req = type.readDataRequestBuilder.setLocalTimeFilter(filter).setOrdering(Ordering.ASC).build()
            for (p in store.readData(req).dataList) out += reflectPoint("SAMSUNG_SDK.$name", p, "${name.lowercase().replace("_", "")}Type")
        }
        return out
    }

    /** Serializes all readable fields of a data point by enumerating DataType.<X>Type field constants via reflection. */
    private fun reflectPoint(type: String, p: com.samsung.android.sdk.health.data.data.HealthDataPoint, typeClassHint: String): JsonObject =
        buildJsonObject {
            put("type", type); put("uid", p.uid); put("startTime", p.startTime.toString())
            p.endTime?.let { put("endTime", it.toString()) }; p.zoneOffset?.let { put("zoneOffset", it.id) }
            putJsonObject("fields") {
                val cls = DataType::class.java.declaredClasses.firstOrNull { it.simpleName.equals(typeClassHint, ignoreCase = true) }
                cls?.fields?.forEach { f ->
                    val key = runCatching { f.get(null) }.getOrNull() ?: return@forEach
                    val v = runCatching {
                        p.javaClass.methods.first { it.name == "getValue" && it.parameterCount == 1 }.invoke(p, key)
                    }.getOrNull()
                    when (v) {
                        is Number -> put(f.name, v.toDouble())
                        is String -> put(f.name, v)
                        null -> Unit
                        else -> put(f.name, v.toString())
                    }
                }
            }
        }

    private fun reflectSession(s: Any): JsonObject = buildJsonObject {
        fun prop(n: String) = runCatching { s.javaClass.getMethod("get" + n.replaceFirstChar { it.uppercase() }).invoke(s) }.getOrNull()
        put("startTime", prop("startTime").toString()); put("endTime", prop("endTime").toString())
        putJsonArray("stages") {
            (prop("stages") as? List<*>)?.forEach { st ->
                if (st == null) return@forEach
                fun sp(n: String) = runCatching { st.javaClass.getMethod("get" + n.replaceFirstChar { it.uppercase() }).invoke(st) }.getOrNull()
                add(buildJsonObject { put("startTime", sp("startTime").toString()); put("endTime", sp("endTime").toString()); put("stage", sp("stage").toString()) })
            }
        }
    }
}
