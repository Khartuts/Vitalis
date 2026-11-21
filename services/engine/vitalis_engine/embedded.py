"""In-process engine for the Android app (Chaquopy).

It speaks exactly the protocol of the desktop's stdio engine (`python -m vitalis_engine`): the first line is the
`init` message, then one JSON request per line; the engine answers with one JSON response or event per line. Here the
lines travel through two in-memory queues instead of stdin/stdout, so the phone and the PC run the same code path.

Kotlin usage (see apps/android/.../engine/EngineHost.kt):
    eng = module.callAttr("EmbeddedEngine"); eng.callAttr("start", initJson)
    eng.callAttr("send", requestJson); eng.callAttr("receive", 30.0)   # "" on timeout, None after shutdown
"""

from __future__ import annotations

import json
import queue
import threading


class _QueueReader:
    """File-like object for `serve()`: readline()/iteration block on a queue; None ends the stream."""

    def __init__(self, q: queue.Queue):
        self._q = q

    def readline(self) -> str:
        item = self._q.get()
        if item is None:
            return ""
        return item if item.endswith("\n") else item + "\n"

    def __iter__(self):
        return self

    def __next__(self) -> str:
        line = self.readline()
        if line == "":
            raise StopIteration
        return line


class _QueueWriter:
    """File-like object for `serve()`: splits written text into lines and queues each complete line."""

    def __init__(self, q: queue.Queue):
        self._q = q
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, s: str) -> int:
        with self._lock:
            self._buf += s
            while "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                if line:
                    self._q.put(line)
        return len(s)

    def flush(self) -> None:
        pass


class EmbeddedEngine:
    def __init__(self) -> None:
        self._in: queue.Queue = queue.Queue()
        self._out: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self.exit_code: int | None = None

    def start(self, init_json: str) -> None:
        """Start the engine thread. `init_json`: {"type":"init","db_path":…,"key":<64 hex>,"demo":…,"tz":…}."""
        if self._thread is not None:
            raise RuntimeError("engine already started")
        if json.loads(init_json).get("type") != "init":
            raise ValueError("the first message must be the init message")
        from .rpc.server import serve

        self._in.put(init_json)

        def run() -> None:
            try:
                self.exit_code = serve(_QueueReader(self._in), _QueueWriter(self._out))
            finally:
                self._out.put(None)  # tells receive() the engine has stopped

        self._thread = threading.Thread(target=run, name="vitalis-engine", daemon=True)
        self._thread.start()

    def send(self, line: str) -> None:
        self._in.put(line)

    def receive(self, timeout_s: float = 30.0) -> str | None:
        """Next protocol line (a response or an event); "" on timeout; None once the engine has stopped."""
        try:
            return self._out.get(timeout=timeout_s)
        except queue.Empty:
            return ""

    def stop(self, timeout_s: float = 30.0) -> int | None:
        if self._thread is None:
            return self.exit_code
        self._in.put(json.dumps({"type": "shutdown"}))
        self._in.put(None)
        self._thread.join(timeout_s)
        return self.exit_code
