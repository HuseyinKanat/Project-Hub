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
    proxy: {
      "/api": "http://backend:8000",
      "/mcp": "http://backend:8000",
      "/ws": {
        target: "http://backend:8000",
        ws: true,
        changeOrigin: true,
        // Avoid Vite HMR WS collision
        configure: (proxy) => {
          proxy.on("error", (err) => {
            console.log("WS proxy error:", err.message);
          });
        },
      },
    },
  },
});
