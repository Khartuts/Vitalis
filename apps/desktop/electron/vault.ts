// OS-protected secret storage (Electron safeStorage → Windows DPAPI / macOS Keychain / libsecret).
import { randomBytes, randomUUID } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export interface Cipher {
  available(): boolean;
  encrypt(plain: string): Buffer;
  decrypt(buf: Buffer): string;
  backend(): string;
}

/** Electron safeStorage adapter. On Linux a weak "basic_text" backend is rejected unless explicitly allowed for tests. */
export function electronCipher(safeStorage: Electron.SafeStorage): Cipher {
  return {
    available() {
      if (!safeStorage.isEncryptionAvailable()) return false;
      if (process.platform === "linux") {
        const b = safeStorage.getSelectedStorageBackend?.() ?? "unknown";
        if (b === "basic_text" && process.env.VITALIS_ALLOW_INSECURE_KEYSTORE !== "1") return false;
      }
      return true;
    },
    encrypt: (p) => safeStorage.encryptString(p),
    decrypt: (b) => safeStorage.decryptString(b),
    backend: () => (process.platform === "linux" ? safeStorage.getSelectedStorageBackend?.() ?? "linux" : process.platform),
  };
}

/** Test-only cipher: explicit opt-in, marks data as not OS-protected. Never used in packaged builds. */
export function insecureTestCipher(): Cipher {
  return {
    available: () => true,
    encrypt: (p) => Buffer.concat([Buffer.from("INSECURE-TEST:"), Buffer.from(p, "utf8")]),
    decrypt: (b) => {
      const s = b.toString("utf8");
      if (!s.startsWith("INSECURE-TEST:")) throw new Error("not a test-cipher blob");
      return s.slice("INSECURE-TEST:".length);
    },
    backend: () => "insecure-test",
  };
}

function atomicWrite(file: string, data: Buffer | string) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const tmp = `${file}.${randomBytes(4).toString("hex")}.tmp`;
  fs.writeFileSync(tmp, data, { mode: 0o600 });
  fs.renameSync(tmp, file);
}

export class Vault {
  constructor(private dir: string, private cipher: Cipher) {}

  private file(name: string) {
    if (!/^[a-z0-9_.-]{1,64}$/i.test(name)) throw new Error("invalid secret name");
    return path.join(this.dir, `${name}.bin`);
  }

  ensureAvailable() {
    if (!this.cipher.available()) {
      throw new Error("OS-protected storage is not available. VITALIS will not store health-data keys unprotected.");
    }
  }

  set(name: string, value: unknown) {
    this.ensureAvailable();
    atomicWrite(this.file(name), this.cipher.encrypt(JSON.stringify(value)));
  }

  get<T = unknown>(name: string): T | null {
    const f = this.file(name);
    if (!fs.existsSync(f)) return null;
    return JSON.parse(this.cipher.decrypt(fs.readFileSync(f))) as T;
  }

  delete(name: string) {
    const f = this.file(name);
    if (fs.existsSync(f)) {
      fs.writeFileSync(f, randomBytes(64));
      fs.rmSync(f);
    }
  }

  backend() {
    return this.cipher.backend();
  }
}

export interface Profile {
  id: string;
  name: string;
  createdAt: string;
  demo: boolean;
  timezone: string;
  dbFile: string;
}

/** Profiles: one encrypted SQLCipher database per profile; its 256-bit key is wrapped by the OS keystore. */
export class ProfileStore {
  private listFile: string;

  constructor(private root: string, private vault: Vault) {
    this.listFile = path.join(root, "profiles.json");
  }

  list(): Profile[] {
    if (!fs.existsSync(this.listFile)) return [];
    return JSON.parse(fs.readFileSync(this.listFile, "utf8")) as Profile[];
  }

  private save(ps: Profile[]) {
    atomicWrite(this.listFile, JSON.stringify(ps, null, 2));
  }

  create(name: string, demo: boolean, timezone: string): Profile {
    this.vault.ensureAvailable();
    const id = randomUUID();
    const p: Profile = { id, name, demo, timezone, createdAt: new Date().toISOString(),
      dbFile: path.join(this.root, "profiles", `${id}.vitalis.db`) };
    fs.mkdirSync(path.dirname(p.dbFile), { recursive: true });
    this.vault.set(`dbkey-${id}`, { key: randomBytes(32).toString("hex") });
    this.save([...this.list(), p]);
    return p;
  }

  key(id: string): string {
    const k = this.vault.get<{ key: string }>(`dbkey-${id}`);
    if (!k) throw new Error("profile key missing");
    return k.key;
  }

  /** Register an externally produced DB (e.g. restored backup) with a fresh wrapped key. */
  adopt(name: string, demo: boolean, timezone: string, keyHex: string, dbFile: string): Profile {
    const id = randomUUID();
    const p: Profile = { id, name, demo, timezone, createdAt: new Date().toISOString(), dbFile };
    this.vault.set(`dbkey-${id}`, { key: keyHex });
    this.save([...this.list(), p]);
    return p;
  }

  newDbPath(): { id: string; dbFile: string; key: string } {
    const id = randomUUID();
    return { id, dbFile: path.join(this.root, "profiles", `${id}.vitalis.db`), key: randomBytes(32).toString("hex") };
  }

  rename(id: string, name: string) {
    this.save(this.list().map((p) => (p.id === id ? { ...p, name } : p)));
  }

  /** One-click deletion: database (+WAL/SHM) overwritten with random bytes, removed, and the key destroyed. */
  delete(id: string) {
    const p = this.list().find((x) => x.id === id);
    if (!p) return;
    for (const suffix of ["", "-wal", "-shm"]) {
      const f = p.dbFile + suffix;
      if (fs.existsSync(f)) {
        const size = fs.statSync(f).size;
        try { fs.writeFileSync(f, randomBytes(Math.min(size, 64 * 1024 * 1024))); } catch { /* best effort */ }
        fs.rmSync(f, { force: true });
      }
    }
    this.vault.delete(`dbkey-${id}`);
    this.save(this.list().filter((x) => x.id !== id));
  }
}
