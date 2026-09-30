const { defineConfig } = require("@playwright/test");

const PORT = 8790;

module.exports = defineConfig({
  testDir: "./tests",
  // A busy machine makes headless Chrome's software WebGL slow to start, and a
  // page that takes 40 s to load is not a failing test.
  timeout: 60_000,
  // One document behind one server, so tests cannot run side by side.
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 1280, height: 800 },
    // The installed Chrome, so no browser download is needed. SwiftShader
    // gives headless Chrome a WebGL context on any machine.
    channel: "chrome",
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] },
  },
  webServer: {
    command: "./serve.sh",
    url: `http://127.0.0.1:${PORT}/index.html`,
    env: { PORT: String(PORT) },
    // REUSE_SERVER=1 attaches to a server already running on the port, for debugging.
    reuseExistingServer: Boolean(process.env.REUSE_SERVER),
    timeout: 60_000,
  },
});
