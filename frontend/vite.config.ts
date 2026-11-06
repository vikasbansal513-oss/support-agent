import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Section 2 (Speed & Scaling), bundle size: react/react-dom/react-router-dom
// almost never change between our own deploys, while the app code in src/
// changes on every release. Without manualChunks, Rollup's default is free to
// fold vendor code into whichever app chunk happens to import it first, so a
// one-line app change can invalidate a vendor-sized chunk the browser already
// has cached. Pinning these three into their own "vendor" chunk means a normal
// deploy only re-downloads the (much smaller) app chunk; returning visitors on
// an unchanged dependency version keep serving "vendor" from cache.
//
// Run `npm run analyze` (builds with --mode analyze, enabling the visualizer
// plugin below) to get dist/stats.html and see the resulting chunk breakdown
// before deciding whether any individual route chunk still needs splitting
// further - the route-level lazy()/import() split in App.tsx already keeps
// each page's own code out of the initial bundle.
export default defineConfig(async ({ mode }) => ({
  plugins: [
    react(),
    ...(mode === "analyze"
      ? [
          // Loaded lazily and only in analyze mode so a normal `vite build`
          // never requires this (devDependency-only, not installed in a
          // production image) package to be present. Dynamic import (not
          // require) because this project is "type": "module".
          (await import("rollup-plugin-visualizer")).visualizer({
            filename: "dist/stats.html",
            gzipSize: true,
            brotliSize: true,
          }),
        ]
      : []),
  ],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ["react", "react-dom", "react-router-dom"],
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
  },
}));
