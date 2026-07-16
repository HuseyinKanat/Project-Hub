import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  server: {
    port: 5173,
    // Accept any Host header (.local mDNS names, changing LAN IPs, future hostnames).
    // The dev server is intentionally exposed to the LAN (docker `--host 0.0.0.0`) for
    // multi-user access, so Vite's default host-check would 403 remote clients. (PH-324)
    allowedHosts: true,
    proxy: {
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://backend:8000",
        changeOrigin: true,
      },
      "/mcp": {
        target: process.env.VITE_API_TARGET ?? "http://backend:8000",
        changeOrigin: true,
      },
      // WebSocket proxy: the browser connects host-relative (ws://<host>/ws/...) and
      // Vite forwards the upgrade to the backend. Required so remote/LAN clients don't
      // open a WS to their OWN localhost. `ws: true` enables the protocol upgrade. (PH-324)
      "/ws": {
        target: process.env.VITE_API_TARGET ?? "http://backend:8000",
        ws: true,
        changeOrigin: true,
      },
    },
  },
});
