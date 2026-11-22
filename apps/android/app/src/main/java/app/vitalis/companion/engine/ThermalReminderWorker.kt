package app.vitalis.companion.engine

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.vitalis.companion.BuildConfig
import app.vitalis.companion.R
import app.vitalis.companion.web.WebAppActivity
import kotlinx.serialization.json.*
import java.time.Duration
import java.time.LocalDate
import java.time.ZonedDateTime
import java.util.concurrent.TimeUnit

/** Evening notification with tonight's suggested bedroom temperature (opt-in; suggestions only, no device control). */
class ThermalReminderWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        if (!BuildConfig.HAS_ENGINE) return Result.success()
        return try {
            val s = PhoneEngine.get(applicationContext).call("thermal.suggestion", buildJsonObject { put("date", LocalDate.now().toString()) }).jsonObject
            val c = s["suggested_c"]!!.jsonPrimitive.double
            val personal = s["basis"]?.jsonPrimitive?.contentOrNull == "personal"
            notify(applicationContext, "Tonight's bedroom: %.1f °C".format(c),
                if (personal) "Based on your own nights. Tap for the reasons." else "General guidance — log your bedroom temperature to personalise it.")
            Result.success()
        } catch (e: Exception) {
            Result.retry()
        }
    }

    companion object {
        private const val NAME = "vitalis-thermal-reminder"
        private const val CHANNEL = "evening_temperature"

        fun schedule(context: Context, enabled: Boolean, time: String) {
            val wm = WorkManager.getInstance(context)
            if (!enabled) { wm.cancelUniqueWork(NAME); return }
            val (h, m) = time.split(":").map { it.toInt() }
            val now = ZonedDateTime.now()
            var next = now.withHour(h).withMinute(m).withSecond(0).withNano(0)
            if (!next.isAfter(now)) next = next.plusDays(1)
            val req = PeriodicWorkRequestBuilder<ThermalReminderWorker>(24, TimeUnit.HOURS)
                .setInitialDelay(Duration.between(now, next).toMillis(), TimeUnit.MILLISECONDS).build()
            wm.enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.UPDATE, req)
        }

        fun notify(context: Context, title: String, text: String) {
            if (Build.VERSION.SDK_INT >= 33 &&
                ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL, "Evening bedroom temperature", NotificationManager.IMPORTANCE_DEFAULT))
            val open = PendingIntent.getActivity(context, 0,
                Intent(context, WebAppActivity::class.java).putExtra(WebAppActivity.EXTRA_ROUTE, "temperature")
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
            val n = NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_launcher)
                .setContentTitle(title).setContentText(text).setStyle(NotificationCompat.BigTextStyle().bigText(text))
                .setContentIntent(open).setAutoCancel(true).build()
            NotificationManagerCompat.from(context).notify(4711, n)
        }
    }
}
