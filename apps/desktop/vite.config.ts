import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const repoRoot = path.resolve(__dirname, "../..");
const topologySeed = path.join(repoRoot, "data/seed/diagnostics/can-topology.v1.json");

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  root: ".",
  base: "./",
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
      "@can-topology": topologySeed,
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: [__dirname, repoRoot] },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, "index.html"),
        ...(mode === "topology-test"
          ? { topologyHarness: path.resolve(__dirname, "topology-harness.html") }
          : {}),
        ...(mode === "engine-test"
          ? { engineHarness: path.resolve(__dirname, "engine-session-harness.html") }
          : {}),
        ...(mode === "connection-test"
          ? { connectionHarness: path.resolve(__dirname, "obd-connection-harness.html") }
          : {}),
      },
    },
  },
}));
