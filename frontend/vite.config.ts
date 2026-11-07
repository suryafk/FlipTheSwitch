import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";
import { defineConfig } from "vitest/config";

// Environment variables come from the repository-root .env (shared with the backend).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, "..", "");
  const backendUrl = env.BLACKBOX_BACKEND_URL || "http://localhost:8010";
  const port = Number(env.BLACKBOX_FRONTEND_PORT || 5180);

  return {
    envDir: "..",
    plugins: [react(), tailwindcss()],
    server: {
      port,
      strictPort: true,
      // In development the browser calls /api on the Vite origin; Vite forwards it to FastAPI.
      proxy: { "/api": { target: backendUrl, changeOrigin: true } },
    },
    preview: { port, strictPort: true },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      css: false,
    },
  };
});
