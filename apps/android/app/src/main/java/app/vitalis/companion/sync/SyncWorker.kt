package app.vitalis.companion.sync

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import app.vitalis.companion.hc.HealthConnectSource
import java.util.concurrent.TimeUnit

/** Hourly background sync. Runs only when paired and the user granted Health Connect background reads. */
class SyncWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {
    override suspend fun doWork(): Result {
        val repo = SyncRepository(applicationContext)
        if (!repo.isPaired()) return Result.success()
        val granted = runCatching { repo.hc.granted() }.getOrDefault(emptySet())
        if (HealthConnectSource.BACKGROUND_PERMISSION !in granted) return Result.success()
        val r = repo.syncNow()
        return if (r.ok) Result.success() else Result.retry()
    }

    companion object {
        private const val NAME = "vitalis-hourly-sync"

        fun schedule(context: Context, enabled: Boolean) {
            val wm = WorkManager.getInstance(context)
            if (!enabled) { wm.cancelUniqueWork(NAME); return }
            val req = PeriodicWorkRequestBuilder<SyncWorker>(1, TimeUnit.HOURS)
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .build()
            wm.enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.UPDATE, req)
        }
    }
}
