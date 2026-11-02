/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes the finished app into ../docintel/static, which the Python server serves.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../docintel/static", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 600 },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8000" } },
  test: { environment: "jsdom", setupFiles: ["./src/test-setup.ts"], globals: true },
});
