import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const appPublic = path.join(here, "public");
const MIME = {
  ".webmanifest": "application/manifest+json",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".json": "application/json",
  ".txt": "text/plain",
};

/**
 * `publicDir` is the shared data folder, so the demo data ships with the site. The app's own
 * static files (web manifest, icons) live in web/public: this plugin serves them in dev, copies
 * them into dist at build time and links the manifest and icons from the page.
 */
function appPublicFiles() {
  let outDir = "dist";
  return {
    name: "overfield-app-public",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = decodeURIComponent((req.url || "").split("?")[0]);
        const file = path.join(appPublic, url);
        if (!file.startsWith(appPublic) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) return next();
        res.setHeader("Content-Type", MIME[path.extname(file)] || "application/octet-stream");
        fs.createReadStream(file).pipe(res);
      });
    },
    closeBundle() {
      if (fs.existsSync(appPublic)) fs.cpSync(appPublic, outDir, { recursive: true });
    },
    transformIndexHtml: {
      order: "post",
      handler() {
        return [
          { tag: "link", attrs: { rel: "manifest", href: "./manifest.webmanifest" }, injectTo: "head" },
          { tag: "link", attrs: { rel: "icon", type: "image/svg+xml", href: "./icons/icon.svg" }, injectTo: "head" },
          { tag: "link", attrs: { rel: "apple-touch-icon", href: "./icons/apple-touch-icon.png" }, injectTo: "head" },
        ];
      },
    },
  };
}

export default defineConfig({
  base: "./",
  publicDir: "../data",
  server: { port: 5173 },
  resolve: {
    alias: {
      // satellite.js's optional WebAssembly builds: not used, kept out of the bundle (see src/globe/no-wasm.js).
      "#wasm-single-thread": path.join(here, "src/globe/no-wasm.js"),
      "#wasm-multi-thread": path.join(here, "src/globe/no-wasm.js"),
    },
  },
  // satellite.js is plain ESM; pre-bundling it would follow those WebAssembly imports past the alias.
  optimizeDeps: { exclude: ["satellite.js"] },
  plugins: [appPublicFiles()],
});
