package app.vitalis.companion.hc

import android.content.Context
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.HealthConnectFeatures
import androidx.health.connect.client.changes.DeletionChange
import androidx.health.connect.client.changes.UpsertionChange
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.*
import androidx.health.connect.client.records.metadata.Device
import androidx.health.connect.client.records.metadata.Metadata
import androidx.health.connect.client.request.AggregateGroupByDurationRequest
import androidx.health.connect.client.request.AggregateGroupByPeriodRequest
import androidx.health.connect.client.request.ChangesTokenRequest
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import kotlinx.serialization.json.*
import java.time.Duration
import java.time.Instant
import java.time.LocalDateTime
import java.time.Period
import java.time.ZoneId
import java.time.ZoneOffset
import kotlin.reflect.KClass

/** One readable Health Connect record type with its read permission. */
data class TypeSpec(val name: String, val cls: KClass<out Record>, val label: String, val featureGated: Boolean = false) {
    val permission: String get() = HealthPermission.getReadPermission(cls)
}

/**
 * Reads authorized Health Connect data and serializes it to the VITALIS sync wire format (vitalis.sync.v1).
 * Strictly read-only: this app never writes to Health Connect.
 */
class HealthConnectSource(private val context: Context) {

    companion object {
        val TYPES = listOf(
            TypeSpec("HeartRateRecord", HeartRateRecord::class, "Heart rate"),
            TypeSpec("RestingHeartRateRecord", RestingHeartRateRecord::class, "Resting heart rate"),
            TypeSpec("HeartRateVariabilityRmssdRecord", HeartRateVariabilityRmssdRecord::class, "Heart rate variability (RMSSD)"),
            TypeSpec("SleepSessionRecord", SleepSessionRecord::class, "Sleep (sessions and stages)"),
            TypeSpec("StepsRecord", StepsRecord::class, "Steps"),
            TypeSpec("ExerciseSessionRecord", ExerciseSessionRecord::class, "Exercise sessions"),
            TypeSpec("DistanceRecord", DistanceRecord::class, "Distance"),
            TypeSpec("ActiveCaloriesBurnedRecord", ActiveCaloriesBurnedRecord::class, "Active calories"),
            TypeSpec("TotalCaloriesBurnedRecord", TotalCaloriesBurnedRecord::class, "Total calories"),
            TypeSpec("FloorsClimbedRecord", FloorsClimbedRecord::class, "Floors climbed"),
            TypeSpec("OxygenSaturationRecord", OxygenSaturationRecord::class, "Blood oxygen (SpO2)"),
            TypeSpec("RespiratoryRateRecord", RespiratoryRateRecord::class, "Respiratory rate"),
            TypeSpec("SkinTemperatureRecord", SkinTemperatureRecord::class, "Skin temperature", featureGated = true),
            TypeSpec("BodyTemperatureRecord", BodyTemperatureRecord::class, "Body temperature"),
            TypeSpec("BloodPressureRecord", BloodPressureRecord::class, "Blood pressure"),
            TypeSpec("BloodGlucoseRecord", BloodGlucoseRecord::class, "Blood glucose"),
            TypeSpec("WeightRecord", WeightRecord::class, "Weight"),
            TypeSpec("BodyFatRecord", BodyFatRecord::class, "Body fat"),
            TypeSpec("LeanBodyMassRecord", LeanBodyMassRecord::class, "Lean body mass"),
            TypeSpec("BodyWaterMassRecord", BodyWaterMassRecord::class, "Body water mass"),
            TypeSpec("BoneMassRecord", BoneMassRecord::class, "Bone mass"),
            TypeSpec("BasalMetabolicRateRecord", BasalMetabolicRateRecord::class, "Basal metabolic rate"),
            TypeSpec("HeightRecord", HeightRecord::class, "Height"),
            TypeSpec("Vo2MaxRecord", Vo2MaxRecord::class, "VO2 max"),
            TypeSpec("HydrationRecord", HydrationRecord::class, "Hydration"),
            TypeSpec("NutritionRecord", NutritionRecord::class, "Nutrition"),
        )

        /** Types whose totals are read via the de-duplicating aggregate API instead of raw records. */
        val AGGREGATED = setOf("StepsRecord", "DistanceRecord", "ActiveCaloriesBurnedRecord")

        const val HISTORY_PERMISSION = HealthPermission.PERMISSION_READ_HEALTH_DATA_HISTORY
        const val BACKGROUND_PERMISSION = HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND

        fun allPermissions(): Set<String> = TYPES.map { it.permission }.toSet() + HISTORY_PERMISSION + BACKGROUND_PERMISSION
    }

    fun sdkStatus(): Int = HealthConnectClient.getSdkStatus(context)

    val client: HealthConnectClient by lazy { HealthConnectClient.getOrCreate(context) }

    suspend fun granted(): Set<String> = client.permissionController.getGrantedPermissions()

    @Suppress("OPT_IN_USAGE")
    fun featureAvailable(spec: TypeSpec): Boolean {
        if (!spec.featureGated) return true
        return try {
            client.features.getFeatureStatus(HealthConnectFeatures.FEATURE_SKIN_TEMPERATURE) ==
                HealthConnectFeatures.FEATURE_STATUS_AVAILABLE
        } catch (e: Throwable) {
            false
        }
    }

    // ---------------------------------------------------------------------------------------------- reading
    suspend fun readRaw(spec: TypeSpec, start: Instant, end: Instant): List<Record> {
        val out = mutableListOf<Record>()
        var token: String? = null
        do {
            val res = client.readRecords(ReadRecordsRequest(spec.cls, TimeRangeFilter.between(start, end), pageSize = 1000, pageToken = token))
            out += res.records
            token = res.pageToken
        } while (!token.isNullOrEmpty()) // some platform versions return "" instead of null at the end
        return out
    }

    /**
     * Time of the oldest record of this type that Health Connect lets this app read, or null when there is none.
     * Health Connect applies the permission rules itself: without "Access past data" only records from 30 days
     * before the first grant onward are visible.
     */
    suspend fun oldest(spec: TypeSpec): Instant? {
        val res = client.readRecords(ReadRecordsRequest(spec.cls, TimeRangeFilter.before(Instant.now()), ascendingOrder = true, pageSize = 1))
        return res.records.firstOrNull()?.let { firstTime(it) }
    }

    /** Hourly (and daily) totals via HC aggregation, which de-duplicates overlapping sources. */
    suspend fun readAggregates(granted: Set<String>, start: Instant, end: Instant, zone: ZoneId): List<JsonObject> {
        val out = mutableListOf<JsonObject>()
        val metrics = buildList {
            if (HealthPermission.getReadPermission(StepsRecord::class) in granted) add(Triple("StepsAggregateHourly", StepsRecord.COUNT_TOTAL, "count"))
            if (HealthPermission.getReadPermission(DistanceRecord::class) in granted) add(Triple("DistanceAggregateHourly", DistanceRecord.DISTANCE_TOTAL, "m"))
            if (HealthPermission.getReadPermission(ActiveCaloriesBurnedRecord::class) in granted) add(Triple("ActiveCaloriesAggregateHourly", ActiveCaloriesBurnedRecord.ACTIVE_CALORIES_TOTAL, "kcal"))
        }
        if (metrics.isEmpty()) return out
        val groups = client.aggregateGroupByDuration(
            AggregateGroupByDurationRequest(metrics.map { it.second }.toSet(), TimeRangeFilter.between(start, end), Duration.ofHours(1)),
        )
        for (g in groups) {
            for ((type, metric, _) in metrics) {
                val raw = g.result[metric] ?: continue
                val value = when (raw) {
                    is Long -> raw.toDouble()
                    is androidx.health.connect.client.units.Length -> raw.inMeters
                    is androidx.health.connect.client.units.Energy -> raw.inKilocalories
                    else -> continue
                }
                if (value <= 0.0) continue
                out += buildJsonObject {
                    put("type", type)
                    put("startTime", g.startTime.toString()); put("endTime", g.endTime.toString())
                    put("startZoneOffset", g.zoneOffset.id)
                    put("value", value)
                    putJsonArray("dataOrigins") { g.result.dataOrigins.forEach { add(it.packageName) } }
                }
            }
        }
        if (HealthPermission.getReadPermission(StepsRecord::class) in granted) {
            val days = client.aggregateGroupByPeriod(
                AggregateGroupByPeriodRequest(setOf(StepsRecord.COUNT_TOTAL),
                    TimeRangeFilter.between(LocalDateTime.ofInstant(start, zone).toLocalDate().atStartOfDay(),
                        LocalDateTime.ofInstant(end, zone)), Period.ofDays(1)),
            )
            for (d in days) {
                val v = d.result[StepsRecord.COUNT_TOTAL] ?: continue
                val s = d.startTime.atZone(zone)
                out += buildJsonObject {
                    put("type", "StepsAggregateDaily")
                    put("startTime", s.toInstant().toString()); put("endTime", d.endTime.atZone(zone).toInstant().toString())
                    put("startZoneOffset", s.offset.id); put("value", v)
                    putJsonArray("dataOrigins") { d.result.dataOrigins.forEach { add(it.packageName) } }
                }
            }
        }
        return out
    }

    // ---------------------------------------------------------------------------------------------- changes
    suspend fun changesToken(spec: TypeSpec): String = client.getChangesToken(ChangesTokenRequest(setOf(spec.cls)))

    data class ChangeBatch(val upserts: List<Record>, val deletions: List<String>, val nextToken: String, val expired: Boolean)

    suspend fun changesSince(token: String): ChangeBatch {
        val ups = mutableListOf<Record>()
        val dels = mutableListOf<String>()
        var t = token
        while (true) {
            val r = client.getChanges(t)
            if (r.changesTokenExpired) return ChangeBatch(emptyList(), emptyList(), t, true)
            for (c in r.changes) {
                when (c) {
                    is UpsertionChange -> if (c.record.metadata.dataOrigin.packageName != context.packageName) ups += c.record
                    is DeletionChange -> dels += c.recordId
                }
            }
            t = r.nextChangesToken
            if (!r.hasMore) break
        }
        return ChangeBatch(ups, dels, t, false)
    }

    // ---------------------------------------------------------------------------------------------- probe
    /**
     * Reports, per type: permission state, how many records exist in the last 30 days, from which apps and devices,
     * the newest record in that window and the oldest record readable at all (so missing history is visible).
     */
    suspend fun availabilityProbe(): JsonObject {
        val granted = granted()
        val end = Instant.now()
        val start = end.minus(Duration.ofDays(30))
        return buildJsonObject {
            put("provider", "health_connect")
            put("generated_at", end.toString())
            put("history_permission", HISTORY_PERMISSION in granted)
            put("background_permission", BACKGROUND_PERMISSION in granted)
            putJsonArray("types") {
                for (spec in TYPES) {
                    add(buildJsonObject {
                        put("type", spec.name); put("label", spec.label)
                        val perm = spec.permission in granted
                        val feature = featureAvailable(spec)
                        put("permission", if (!feature) "feature_unavailable" else if (perm) "granted" else "not_granted")
                        if (perm && feature) {
                            try {
                                val recs = readRaw(spec, start, end)
                                put("count", recs.size)
                                putJsonArray("origins") { recs.map { it.metadata.dataOrigin.packageName }.distinct().take(8).forEach { add(it) } }
                                putJsonArray("devices") { recs.mapNotNull { it.metadata.device?.model }.distinct().take(5).forEach { add(it) } }
                                recs.maxOfOrNull { lastTime(it) }?.let { put("newest", it.toString()) }
                                oldest(spec)?.let { put("oldest", it.toString()) }
                            } catch (e: Exception) {
                                put("count", 0); put("error", e.javaClass.simpleName + ": " + (e.message ?: ""))
                            }
                        } else put("count", 0)
                    })
                }
            }
        }
    }

    private fun firstTime(r: Record): Instant? {
        when (r) { // types synced via aggregates have no serializer branch
            is StepsRecord -> return r.startTime
            is DistanceRecord -> return r.startTime
            is ActiveCaloriesBurnedRecord -> return r.startTime
            else -> {}
        }
        val o = HcSerializer.serialize(r) ?: return null
        val t = (o["time"] ?: o["startTime"])?.jsonPrimitive?.contentOrNull ?: return null
        return runCatching { Instant.parse(t) }.getOrNull()
    }

    private fun lastTime(r: Record): Instant {
        val o = HcSerializer.serialize(r) ?: return Instant.EPOCH
        val t = (o["time"] ?: o["endTime"])?.jsonPrimitive?.contentOrNull ?: return Instant.EPOCH
        return runCatching { Instant.parse(t) }.getOrDefault(Instant.EPOCH)
    }
}

/** Serializes Health Connect records to the wire format consumed by vitalis_engine/ingest/health_connect.py. */
object HcSerializer {
    fun deviceType(t: Int): String = when (t) {
        Device.TYPE_WATCH -> "WATCH"; Device.TYPE_PHONE -> "PHONE"; Device.TYPE_SCALE -> "SCALE"; Device.TYPE_RING -> "RING"
        Device.TYPE_HEAD_MOUNTED -> "HEAD_MOUNTED"; Device.TYPE_FITNESS_BAND -> "FITNESS_BAND"; Device.TYPE_CHEST_STRAP -> "CHEST_STRAP"
        Device.TYPE_SMART_DISPLAY -> "SMART_DISPLAY"; else -> "UNKNOWN"
    }

    fun recordingMethod(m: Int): String = when (m) {
        Metadata.RECORDING_METHOD_ACTIVELY_RECORDED -> "actively_recorded"
        Metadata.RECORDING_METHOD_AUTOMATICALLY_RECORDED -> "automatically_recorded"
        Metadata.RECORDING_METHOD_MANUAL_ENTRY -> "manual_entry"
        else -> "unknown"
    }

    fun stage(s: Int): String = when (s) {
        SleepSessionRecord.STAGE_TYPE_AWAKE -> "awake"; SleepSessionRecord.STAGE_TYPE_SLEEPING -> "sleeping"
        SleepSessionRecord.STAGE_TYPE_OUT_OF_BED -> "out_of_bed"; SleepSessionRecord.STAGE_TYPE_LIGHT -> "light"
        SleepSessionRecord.STAGE_TYPE_DEEP -> "deep"; SleepSessionRecord.STAGE_TYPE_REM -> "rem"
        SleepSessionRecord.STAGE_TYPE_AWAKE_IN_BED -> "awake_in_bed"; else -> "unknown"
    }

    fun exercise(t: Int): String = when (t) {
        ExerciseSessionRecord.EXERCISE_TYPE_RUNNING -> "running"
        ExerciseSessionRecord.EXERCISE_TYPE_RUNNING_TREADMILL -> "running_treadmill"
        ExerciseSessionRecord.EXERCISE_TYPE_WALKING -> "walking"
        ExerciseSessionRecord.EXERCISE_TYPE_HIKING -> "hiking"
        ExerciseSessionRecord.EXERCISE_TYPE_BIKING -> "biking"
        ExerciseSessionRecord.EXERCISE_TYPE_BIKING_STATIONARY -> "biking_stationary"
        ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_POOL -> "swimming_pool"
        ExerciseSessionRecord.EXERCISE_TYPE_SWIMMING_OPEN_WATER -> "swimming_open_water"
        ExerciseSessionRecord.EXERCISE_TYPE_STRENGTH_TRAINING -> "strength_training"
        ExerciseSessionRecord.EXERCISE_TYPE_WEIGHTLIFTING -> "weightlifting"
        ExerciseSessionRecord.EXERCISE_TYPE_YOGA -> "yoga"
        ExerciseSessionRecord.EXERCISE_TYPE_PILATES -> "pilates"
        ExerciseSessionRecord.EXERCISE_TYPE_HIGH_INTENSITY_INTERVAL_TRAINING -> "hiit"
        ExerciseSessionRecord.EXERCISE_TYPE_ELLIPTICAL -> "elliptical"
        ExerciseSessionRecord.EXERCISE_TYPE_ROWING_MACHINE -> "rowing_machine"
        ExerciseSessionRecord.EXERCISE_TYPE_STAIR_CLIMBING -> "stair_climbing"
        ExerciseSessionRecord.EXERCISE_TYPE_TENNIS -> "tennis"
        ExerciseSessionRecord.EXERCISE_TYPE_SOCCER -> "soccer"
        ExerciseSessionRecord.EXERCISE_TYPE_BASKETBALL -> "basketball"
        ExerciseSessionRecord.EXERCISE_TYPE_STRETCHING -> "stretching"
        ExerciseSessionRecord.EXERCISE_TYPE_OTHER_WORKOUT -> "other"
        else -> "type_$t"
    }

    private fun JsonObjectBuilder.common(r: Record, type: String) {
        put("type", type)
        put("id", r.metadata.id)
        put("origin", r.metadata.dataOrigin.packageName)
        put("recordingMethod", recordingMethod(r.metadata.recordingMethod))
        put("lastModified", r.metadata.lastModifiedTime.toString())
        r.metadata.clientRecordId?.let { put("clientRecordId", it) }
        r.metadata.device?.let { d ->
            putJsonObject("device") { put("type", deviceType(d.type)); d.manufacturer?.let { put("manufacturer", it) }; d.model?.let { put("model", it) } }
        }
    }

    private fun JsonObjectBuilder.instant(t: Instant, off: ZoneOffset?) {
        put("time", t.toString()); off?.let { put("zoneOffset", it.id) }
    }

    private fun JsonObjectBuilder.interval(s: Instant, so: ZoneOffset?, e: Instant, eo: ZoneOffset?) {
        put("startTime", s.toString()); so?.let { put("startZoneOffset", it.id) }
        put("endTime", e.toString()); eo?.let { put("endZoneOffset", it.id) }
    }

    /** Returns null for types that are synced through aggregates or are not supported. */
    fun serialize(r: Record): JsonObject? = buildJsonObject {
        when (r) {
            is HeartRateRecord -> {
                common(r, "HeartRateRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset)
                putJsonArray("samples") { r.samples.forEach { s -> add(buildJsonObject { put("time", s.time.toString()); put("bpm", s.beatsPerMinute) }) } }
            }
            is RestingHeartRateRecord -> { common(r, "RestingHeartRateRecord"); instant(r.time, r.zoneOffset); put("bpm", r.beatsPerMinute) }
            is HeartRateVariabilityRmssdRecord -> { common(r, "HeartRateVariabilityRmssdRecord"); instant(r.time, r.zoneOffset); put("rmssdMs", r.heartRateVariabilityMillis) }
            is OxygenSaturationRecord -> { common(r, "OxygenSaturationRecord"); instant(r.time, r.zoneOffset); put("percent", r.percentage.value) }
            is RespiratoryRateRecord -> { common(r, "RespiratoryRateRecord"); instant(r.time, r.zoneOffset); put("rate", r.rate) }
            is BodyTemperatureRecord -> { common(r, "BodyTemperatureRecord"); instant(r.time, r.zoneOffset); put("celsius", r.temperature.inCelsius); put("location", r.measurementLocation) }
            is WeightRecord -> { common(r, "WeightRecord"); instant(r.time, r.zoneOffset); put("kg", r.weight.inKilograms) }
            is BodyFatRecord -> { common(r, "BodyFatRecord"); instant(r.time, r.zoneOffset); put("percent", r.percentage.value) }
            is LeanBodyMassRecord -> { common(r, "LeanBodyMassRecord"); instant(r.time, r.zoneOffset); put("kg", r.mass.inKilograms) }
            is BodyWaterMassRecord -> { common(r, "BodyWaterMassRecord"); instant(r.time, r.zoneOffset); put("kg", r.mass.inKilograms) }
            is BoneMassRecord -> { common(r, "BoneMassRecord"); instant(r.time, r.zoneOffset); put("kg", r.mass.inKilograms) }
            is BasalMetabolicRateRecord -> { common(r, "BasalMetabolicRateRecord"); instant(r.time, r.zoneOffset); put("kcalPerDay", r.basalMetabolicRate.inKilocaloriesPerDay) }
            is HeightRecord -> { common(r, "HeightRecord"); instant(r.time, r.zoneOffset); put("meters", r.height.inMeters) }
            is BloodGlucoseRecord -> {
                common(r, "BloodGlucoseRecord"); instant(r.time, r.zoneOffset); put("mmolPerL", r.level.inMillimolesPerLiter)
                put("specimenSource", r.specimenSource); put("mealType", r.mealType); put("relationToMeal", r.relationToMeal)
            }
            is Vo2MaxRecord -> { common(r, "Vo2MaxRecord"); instant(r.time, r.zoneOffset); put("vo2", r.vo2MillilitersPerMinuteKilogram); put("method", r.measurementMethod) }
            is BloodPressureRecord -> {
                common(r, "BloodPressureRecord"); instant(r.time, r.zoneOffset)
                put("systolicMmHg", r.systolic.inMillimetersOfMercury); put("diastolicMmHg", r.diastolic.inMillimetersOfMercury)
                put("bodyPosition", r.bodyPosition); put("measurementLocation", r.measurementLocation)
            }
            is SkinTemperatureRecord -> {
                common(r, "SkinTemperatureRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset)
                r.baseline?.let { put("baselineC", it.inCelsius) }
                put("location", r.measurementLocation)
                putJsonArray("deltas") { r.deltas.forEach { d -> add(buildJsonObject { put("time", d.time.toString()); put("deltaC", d.delta.inCelsius) }) } }
            }
            is SleepSessionRecord -> {
                common(r, "SleepSessionRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset)
                r.title?.let { put("title", it) }
                putJsonArray("stages") {
                    r.stages.forEach { s -> add(buildJsonObject { put("startTime", s.startTime.toString()); put("endTime", s.endTime.toString()); put("stage", stage(s.stage)) }) }
                }
            }
            is ExerciseSessionRecord -> {
                common(r, "ExerciseSessionRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset)
                put("exerciseType", exercise(r.exerciseType)); r.title?.let { put("title", it) }
            }
            is TotalCaloriesBurnedRecord -> { common(r, "TotalCaloriesBurnedRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset); put("kcal", r.energy.inKilocalories) }
            is FloorsClimbedRecord -> { common(r, "FloorsClimbedRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset); put("floors", r.floors) }
            is HydrationRecord -> { common(r, "HydrationRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset); put("liters", r.volume.inLiters) }
            is NutritionRecord -> {
                common(r, "NutritionRecord"); interval(r.startTime, r.startZoneOffset, r.endTime, r.endZoneOffset)
                r.energy?.let { put("kcal", it.inKilocalories) }; r.name?.let { put("name", it) }
            }
            else -> return null
        }
    }
}
