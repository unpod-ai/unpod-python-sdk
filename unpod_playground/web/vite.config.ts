import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In dev, proxy the harness control plane (sessions + events side-channel) to
// the FastAPI server. The audio WebSocket uses the absolute ws_url returned by
// POST /playground/sessions, so it connects directly to supervoice.
const HARNESS = process.env.HARNESS_URL ?? "http://localhost:9100";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/playground": { target: HARNESS, changeOrigin: true, ws: true },
      "/connect": { target: HARNESS, changeOrigin: true },
    },
  },
});
