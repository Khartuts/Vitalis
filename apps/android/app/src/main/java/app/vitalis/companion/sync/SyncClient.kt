package app.vitalis.companion.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager

class SyncHttpException(val status: Int, message: String) : IOException(message)

/**
 * HTTPS client for the paired desktop. The desktop uses a self-signed certificate; instead of trusting any CA,
 * the client accepts exactly one certificate: the one whose SHA-256 (DER) matches the fingerprint from the QR.
 */
class SyncClient(private val hosts: List<String>, private val port: Int, private val fingerprint: String) {
    private val json = Json { ignoreUnknownKeys = true }

    internal class PinningTrustManager(private val expected: String) : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) =
            throw CertificateException("client certificates not supported")

        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
            val leaf = chain?.firstOrNull() ?: throw CertificateException("no server certificate")
            val got = Pairing.sha256Hex(leaf.encoded)
            if (got != expected) throw CertificateException("certificate fingerprint mismatch")
            leaf.checkValidity()
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private val client: OkHttpClient by lazy {
        val tm = PinningTrustManager(fingerprint)
        val ctx = SSLContext.getInstance("TLS").apply { init(null, arrayOf(tm), SecureRandom()) }
        OkHttpClient.Builder()
            .sslSocketFactory(ctx.socketFactory, tm)
            .hostnameVerifier { _, _ -> true } // identity is established by the pinned fingerprint above
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(120, TimeUnit.SECONDS)
            .build()
    }

    private var workingHost: String? = null

    private fun call(path: String, body: String?, token: String?): JsonObject {
        val candidates = listOfNotNull(workingHost) + hosts.filter { it != workingHost }
        var last: Exception? = null
        for (h in candidates) {
            val rb = Request.Builder().url("https://$h:$port$path")
            if (token != null) rb.header("Authorization", "Bearer $token")
            if (body != null) rb.post(body.toRequestBody("application/json".toMediaType())) else rb.get()
            try {
                client.newCall(rb.build()).execute().use { res ->
                    val text = res.body.string()
                    if (!res.isSuccessful) {
                        val detail = runCatching { json.parseToJsonElement(text).jsonObject["detail"]?.jsonPrimitive?.content }.getOrNull()
                        throw SyncHttpException(res.code, detail ?: "HTTP ${res.code}")
                    }
                    workingHost = h
                    return json.parseToJsonElement(text.ifEmpty { "{}" }).jsonObject
                }
            } catch (e: SyncHttpException) {
                throw e
            } catch (e: Exception) {
                last = e
            }
        }
        throw IOException("Desktop not reachable on ${hosts.joinToString()}:$port (${last?.message})", last)
    }

    fun hello(): JsonObject = call("/v1/hello", null, null)

    fun pair(code: String, deviceName: String): Pair<String, String> {
        val r = call("/v1/pair", buildJsonObject { put("code", code); put("device_name", deviceName) }.toString(), null)
        return r["device_id"]!!.jsonPrimitive.content to r["token"]!!.jsonPrimitive.content
    }

    fun sendBatch(payload: String, token: String): JsonObject = call("/v1/batches", payload, token)

    fun sendAvailability(payload: String, token: String): JsonObject = call("/v1/availability", payload, token)

    /** Two-way journal/experiment exchange (vitalis.journal.v1). */
    fun exchangeJournal(body: String, token: String): JsonObject = call("/v1/journal/exchange", body, token)
}
