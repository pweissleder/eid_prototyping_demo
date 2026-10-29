import { expect, test } from '@playwright/test';
import {
  connectDevice,
  createAlarm,
  enableSound,
  fake,
  fakeDevice,
  localTimeIn,
  openApp,
  resetService,
  waitForSnapshot,
} from './helpers';

// US-03 / FR-03 / FR-09: the alarm can only be armed when everything works.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-03 arming gate', () => {
  test('without the sound test arming is impossible and the missing step is listed', async ({ page }) => {
    await openApp(page);
    await connectDevice(page);
    const item = await createAlarm(page, 'Needs sound', localTimeIn(120));
    await expect(page.locator('#soundStatus')).toHaveText('Not tested');
    await expect(item.getByRole('button', { name: 'Arm alarm' })).toBeDisabled();
    await expect(page.locator('#armDetail')).toHaveText('Enable and test sound in the browser first.');
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeDisabled();

    await enableSound(page);
    await expect(item.getByRole('button', { name: 'Arm alarm' })).toBeEnabled();
  });

  test('with an unstable sensor arming is impossible until the device is still', async ({ page, request }) => {
    await fake(request, 'sensor-unstable');
    await openApp(page);
    await connectDevice(page);
    await enableSound(page);
    const item = await createAlarm(page, 'Shaky', localTimeIn(120));
    await expect(page.locator('#deviceDetail')).toContainText('keep it still for 2 seconds');
    await expect(page.locator('#stepDevice')).toHaveAttribute('data-done', 'false');
    await expect(item.getByRole('button', { name: 'Arm alarm' })).toBeDisabled();
    await expect(page.locator('#armDetail')).not.toHaveText('');

    await fake(request, 'sensor-stable');
    await expect(page.locator('#deviceDetail')).toHaveText('Tap sensor is resting and ready.', { timeout: 10_000 });
    await expect(item.getByRole('button', { name: 'Arm alarm' })).toBeEnabled();
  });

  test('after connect + sound test + stable sensor the alarm arms; the light stays off', async ({ page, request }) => {
    await openApp(page);
    await connectDevice(page);
    await enableSound(page);
    const item = await createAlarm(page, 'Ready to go', localTimeIn(120));
    await expect(page.locator('#stepDevice')).toHaveAttribute('data-done', 'true');
    await expect(page.locator('#stepSound')).toHaveAttribute('data-done', 'true');
    await expect(page.locator('#armDetail')).toHaveText('Pick an alarm in the list and press “Arm alarm”.');
    await item.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for \d{2}:\d{2} /);
    await expect(page.locator('#setupSub')).toContainText('The device light stays off until it rings.');
    await waitForSnapshot(page, "s.run.state === 'armed'");
    expect((await fakeDevice(request)).indicator).toBe(false);
    // And it stays off a moment later (no stray activation).
    await page.waitForTimeout(1500);
    expect((await fakeDevice(request)).indicator).toBe(false);
  });
});
