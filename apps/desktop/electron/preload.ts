// Sandboxed preload: exposes a narrow, allow-listed bridge. No Node APIs reach the renderer.
import { contextBridge, ipcRenderer, type IpcRendererEvent } from "electron";

const INVOKE = new Set([
  "app:info", "app:openExternal", "profiles:list", "profiles:create", "profiles:open", "profiles:rename", "profiles:delete",
  "engine:call", "data:wipe", "sync:start", "sync:stop", "sync:pairing", "import:pick", "import:commit", "export:all", "export:csv",
  "report:json", "report:pdf", "chart:savePng", "backup:create", "backup:restore", "ai:status", "ai:models", "ai:apikey:set",
  "ai:apikey:clear", "ai:local:set", "ai:siwc:signIn", "ai:siwc:cancel", "ai:siwc:signOut", "ai:siwc:welcomed", "ai:send",
  "ai:decide", "ai:cancel", "google:status", "google:config", "google:connect", "google:sync", "google:disconnect",
]);
const EVENTS = new Set(["engine:event", "ai:event"]);

contextBridge.exposeInMainWorld("vitalis", {
  invoke: (channel: string, payload?: unknown) => {
    if (!INVOKE.has(channel)) return Promise.reject(new Error(`channel not allowed: ${channel}`));
    return ipcRenderer.invoke(channel, payload);
  },
  on: (channel: string, cb: (payload: unknown) => void) => {
    if (!EVENTS.has(channel)) throw new Error(`event not allowed: ${channel}`);
    const listener = (_e: IpcRendererEvent, payload: unknown) => cb(payload);
    ipcRenderer.on(channel, listener);
    return () => ipcRenderer.removeListener(channel, listener);
  },
  reportReady: () => ipcRenderer.send("report:ready"),
});
