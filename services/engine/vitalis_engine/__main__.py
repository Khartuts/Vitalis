"""Entry point: `python -m vitalis_engine` starts the stdio JSON-RPC engine."""

import io
import os
import sys


def main() -> int:
    # stdout is reserved for protocol messages; make it line-buffered UTF-8 and keep prints off it.
    proto_out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True, newline="\n")
    proto_in = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    sys.stdout = sys.stderr
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    from vitalis_engine.rpc.server import serve
    return serve(proto_in, proto_out)


if __name__ == "__main__":
    raise SystemExit(main())
