import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run build` writes the site into ../web, which the Python server (PC app and Vercel) serves.
// `npm run dev` proxies /api to a TrendBot running locally on port 8765.
export default defineConfig({
  plugins: [react()],
  base: "./",
  // Target older phones and browsers too (iOS 14+, Chrome/Edge 87+, Firefox 78+).
  build: { outDir: "../web", emptyOutDir: true, target: ["es2020", "safari14", "chrome87", "edge88", "firefox78"] },
  server: { proxy: { "/api": "http://127.0.0.1:8765" } },
});
