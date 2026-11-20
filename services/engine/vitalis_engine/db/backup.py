"""Encrypted backup / restore.

A backup is a SQLCipher database re-keyed with a key derived from a user passphrase
(scrypt, random salt). File layout: MAGIC(8) | salt(16) | scrypt params(12) | sqlcipher-db bytes.
"""

from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlcipher3 import dbapi2 as sqlcipher

from .connection import Database, DatabaseError, _apply_key

MAGIC = b"VITBAK01"
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1


def _derive(passphrase: str, salt: bytes, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    if len(passphrase) < 10:
        raise ValueError("backup passphrase must be at least 10 characters")
    kdf = Scrypt(salt=salt, length=32, n=n, r=r, p=p)
    return kdf.derive(passphrase.encode("utf-8")).hex()


def create_backup(db: Database, dest: str | Path, passphrase: str) -> dict:
    salt = os.urandom(16)
    bkey = _derive(passphrase, salt)
    dest = Path(dest)
    with tempfile.TemporaryDirectory() as td:
        tmp_db = Path(td) / "export.db"
        conn = db.connect()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute(f"ATTACH DATABASE ? AS backup KEY \"x'{bkey}'\"", (str(tmp_db),))
        try:
            conn.execute("SELECT sqlcipher_export('backup')")
        finally:
            conn.execute("DETACH DATABASE backup")
        data = tmp_db.read_bytes()
    tmp_out = dest.with_suffix(dest.suffix + ".tmp")
    with open(tmp_out, "wb") as f:
        f.write(MAGIC + salt + struct.pack("<III", SCRYPT_N, SCRYPT_R, SCRYPT_P) + data)
    os.replace(tmp_out, dest)
    return {"path": str(dest), "bytes": dest.stat().st_size, "schema_version": db.schema_version()}


def restore_backup(src: str | Path, passphrase: str, target_path: str | Path, target_key_hex: str) -> dict:
    """Decrypt a backup and write it as a new database encrypted with target_key_hex."""
    raw = Path(src).read_bytes()
    if raw[:8] != MAGIC:
        raise DatabaseError("not a VITALIS backup file")
    salt = raw[8:24]
    n, r, p = struct.unpack("<III", raw[24:36])
    bkey = _derive(passphrase, salt, n, r, p)
    with tempfile.TemporaryDirectory() as td:
        src_db = Path(td) / "backup.db"
        src_db.write_bytes(raw[36:])
        conn = sqlcipher.connect(str(src_db))
        _apply_key(conn, bkey)
        try:
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher.DatabaseError as exc:
            conn.close()
            raise DatabaseError("wrong passphrase or corrupted backup") from exc
        out = Path(td) / "restored.db"
        conn.execute(f"ATTACH DATABASE ? AS restored KEY \"x'{target_key_hex}'\"", (str(out),))
        conn.execute("SELECT sqlcipher_export('restored')")
        conn.execute("DETACH DATABASE restored")
        conn.close()
        target = Path(target_path)
        for suffix in ("", "-wal", "-shm"):
            p_ = Path(str(target) + suffix)
            if suffix and p_.exists():
                p_.unlink()
        shutil.copyfile(out, target)
    restored = Database(target, target_key_hex)
    info = {"path": str(target), "schema_version": restored.schema_version()}
    restored.close()
    return info
