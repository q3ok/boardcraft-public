import { defineConfig } from "vitest/config";
export default defineConfig({
  base: "./",
  plugins: [
    {
      name: "shader-line-endings",
      renderChunk: {
        order: "post",
        handler(code) {
          return { code: code.replace(/[\t ]+$/gm, ""), map: null };
        },
      },
    },
  ],
  build: {
    outDir: "../src/boardcraft/server/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: { environment: "jsdom", include: ["tests/**/*.test.ts"] },
});
