package app.vitalis.companion

import app.vitalis.companion.sync.FileCrypto
import app.vitalis.companion.sync.Pairing
import app.vitalis.companion.sync.SyncClient
import app.vitalis.companion.sync.SyncCounts
import app.vitalis.companion.sync.SyncRepository
import app.vitalis.companion.sync.summary
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import org.junit.Assert.*
import org.junit.Test
import java.io.ByteArrayInputStream
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.time.Instant

class CompanionTest {
    private fun hex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    // Produced by vitalis_engine/sync/crypto.py (desktop) — proves both sides use the same file format.
    private val keyHex = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
    private val blobHex = "5653594e433030314c86f2ea0ca9b9d2fe0425a0234a9a3a6c992a651b62107d1848016c45169c6890273c88d489c157ea870a53c9783ee4ce22b99a12c0543ff71d5a4f7ec8b2e851a94812c8b4993569"
    private val plaintext = "{\"schema\":\"vitalis.sync.v1\",\"hello\":\"zagreb\"}"
    private val certPem = "-----BEGIN CERTIFICATE-----\nMIIBbzCCARagAwIBAgIUdnon7GZYOmKccKqU0MU+J9MpU+QwCgYIKoZIzj0EAwIw\nHzEdMBsGA1UEAwwUVklUQUxJUyBEZXNrdG9wIFN5bmMwHhcNMjYxMTIwMTQ1ODEw\nWhcNMjkwMjIyMTUwMzEwWjAfMR0wGwYDVQQDDBRWSVRBTElTIERlc2t0b3AgU3lu\nYzBZMBMGByqGSM49AgEGCCqGSM49AwEHA0IABM58HWpwXyK0cS1/tDcrIrWJK2DK\nc+Xw1kykPvBAPhBDbvBtXKMu5HXCVSeuetyOZTBttZSsZmBS6+TixS+KzzCjMDAu\nMB4GA1UdEQQXMBWHBH8AAAGCDXZpdGFsaXMubG9jYWwwDAYDVR0TAQH/BAIwADAK\nBggqhkjOPQQDAgNHADBEAiATERPv1pT4gg973ZhtdnjPSyI0DliJZMZA9G6Kvggp\n1wIgYP6W4J9vngKEaaWG9uhsZ1q7jI+83CeAY9wTTmRPxaA=\n-----END CERTIFICATE-----"
    private val certFp = "dcd70f66834e2e060ea9d74ac8d8d01067c66771b85601b5ad535a808b9ced00"

    @Test fun decryptsDesktopProducedFile() {
        assertEquals(plaintext, String(FileCrypto.decrypt(hex(keyHex), hex(blobHex))))
    }

    @Test fun encryptRoundTripAndTamperDetection() {
        val key = ByteArray(32) { it.toByte() }
        val blob = FileCrypto.encrypt(key, "health".toByteArray())
        assertArrayEquals("VSYNC001".toByteArray(), blob.copyOfRange(0, 8))
        assertEquals("health", String(FileCrypto.decrypt(key, blob)))
        blob[blob.size - 1] = (blob[blob.size - 1].toInt() xor 1).toByte()
        assertThrows(Exception::class.java) { FileCrypto.decrypt(key, blob) }
        assertThrows(Exception::class.java) { FileCrypto.decrypt(ByteArray(32), FileCrypto.encrypt(key, "x".toByteArray())) }
    }

    private fun pairingText(fp: String = "a".repeat(64), code: String = "12345678", hosts: String = "[\"192.168.1.20\"]") =
        "vitalis:{\"v\":1,\"app\":\"vitalis\",\"name\":\"PC\",\"hosts\":$hosts,\"port\":47821,\"fp\":\"$fp\",\"code\":\"$code\",\"fk\":\"AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8\"}"

    @Test fun parsesValidPairingAndRejectsInvalid() {
        val p = Pairing.parse(pairingText())
        assertEquals(47821, p.port)
        assertEquals(32, p.fileKey().size)
        assertEquals(0, p.fileKey()[0].toInt()); assertEquals(31, p.fileKey()[31].toInt())
        assertThrows(IllegalArgumentException::class.java) { Pairing.parse("https://example.com") }
        assertThrows(IllegalArgumentException::class.java) { Pairing.parse(pairingText(fp = "zz")) }
        assertThrows(IllegalArgumentException::class.java) { Pairing.parse(pairingText(code = "12ab")) }
        assertThrows(IllegalArgumentException::class.java) { Pairing.parse(pairingText(hosts = "[\"evil.example.com\"]")) }
    }

    private fun cert(): X509Certificate =
        CertificateFactory.getInstance("X.509").generateCertificate(ByteArrayInputStream(certPem.replace("\\n", "\n").toByteArray())) as X509Certificate

    @Test fun pinsExactlyTheDesktopCertificate() {
        val c = cert()
        assertEquals(certFp, Pairing.sha256Hex(c.encoded))
        SyncClient.PinningTrustManager(certFp).checkServerTrusted(arrayOf(c), "ECDHE_ECDSA")
        assertThrows(CertificateException::class.java) { SyncClient.PinningTrustManager("b".repeat(64)).checkServerTrusted(arrayOf(c), "ECDHE_ECDSA") }
        assertThrows(CertificateException::class.java) { SyncClient.PinningTrustManager(certFp).checkServerTrusted(arrayOf(), "ECDHE_ECDSA") }
    }

    @Test fun batchingAndWindows() {
        val items = List(9001) { buildJsonObject { put("i", JsonPrimitive(it)) } }
        val parts = SyncRepository.chunk(items)
        assertEquals(listOf(4000, 4000, 1001), parts.map { it.size })
        assertTrue(SyncRepository.chunk(emptyList()).isEmpty())
        val s = Instant.parse("2026-01-01T00:00:00Z")
        val w = SyncRepository.windows(s, s.plusSeconds(95L * 86400))
        assertEquals(4, w.size)
        assertEquals(w.last().second, s.plusSeconds(95L * 86400))
        w.zipWithNext().forEach { (a, b) -> assertEquals(a.second, b.first) }
    }

    @Test fun syncSummaryNamesWhatWasSent() {
        assertEquals("no new records", SyncCounts().summary())
        val c = SyncCounts(); c["Sleep (sessions and stages)"] = 31; c["Heart rate"] = 0; c["Activity totals"] = 2208
        assertEquals("Sleep (sessions and stages) 31, Activity totals 2208", c.summary())
    }
}
