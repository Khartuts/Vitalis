"""Starts an engine interpreter (e.g. the bundled Windows python.exe) and exercises the stdio JSON-RPC protocol:
init -> system.status -> demo.load (synthetic) -> overview.get -> shutdown. Exit code 0 on success."""
import json, os, subprocess, sys, tempfile

py = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else sys.executable  # absolute, like the desktop app
db = os.path.join(tempfile.mkdtemp(), "smoke.db")
print("interpreter:", py)
p = subprocess.Popen([py, "-I", "-m", "vitalis_engine"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                     cwd=os.path.dirname(os.path.dirname(py)))

def send(o):
    p.stdin.write(json.dumps(o) + "\n"); p.stdin.flush()

def wait(rid):
    while True:
        m = json.loads(p.stdout.readline())
        if m.get("id") == rid:
            if "error" in m:
                raise SystemExit(f"error: {m['error']}")
            return m["result"]

send({"type": "init", "db_path": db, "key": os.urandom(32).hex(), "demo": True})
ready = json.loads(p.stdout.readline()); assert ready["type"] == "ready", ready
send({"id": 1, "method": "system.status"}); print("status", wait(1)["version"])
send({"id": 2, "method": "demo.load", "params": {"days": 45}}); r = wait(2); print("demo", r["import"]["inserted"], "records")
send({"id": 3, "method": "overview.get", "params": {"date": r["range"][1]}}); o = wait(3)
print("overview recovery:", (o.get("recovery") or {}).get("category"), "| sleep:", o["sleep_today"].get("sleep_tst_min"))
p.stdin.close(); assert p.wait(timeout=60) == 0
print("SMOKE OK")
