import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API + demo-site calls to the FastAPI server so no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/demo": "http://127.0.0.1:8000",
    },
  },
});
