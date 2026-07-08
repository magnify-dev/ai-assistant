import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5175,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8767",
        changeOrigin: true,
        timeout: 0,
        proxyTimeout: 0,
        configure: (proxy) => {
          proxy.on("error", (err, _req, res) => {
            // Benign when API restarts while a proxied SSE connection is open.
            if (res && "writeHead" in res && !res.headersSent) {
              res.writeHead(502, { "Content-Type": "text/plain" });
              res.end("API restarting — retry in a moment");
            }
          });
        },
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
