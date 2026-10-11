import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the FastAPI backend, so the browser never needs credentials or the API key.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": process.env.VITE_API_TARGET || "http://localhost:8000" },
  },
});
