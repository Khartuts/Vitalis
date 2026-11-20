package app.vitalis.companion.sync

import java.security.SecureRandom
import javax.crypto.Cipher
import javax.crypto.spec.GCMParameterSpec
import javax.crypto.spec.SecretKeySpec

/**
 * Encrypted file transfer format shared with the desktop engine (vitalis_engine/sync/crypto.py):
 *   b"VSYNC001" | nonce (12 bytes) | AES-256-GCM ciphertext+tag (128-bit), AAD = b"VSYNC001".
 */
object FileCrypto {
    val MAGIC = "VSYNC001".toByteArray(Charsets.US_ASCII)
    private val rng = SecureRandom()

    fun encrypt(key: ByteArray, plaintext: ByteArray): ByteArray {
        require(key.size == 32) { "file key must be 32 bytes" }
        val nonce = ByteArray(12).also { rng.nextBytes(it) }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, nonce))
        c.updateAAD(MAGIC)
        return MAGIC + nonce + c.doFinal(plaintext)
    }

    fun decrypt(key: ByteArray, blob: ByteArray): ByteArray {
        require(blob.size > 8 + 12 + 16 && blob.copyOfRange(0, 8).contentEquals(MAGIC)) { "not a VITALIS sync file" }
        val c = Cipher.getInstance("AES/GCM/NoPadding")
        c.init(Cipher.DECRYPT_MODE, SecretKeySpec(key, "AES"), GCMParameterSpec(128, blob.copyOfRange(8, 20)))
        c.updateAAD(MAGIC)
        return c.doFinal(blob, 20, blob.size - 20)
    }
}
