// Supervises the Python analytics engine and speaks newline-delimited JSON-RPC over stdio.
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { EventEmitter } from "node:events";
import fs from "node:fs";
import path from "node:path";
import readline from "node:readline";

export interface EngineInit {
  dbPath: string;
  keyHex: string;
  demo: boolean;
  tz: string;
  profileName: string;
}

export interface EngineCommand {
  command: string;
  args: string[];
  cwd: string;
  env: NodeJS.ProcessEnv;
}

export class EngineError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

/** Resolve how to launch the engine: env override (tests), packaged runtime, or the dev virtualenv. */
export function resolveEngineCommand(opts: { isPackaged: boolean; resourcesPath: string; appPath: string }): EngineCommand {
  const baseEnv: NodeJS.ProcessEnv = {
    PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1",
    SYSTEMROOT: process.env.SYSTEMROOT, PATH: process.env.PATH, TEMP: process.env.TEMP, TMP: process.env.TMP,
    TMPDIR: process.env.TMPDIR, HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE,
    LOCALAPPDATA: process.env.LOCALAPPDATA,
  };
  if (process.env.VITALIS_PYTHON) {
    return { command: process.env.VITALIS_PYTHON, args: ["-m", "vitalis_engine"],
      cwd: process.env.VITALIS_ENGINE_DIR ?? process.cwd(), env: baseEnv };
  }
  if (opts.isPackaged) {
    // Bundled official CPython for Windows; packages and vitalis_engine live in its own site-packages, so the
    // engine runs in isolated mode (-I: ignores PYTHON* environment variables and the user site directory).
    const root = path.join(opts.resourcesPath, "engine");
    const py = process.platform === "win32" ? path.join(root, "python", "python.exe") : path.join(root, "python", "bin", "python3");
    return { command: py, args: ["-I", "-m", "vitalis_engine"], cwd: root, env: baseEnv };
  }
  const engineDir = path.resolve(opts.appPath, "..", "..", "services", "engine");
  const venvPy = process.platform === "win32" ? path.join(engineDir, ".venv", "Scripts", "python.exe")
    : path.join(engineDir, ".venv", "bin", "python");
  return { command: fs.existsSync(venvPy) ? venvPy : "python3", args: ["-m", "vitalis_engine"], cwd: engineDir, env: baseEnv };
}

type Pending = { resolve: (v: unknown) => void; reject: (e: Error) => void; timer: NodeJS.Timeout };

export class EngineBridge extends EventEmitter {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<number, Pending>();
  private nextId = 1;
  private stderrTail: string[] = [];
  ready = false;
  info: Record<string, unknown> | null = null;

  constructor(private cmd: EngineCommand) {
    super();
  }

  async start(init: EngineInit, timeoutMs = 60000): Promise<Record<string, unknown>> {
    if (this.proc) throw new EngineError("already_running", "engine already running");
    const proc = spawn(this.cmd.command, this.cmd.args, { cwd: this.cmd.cwd, env: this.cmd.env, stdio: "pipe",
      windowsHide: true });
    this.proc = proc;
    proc.stderr.setEncoding("utf8");
    proc.stderr.on("data", (d: string) => {
      this.stderrTail.push(d);
      if (this.stderrTail.length > 50) this.stderrTail.shift();
    });
    const rl = readline.createInterface({ input: proc.stdout });
    const ready = new Promise<Record<string, unknown>>((resolve, reject) => {
      const t = setTimeout(() => reject(new EngineError("start_timeout", "engine did not start in time")), timeoutMs);
      const onLine = (line: string) => {
        let msg: any;
        try { msg = JSON.parse(line); } catch { return; }
        if (msg.type === "ready") { clearTimeout(t); this.ready = true; this.info = msg; resolve(msg); return; }
        if (msg.type === "fatal") { clearTimeout(t); reject(new EngineError("fatal", String(msg.error))); return; }
        this.handle(msg);
      };
      rl.on("line", onLine);
      proc.once("error", (e) => { clearTimeout(t); reject(new EngineError("spawn_failed", e.message)); });
      proc.once("exit", (code) => {
        clearTimeout(t);
        if (!this.ready) reject(new EngineError("exited", `engine exited (${code}): ${this.stderrTail.join("").slice(-800)}`));
      });
    });
    proc.once("exit", (code, signal) => {
      this.ready = false;
      this.proc = null;
      for (const [, p] of this.pending) { clearTimeout(p.timer); p.reject(new EngineError("engine_exited", "engine stopped")); }
      this.pending.clear();
      this.emit("exit", { code, signal, stderr: this.stderrTail.join("").slice(-2000) });
    });
    // The key is delivered over stdin only (never argv/env), then the line is discarded by the engine.
    proc.stdin.write(JSON.stringify({ type: "init", db_path: init.dbPath, key: init.keyHex, demo: init.demo, tz: init.tz,
      profile_name: init.profileName }) + "\n");
    return ready;
  }

  private handle(msg: any) {
    if (msg.type === "event") { this.emit("event", msg.event); return; }
    if (typeof msg.id !== "number") return;
    const p = this.pending.get(msg.id);
    if (!p) return;
    this.pending.delete(msg.id);
    clearTimeout(p.timer);
    if (msg.error) p.reject(new EngineError(msg.error.code, msg.error.message));
    else p.resolve(msg.result);
  }

  call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs = 300000): Promise<T> {
    if (!this.proc || !this.ready) return Promise.reject(new EngineError("not_running", "engine is not running"));
    const id = this.nextId++;
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new EngineError("timeout", `${method} timed out`));
      }, timeoutMs);
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer });
      this.proc!.stdin.write(JSON.stringify({ id, method, params: params ?? {} }) + "\n");
    });
  }

  async stop(): Promise<void> {
    const proc = this.proc;
    if (!proc) return;
    await new Promise<void>((resolve) => {
      const t = setTimeout(() => { proc.kill(); resolve(); }, 8000);
      proc.once("exit", () => { clearTimeout(t); resolve(); });
      try { proc.stdin.write(JSON.stringify({ type: "shutdown" }) + "\n"); proc.stdin.end(); } catch { proc.kill(); }
    });
  }
}
