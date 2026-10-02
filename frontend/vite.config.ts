import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

/** Where `uv run uvicorn app.main:app` listens. */
const API = "http://localhost:8000";

/** Paths the dev server passes on to the API. The web app's JSON routes go under /api. */
const API_PATHS = ["/api", "/auth", "/healthz", "/docs", "/openapi.json"];

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(API_PATHS.map((path) => [path, API])),
  },
  test: {
    environment: "jsdom",
    environmentOptions: { jsdom: { url: "https://journal.test/" } },
    setupFiles: ["./src/test/setup.ts"],
    unstubGlobals: true,
  },
});
