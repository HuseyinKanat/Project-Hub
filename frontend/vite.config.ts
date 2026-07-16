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
      // HTTP only. WebSockets do NOT go through Vite: vite-5's WS proxy cannot forward
      // the upgrade handshake (QA proved /ws times out), so useWebSocket.ts connects the
      // browser straight to the backend origin (hostname:8000). Keep /api and /mcp here —
      // these HTTP paths work through the proxy (prod returns 200). (PH-324)
      "/api": {
        target: process.env.VITE_API_TARGET ?? "http://backend:8000",
        changeOrigin: true,
      },
      "/mcp": {
        target: process.env.VITE_API_TARGET ?? "http://backend:8000",
        changeOrigin: true,
      },
    },
  },
});
