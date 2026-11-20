// Bundles the Electron main (ESM) and preload (CommonJS — required for sandboxed preloads) with esbuild.
import { build, context } from "esbuild";

const watch = process.argv.includes("--watch");
const common = { bundle: true, platform: "node", target: "node22", sourcemap: true, external: ["electron"], logLevel: "info" };
const configs = [
  { ...common, entryPoints: ["electron/main.ts"], outfile: "dist-electron/main.js", format: "esm",
    banner: { js: "import { createRequire as __cr } from 'module'; const require = __cr(import.meta.url);" } },
  { ...common, entryPoints: ["electron/preload.ts"], outfile: "dist-electron/preload.cjs", format: "cjs" },
];
if (watch) {
  for (const c of configs) (await context(c)).watch();
} else {
  await Promise.all(configs.map((c) => build(c)));
}
