import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "node:path";

export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src"), "@shared": path.resolve(__dirname, "shared") } },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2500, sourcemap: false },
  server: { port: 5173, strictPort: true },
});
