import { defineConfig, devices } from '@playwright/test';

/**
 * The suite runs against a backend the caller starts (the Python gate does),
 * so there is no `webServer` here -- only a base URL.  There is no GPU on the
 * cluster node: Chromium's headless shell serves WebGL 2 through SwiftShader,
 * which deck.gl is happy with, and the flags below say so explicitly rather
 * than relying on the default.
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:5006',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
    trace: 'off',
    video: 'off',
    screenshot: 'off',
    viewport: { width: 1500, height: 950 },
    launchOptions: {
      args: ['--enable-unsafe-swiftshader', '--disable-dev-shm-usage'],
    },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
