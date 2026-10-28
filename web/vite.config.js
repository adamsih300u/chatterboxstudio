import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, proxy API calls to a locally running FastAPI instance.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/v1": "http://localhost:8000",
    },
  },
});
