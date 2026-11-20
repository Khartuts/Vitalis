"""Validation + import of companion payloads (schema vitalis.sync.v1), shared by LAN and file sync."""

from __future__ import annotations

import json
import re

from ..db.connection import Database
from ..ingest import health_connect, samsung_sdk
from ..ingest.pipeline import Importer

SCHEMA = "vitalis.sync.v1"
_UUID = re.compile(r"^[0-9a-fA-F-]{16,64}$")
MAX_RECORDS = 200_000


def validate(payload: dict) -> None:
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError(f"unsupported schema (expected {SCHEMA})")
    if not _UUID.match(str(payload.get("batch_id", ""))):
        raise ValueError("batch_id must be a UUID")
    if payload.get("source") not in ("health_connect", "samsung_sdk"):
        raise ValueError("source must be health_connect or samsung_sdk")
    recs = payload.get("records")
    if not isinstance(recs, list) or len(recs) > MAX_RECORDS:
        raise ValueError("records must be a list (max 200000)")
    for r in recs[:10]:
        if not isinstance(r, dict) or "type" not in r:
            raise ValueError("each record needs a type")


def import_sync_payload(db: Database, payload: dict, method: str, device: dict | None = None) -> dict:
    validate(payload)
    src = payload["source"]
    adapter = health_connect.normalize if src == "health_connect" else samsung_sdk.normalize
    detail = {"device": payload.get("device"), "device_id": (device or {}).get("id"), "seq": payload.get("seq"),
              "app_version": payload.get("app_version"), "cursor": payload.get("cursor")}
    s = Importer(db).run(provider=src, import_method=method, records=payload["records"], adapter=adapter,
                         batch_id=str(payload["batch_id"]), deletions=payload.get("deletions") or [], detail=detail)
    if payload.get("availability"):
        from ..db.connection import utcnow
        db.execute("INSERT INTO availability_reports(device_id, provider, received_at, report) VALUES (?,?,?,?)",
                   ((device or {}).get("id"), src, utcnow(), json.dumps(payload["availability"])[:2_000_000]))
    return s.as_dict()
