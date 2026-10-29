import { expect, test } from '@playwright/test';
import { connectDevice, createAlarm, fake, localTimeIn, openApp, resetService } from './helpers';

// US-02 / FR-02 / FR-09: connect to the device and notice when it drops.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-02 connect', () => {
  test('before connecting: arming is impossible and step 1 says "Not connected"', async ({ page }) => {
    await openApp(page);
    const item = await createAlarm(page, 'Early bird', localTimeIn(120));
    await expect(page.locator('#deviceStatus')).toHaveText('Not connected');
    await expect(page.getByRole('button', { name: 'Connect device' })).toBeEnabled();
    await expect(item.getByRole('button', { name: 'Arm alarm' })).toBeDisabled();
    await expect(page.locator('#armDetail')).toContainText('Connect the device first');
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeDisabled();
  });

  test('connect, notice power loss within 5 s, and reconnect on power-on', async ({ page, request }, testInfo) => {
    await openApp(page);
    await connectDevice(page);
    await expect(page.locator('#deviceStatus')).toHaveText(/^Connected to wq-[0-9a-z]+$/i);
    await expect(page.locator('#stepDevice')).toHaveAttribute('data-state', 'connected');
    await expect(page.getByRole('button', { name: 'Connect device' })).toBeHidden();

    const t0 = Date.now();
    await fake(request, 'power-off');
    await expect(page.locator('#deviceStatus')).toHaveText(/Reconnecting/, { timeout: 5_000 });
    const detectMs = Date.now() - t0;
    testInfo.annotations.push({ type: 'disconnect-detection-ms', description: String(detectMs) });
    console.log(`[measure] disconnect detection: ${detectMs} ms`);
    expect(detectMs).toBeLessThan(5_000);
    await expect(page.locator('#stepDevice')).toHaveAttribute('data-state', 'reconnecting');
    await expect(page.locator('#deviceDetail')).not.toHaveText('');

    const t1 = Date.now();
    await fake(request, 'power-on');
    await expect(page.locator('#deviceStatus')).toHaveText(/^Connected to wq-/, { timeout: 15_000 });
    const reconnectMs = Date.now() - t1;
    testInfo.annotations.push({ type: 'reconnect-ms', description: String(reconnectMs) });
    console.log(`[measure] reconnect after power-on: ${reconnectMs} ms`);
  });
});
