import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev: API-Aufrufe an das FastAPI-Backend (Port 8090) proxen.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8090",
    },
  },
});
