import { defineConfig } from "vite"
import preact from "@preact/preset-vite"

export default defineConfig({
  plugins: [preact()],
  root: "src",

  // Dev server: proxy /api/* to the live Pi so you can iterate in the
  // browser without deploying every change. Port 80, not 8080 — nginx is
  // the only thing reachable from off-Pi; Flask/Waitress binds to
  // 127.0.0.1:8080 only (see main.py). For `python3 simhost/run.py`
  // instead, point this at http://127.0.0.1:8080 (no nginx involved
  // there, so the real port applies).
  server: {
    proxy: {
      "/api": {
        target: "http://hexagons.local",
        changeOrigin: true,
      },
    },
  },

  build: {
    // Emit into dist/ (one level up from src/)
    outDir: "../dist",
    emptyOutDir: true,
    // Stable filenames — avoids accumulating orphaned hashed assets since
    // deploy.sh mirrors dist/ by path, not by content hash.
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
})
