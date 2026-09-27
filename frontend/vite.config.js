import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes the site into ../web, which the Python server (PC app and Vercel) serves.
// `npm run dev` proxies /api to a TrendBot running locally on port 8765.
export default defineConfig({
  plugins: [react()],
  base: "./",
  build: { outDir: "../web", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
