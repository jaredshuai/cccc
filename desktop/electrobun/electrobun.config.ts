import type { ElectrobunConfig } from "electrobun/bun";

const config: ElectrobunConfig = {
  app: {
    name: "CCCC",
    identifier: "com.chesterra.cccc",
    version: "0.4.2",
    urlSchemes: ["cccc"],
  },
  build: {
    buildFolder: "dist",
    artifactFolder: "artifacts",
    bun: {
      entrypoint: "src/bun/index.ts",
      minify: true,
      sourcemap: "external",
    },
    // Copy standalone backend directory into packaged Resources/app/cccc-backend
    copy: {
      "../dist/cccc-backend": "cccc-backend",
    },
  },
  runtime: {},
  release: {
    baseUrl: "https://releases.cccc.app/updates",
    generatePatch: false,
  },
};

export default config;
