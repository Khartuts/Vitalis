import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import fs from "node:fs";
import path from "node:path";
import { RENDERER_ENGINE_METHODS } from "./shared/contract";

/** `vite build --mode android`: the same app for the phone's WebView, with the Android bridge shim and a CSP
 *  without the dev-server allowances. Output: dist-android (copied into the APK by the Android build). */
function androidShell(): Plugin {
  return {
    name: "vitalis-android-shell",
    transformIndexHtml(html) {
      return html
        .replace(/connect-src [^;]*;/, "connect-src 'self';")
        .replace('<script type="module"', '<script src="./android-bridge.js"></script>\n    <script type="module"');
    },
    generateBundle() {
      this.emitFile({ type: "asset", fileName: "android-bridge.js",
        source: fs.readFileSync(path.resolve(__dirname, "android/android-bridge.js"), "utf8") });
      // The same engine-method allow-list as the desktop's main process, enforced by the phone app.
      this.emitFile({ type: "asset", fileName: "engine-methods.json", source: JSON.stringify(RENDERER_ENGINE_METHODS) });
    },
  };
}

export default defineConfig(({ mode }) => {
  const android = mode === "android";
  return {
    base: "./",
    plugins: [react(), tailwindcss(), ...(android ? [androidShell()] : [])],
    resolve: { alias: { "@": path.resolve(__dirname, "src"), "@shared": path.resolve(__dirname, "shared") } },
    build: { outDir: android ? "dist-android" : "dist", emptyOutDir: true, chunkSizeWarningLimit: 2500, sourcemap: false },
    server: { port: 5173, strictPort: true },
  };
});
