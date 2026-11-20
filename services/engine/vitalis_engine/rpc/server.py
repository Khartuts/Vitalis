"""Newline-delimited JSON-RPC over stdin/stdout. stdout carries ONLY protocol messages; logs go to stderr.

Protocol
  -> {"type":"init","db_path":...,"key":"<64 hex>","demo":false,"tz":"Europe/Zagreb","profile_name":"..."}
  <- {"type":"ready", ...}
  -> {"id": 1, "method": "features.series", "params": {...}}
  <- {"id": 1, "result": ...}            |  {"id": 1, "error": {"code": "...", "message": "..."}}
  <- {"type": "event", "event": {...}}    (progress, data_changed, sync events)
"""

from __future__ import annotations

import json
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import BaseModel, ValidationError

from .. import __version__
from ..db.connection import Database, DatabaseError


class RpcError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


REGISTRY: dict[str, tuple[Any, type[BaseModel] | None]] = {}


def method(name: str, params: type[BaseModel] | None = None):
    def deco(fn):
        REGISTRY[name] = (fn, params)
        return fn
    return deco


class Engine:
    def __init__(self, db: Database, out, demo: bool):
        self.db = db
        self._out = out
        self._out_lock = threading.Lock()
        self.demo = demo
        self.sync = None
        self._recompute_lock = threading.Lock()

    def send(self, msg: dict) -> None:
        line = json.dumps(msg, separators=(",", ":"), default=_default)
        with self._out_lock:
            self._out.write(line + "\n")
            self._out.flush()

    def event(self, ev: dict) -> None:
        self.send({"type": "event", "event": ev})


def _default(o):
    import datetime as _dt
    import numpy as np
    if isinstance(o, (_dt.date, _dt.datetime)):
        return o.isoformat()
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, bytes):
        return None
    return str(o)


def _sanitize(o):
    """Replace NaN/inf (invalid JSON) with null."""
    import math
    if isinstance(o, float):
        return None if (math.isnan(o) or math.isinf(o)) else o
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize(v) for v in o]
    return o


def dispatch(engine: Engine, req: dict) -> dict:
    rid = req.get("id")
    name = req.get("method")
    if name not in REGISTRY:
        return {"id": rid, "error": {"code": "method_not_found", "message": f"unknown method {name}"}}
    fn, model = REGISTRY[name]
    try:
        params = req.get("params") or {}
        if not isinstance(params, dict):
            raise RpcError("invalid_params", "params must be an object")
        if model is not None:
            parsed = model.model_validate(params)
            result = fn(engine, parsed)
        else:
            if params:
                raise RpcError("invalid_params", "this method takes no parameters")
            result = fn(engine)
        return {"id": rid, "result": _sanitize(json.loads(json.dumps(result, default=_default)))}
    except ValidationError as exc:
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return {"id": rid, "error": {"code": "invalid_params", "message": msg[:1000]}}
    except RpcError as exc:
        return {"id": rid, "error": {"code": exc.code, "message": str(exc)}}
    except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
        return {"id": rid, "error": {"code": type(exc).__name__, "message": str(exc)[:1000]}}
    except DatabaseError as exc:
        return {"id": rid, "error": {"code": "database_error", "message": str(exc)}}
    except Exception as exc:  # pragma: no cover
        traceback.print_exc(file=sys.stderr)
        return {"id": rid, "error": {"code": "internal_error", "message": f"{type(exc).__name__}: {exc}"[:500]}}


def serve(stdin=None, stdout=None) -> int:
    from . import methods  # noqa: F401  (registers methods)
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    first = stdin.readline()
    if not first:
        return 1
    try:
        init = json.loads(first)
        assert init.get("type") == "init"
        db = Database(init["db_path"], init["key"])
    except Exception as exc:
        stdout.write(json.dumps({"type": "fatal", "error": f"init failed: {exc}"}) + "\n")
        stdout.flush()
        return 2
    if init.get("demo"):
        db.set_pref("is_demo", True)
    if db.get_pref("display_timezone") is None:
        db.set_pref("display_timezone", init.get("tz") or "Europe/Zagreb")
    if init.get("profile_name") and not db.get_pref("profile_name"):
        db.set_pref("profile_name", init["profile_name"])
    from ..journal import ensure_builtins
    ensure_builtins(db)
    engine = Engine(db, stdout, bool(db.get_pref("is_demo", False)))
    from ..sync.server import SyncService
    engine.sync = SyncService(db, lambda ev: methods.on_sync_event(engine, ev))
    engine.send({"type": "ready", "version": __version__, "schema_version": db.schema_version(),
                 "demo": engine.demo})
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rpc")
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            engine.send({"id": None, "error": {"code": "parse_error", "message": "invalid JSON"}})
            continue
        if req.get("type") == "shutdown":
            break
        pool.submit(lambda r=req: engine.send(dispatch(engine, r)))
    pool.shutdown(wait=True)
    if engine.sync:
        engine.sync.stop()
    db.close()
    return 0
