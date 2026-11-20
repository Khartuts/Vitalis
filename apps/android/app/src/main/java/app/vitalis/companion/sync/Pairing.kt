package app.vitalis.companion.sync

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.security.MessageDigest
import java.util.Base64

/** Contents of the desktop's pairing QR: `vitalis:{"v":1,"app":"vitalis","name":..,"hosts":[..],"port":..,"fp":..,"code":..,"fk":..}` */
@Serializable
data class PairingInfo(
    val v: Int,
    val app: String,
    val name: String,
    val hosts: List<String>,
    val port: Int,
    val fp: String,
    val code: String,
    val fk: String,
) {
    fun fileKey(): ByteArray = Base64.getUrlDecoder().decode(fk.padEnd((fk.length + 3) / 4 * 4, '='))
}

object Pairing {
    private val json = Json { ignoreUnknownKeys = true }

    /** Parses and validates a scanned/pasted pairing string. Throws IllegalArgumentException with a user-facing message. */
    fun parse(text: String): PairingInfo {
        val t = text.trim()
        require(t.startsWith("vitalis:")) { "This is not a VITALIS pairing code." }
        val info = try {
            json.decodeFromString(PairingInfo.serializer(), t.removePrefix("vitalis:"))
        } catch (e: Exception) {
            throw IllegalArgumentException("The pairing code could not be read.")
        }
        require(info.v == 1 && info.app == "vitalis") { "Unsupported pairing code version." }
        require(info.fp.matches(Regex("^[0-9a-f]{64}$"))) { "Invalid certificate fingerprint." }
        require(info.code.matches(Regex("^\\d{8}$"))) { "Invalid one-time code." }
        require(info.port in 1024..65535) { "Invalid port." }
        require(info.hosts.isNotEmpty() && info.hosts.all { it.matches(Regex("^[0-9.]{7,15}$")) }) { "Invalid desktop address." }
        require(info.fileKey().size == 32) { "Invalid file key." }
        return info
    }

    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}
