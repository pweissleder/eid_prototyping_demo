import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const PORT = 8765;
const BASE = `http://127.0.0.1:${PORT}`;
// Fresh data directory per run (test-results/ is gitignored).
const dataDir = path.resolve('test-results', `e2e-data-${Date.now()}`);

export default defineConfig({
  testDir: './e2e',
  // One shared service state: tests run strictly one after another.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 45_000,
  expect: { timeout: 8_000 },
  reporter: [['list']],
  // Keep Playwright's artifact cleanup away from the data directory.
  outputDir: 'test-results/artifacts',
  use: {
    baseURL: BASE,
    locale: 'en-GB', // 24-hour times in the alarm list (the page formats with the browser locale)
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: `uv run uvicorn wakequest.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `${BASE}/api/state`,
    reuseExistingServer: false,
    // Server logs are noisy; switch to 'pipe' when debugging the service.
    stdout: 'ignore',
    stderr: 'ignore',
    timeout: 60_000,
    gracefulShutdown: { signal: 'SIGTERM', timeout: 3000 },
    env: {
      WAKEQUEST_DEVICE: 'fake',
      WAKEQUEST_TEST_MODE: '1',
      WAKEQUEST_TEST_ALARM_DELAY_S: '1',
      WAKEQUEST_DATA_DIR: dataDir,
    },
  },
  projects: [
    {
      name: 'chromium',
      testIgnore: /blocked-audio\.spec\.ts/,
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: { args: ['--autoplay-policy=no-user-gesture-required'] },
      },
    },
    {
      // No autoplay flag: Chromium's default policy applies.
      name: 'chromium-blocked-audio',
      testMatch: /blocked-audio\.spec\.ts/,
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
