import { defineConfig } from "vite";

const SV = process.env.SUPERVOICE_URL?.replace(/^ws/, "http") ?? "http://localhost:9000";

export default defineConfig({
  server: {
    port: 5173,
    proxy: {
      "/connect": { target: SV, changeOrigin: true, secure: false },
      "/ws": { target: SV.replace(/^http/, "ws"), ws: true, changeOrigin: true },
    },
  },
});
