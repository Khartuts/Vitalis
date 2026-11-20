package app.vitalis.companion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Privacy policy shown by Health Connect when the user reviews VITALIS's permissions (required by Health Connect). */
class PermissionsRationaleActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                Surface(Modifier.fillMaxSize(), color = Color(0xFF0E1116)) {
                    Column(Modifier.verticalScroll(rememberScrollState()).padding(20.dp)) {
                        Text("VITALIS Companion — privacy policy", color = Color.White, fontSize = 20.sp)
                        Text(POLICY, color = Color(0xFFA9B1BD), fontSize = 14.sp, modifier = Modifier.padding(top = 12.dp))
                        Button(onClick = { finish() }, modifier = Modifier.padding(top = 16.dp)) { Text("Close") }
                    }
                }
            }
        }
    }

    companion object {
        const val POLICY = """
• What is read: only the Health Connect data types you approve (read-only). Optionally, data from Samsung Health via the Samsung Health Data SDK if you connect it.

• Why: to show your own trends, sleep, recovery and habit analyses in the VITALIS desktop app on your computer.

• Where it goes: only to the Windows PC you paired by scanning its QR code, over an encrypted connection (TLS with a pinned certificate) on your local network, or in an AES-256-GCM encrypted file that you move yourself. Nothing is sent to VITALIS servers — there are none — and there are no analytics, ads or trackers.

• On this phone: the pairing token and keys are encrypted with the Android Keystore and excluded from backups. Health data is not stored by the companion beyond what is needed to send a batch.

• Writing: VITALIS never writes to or deletes data in Health Connect or Samsung Health.

• Control: revoke any permission in Health Connect settings at any time; unpair in the companion; delete data in the desktop app (Settings → Privacy & data).

• Not medical advice: VITALIS provides wellness information, not diagnoses.
"""
    }
}
