import { expect, test } from '@playwright/test';
import {
  fake,
  fakeDevice,
  getState,
  resetService,
  setupReady,
  startTestAlarmAndRing,
  visibleButtonNames,
  waitForDeviceActive,
} from './helpers';

// US-04 / FR-04 / FR-05 / QR-07: the alarm takes over the screen and has no way out except the tap.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-04 ringing', () => {
  test('countdown, then the ring view with sound and light; no dismiss or snooze', async ({ page, request }) => {
    await setupReady(page);
    await request.post('/api/_test/test-alarm-delay', { data: { seconds: 5 } }); // long enough to see the countdown
    await page.getByRole('button', { name: 'Test alarm in 10 seconds' }).click();

    // Countdown in the setup headline.
    await expect(page.locator('#setupHeadline')).toHaveText(/^Test alarm rings in \d+ s\.$/);
    await expect(page.locator('#setupSub')).toContainText('This is a demo run');
    await expect(page.locator('#presenterMsg')).toContainText('Test alarm counting down');
    await expect(page.getByRole('button', { name: 'Stop run (aborted)', exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeDisabled();

    // The ring view takes over.
    await expect(page.locator('#ringView')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('#setupView')).toBeHidden();
    await expect(page.locator('#ringHeadline')).toHaveText('Pick up your device and tap it');
    await expect(page.locator('#ringLabel')).toHaveText('Test alarm (demo run)');
    await expect(page.locator('#ringElapsed')).toHaveText(/^Ringing for \d+ s$/);
    await expect(page.locator('body')).toHaveAttribute('data-view', 'ring');

    // Sound plays in the browser and is confirmed to the service.
    await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
    await expect
      .poll(() => page.evaluate(() => (window as any).__wq.soundSessionId))
      .toBe((await getState(request)).run.session.sessionId);
    await expect
      .poll(async () => (await getState(request)).run.session.sound.status)
      .toBe('started');
    await expect(page.locator('#startSoundBtn')).toBeHidden();

    // The device light is on.
    await waitForDeviceActive(request);
    expect((await fakeDevice(request)).sessionId).toBe((await getState(request)).run.session.sessionId);

    // The only control is the presenter's abort. No dismiss, snooze, close or stop-sound buttons.
    const buttons = await visibleButtonNames(page, 'body');
    expect(buttons).toEqual(['Presenter: stop run (aborted)']);
    await expect(page.getByRole('button', { name: /dismiss|snooze|close|mute|later/i })).toHaveCount(0);

    // Ringing keeps going; time passing does not end it.
    await page.waitForTimeout(1500);
    await expect(page.locator('#ringView')).toBeVisible();
    await expect(page.locator('#ringIssues li')).toHaveCount(0);
  });

  test('if the device never confirms its light, the ring view says so', async ({ page, request }) => {
    await setupReady(page);
    await fake(request, 'no-ack', { enabled: true });
    await startTestAlarmAndRing(page);
    await expect(page.locator('#ringIssues')).toContainText('has not confirmed its light', { timeout: 8_000 });
    await expect(page.locator('#ringIssues')).toContainText('Taps count as soon as it does.');
    // Still ringing, sound still playing.
    await expect(page.locator('#ringHeadline')).toHaveText('Pick up your device and tap it');
    expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
  });
});
