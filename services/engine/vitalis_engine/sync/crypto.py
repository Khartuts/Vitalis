"""Cryptographic helpers for phone↔PC sync.

* TLS: self-signed ECDSA P-256 certificate; the phone pins its SHA-256 fingerprint (delivered in the QR code).
* Device auth: 256-bit bearer tokens; only SHA-256 hashes are stored.
* File fallback: AES-256-GCM with a 32-byte file key shared via the QR code.
  Format: b"VSYNC001" | nonce (12) | ciphertext+tag ; AAD = b"VSYNC001".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import NameOID

from ..db.connection import Database, utcnow

FILE_MAGIC = b"VSYNC001"
PAIRING_TTL_MIN = 10


def _secret(db: Database, name: str) -> bytes | None:
    r = db.query_one("SELECT value FROM sync_secrets WHERE name=?", (name,))
    return bytes(r["value"]) if r else None


def _put_secret(db: Database, name: str, value: bytes) -> None:
    db.execute("INSERT OR REPLACE INTO sync_secrets(name, value, created_at) VALUES (?,?,?)", (name, value, utcnow()))


def ensure_tls(db: Database, hosts: list[str]) -> tuple[bytes, bytes, str]:
    """Return (cert_pem, key_pem, sha256 fingerprint hex). Regenerated when hosts change."""
    cert_pem = _secret(db, "tls_cert_pem")
    key_pem = _secret(db, "tls_key_pem")
    if cert_pem and key_pem:
        cert = x509.load_pem_x509_certificate(cert_pem)
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        except x509.ExtensionNotFound:
            ips = set()
        if set(hosts) <= ips and cert.not_valid_after_utc > datetime.now(timezone.utc) + timedelta(days=30):
            return cert_pem, key_pem, fingerprint(cert_pem)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "VITALIS Desktop Sync")])
    now = datetime.now(timezone.utc)
    san = [x509.IPAddress(ipaddress.ip_address(h)) for h in hosts] + [x509.DNSName("vitalis.local")]
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    _put_secret(db, "tls_cert_pem", cert_pem)
    _put_secret(db, "tls_key_pem", key_pem)
    return cert_pem, key_pem, fingerprint(cert_pem)


def fingerprint(cert_pem: bytes) -> str:
    cert = x509.load_pem_x509_certificate(cert_pem)
    return hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def file_key(db: Database) -> bytes:
    k = _secret(db, "file_key")
    if k is None:
        k = os.urandom(32)
        _put_secret(db, "file_key", k)
    return k


def rotate_file_key(db: Database) -> bytes:
    k = os.urandom(32)
    _put_secret(db, "file_key", k)
    return k


def encrypt_file_payload(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(12)
    return FILE_MAGIC + nonce + AESGCM(key).encrypt(nonce, plaintext, FILE_MAGIC)


def decrypt_file_payload(key: bytes, blob: bytes) -> bytes:
    if blob[:8] != FILE_MAGIC or len(blob) < 8 + 12 + 16:
        raise ValueError("not a VITALIS sync file")
    return AESGCM(key).decrypt(blob[8:20], blob[20:], FILE_MAGIC)


def new_pairing_code(db: Database) -> tuple[str, str]:
    """8-digit numeric code (also embedded in the QR). Stored hashed with expiry."""
    code = f"{secrets.randbelow(10 ** 8):08d}"
    expires = (datetime.now(timezone.utc) + timedelta(minutes=PAIRING_TTL_MIN)).isoformat()
    db.execute("DELETE FROM sync_pairing_codes WHERE expires_at < ? OR used_at IS NOT NULL",
               (datetime.now(timezone.utc).isoformat(),))
    db.execute("INSERT INTO sync_pairing_codes(code_sha256, expires_at) VALUES (?,?)",
               (hashlib.sha256(code.encode()).hexdigest(), expires))
    return code, expires


def consume_pairing_code(db: Database, code: str) -> bool:
    h = hashlib.sha256(code.encode()).hexdigest()
    r = db.query_one("SELECT expires_at, used_at FROM sync_pairing_codes WHERE code_sha256=?", (h,))
    if not r or r["used_at"] or datetime.fromisoformat(r["expires_at"]) < datetime.now(timezone.utc):
        return False
    db.execute("UPDATE sync_pairing_codes SET used_at=? WHERE code_sha256=?", (utcnow(), h))
    return True


def new_device(db: Database, name: str) -> tuple[str, str]:
    device_id = "dev_" + secrets.token_hex(8)
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sync_devices(id, name, token_sha256, created_at) VALUES (?,?,?,?)",
               (device_id, name[:80], hashlib.sha256(token.encode()).hexdigest(), utcnow()))
    return device_id, token


def authenticate(db: Database, token: str | None) -> dict | None:
    if not token:
        return None
    h = hashlib.sha256(token.encode()).hexdigest()
    for r in db.query("SELECT id, name, token_sha256, revoked_at FROM sync_devices"):
        if hmac.compare_digest(r["token_sha256"], h) and not r["revoked_at"]:
            db.execute("UPDATE sync_devices SET last_seen=? WHERE id=?", (utcnow(), r["id"]))
            return r
    return None


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")
