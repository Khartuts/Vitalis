// Loopback (127.0.0.1) redirect listener for OAuth in the system browser (RFC 8252). Never uses an embedded WebView.
import { createHash, randomBytes } from "node:crypto";
import http from "node:http";
import type { AddressInfo } from "node:net";

export function pkce() {
  const verifier = randomBytes(48).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  return { verifier, challenge };
}

export const randomToken = (n = 24) => randomBytes(n).toString("base64url");

export interface LoopbackResult {
  params: URLSearchParams;
}

const PAGE = (title: string, body: string) =>
  `<!doctype html><meta charset="utf-8"><title>${title}</title><body style="font:16px system-ui;background:#111418;color:#e6e9ee;display:grid;place-items:center;height:100vh"><div><h2>${title}</h2><p>${body}</p></div></body>`;

/** Start a one-shot listener. Returns the redirect URI and a promise for the callback parameters. */
export async function startLoopback(opts: { port: number; path: string; expectedState: string; timeoutMs?: number;
  fallbackToEphemeral?: boolean }): Promise<{ redirectUri: string; result: Promise<LoopbackResult>; close: () => void }> {
  let settle!: { resolve: (r: LoopbackResult) => void; reject: (e: Error) => void };
  const result = new Promise<LoopbackResult>((resolve, reject) => (settle = { resolve, reject }));
  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://127.0.0.1");
    if (req.method !== "GET" || url.pathname !== opts.path) {
      res.writeHead(404).end();
      return;
    }
    const params = url.searchParams;
    if (params.get("state") !== opts.expectedState) {
      res.writeHead(400, { "content-type": "text/html" }).end(PAGE("Sign-in failed", "State mismatch. Please try again."));
      settle.reject(new Error("state_mismatch"));
      close();
      return;
    }
    const err = params.get("error");
    res.writeHead(200, { "content-type": "text/html", "cache-control": "no-store" }).end(
      err ? PAGE("Sign-in not completed", "You can close this tab and return to VITALIS.")
        : PAGE("Signed in", "You can close this tab and return to VITALIS."));
    settle.resolve({ params });
    close();
  });
  const listen = (port: number) => new Promise<number>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", () => resolve((server.address() as AddressInfo).port));
  });
  let port: number;
  try {
    port = await listen(opts.port);
  } catch (e) {
    if (!opts.fallbackToEphemeral) throw e;
    port = await listen(0);
  }
  const timer = setTimeout(() => { settle.reject(new Error("sign_in_timeout")); close(); }, opts.timeoutMs ?? 10 * 60 * 1000);
  function close() {
    clearTimeout(timer);
    server.close();
  }
  return { redirectUri: `http://127.0.0.1:${port}${opts.path}`, result, close };
}
