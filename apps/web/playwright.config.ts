import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end flows against the mock control API (mock/server.ts), which also serves the
 * built SPA exactly as the reverse proxy will (same origin, SPA fallback, CSP headers).
 * The mock keeps global state, so tests run serially and reset it in beforeEach.
 */
const PORT = 4173;

export default defineConfig({
  testDir: 'e2e',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'on-first-retry',
    viewport: { width: 1440, height: 900 },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npm run build && node mock/server.ts --port ${PORT} --static dist --speed 150`,
    url: `http://127.0.0.1:${PORT}/api/v1/health/live`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
