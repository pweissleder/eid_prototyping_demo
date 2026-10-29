import { expect, test } from '@playwright/test';
import {
  createAlarm,
  fake,
  fakeDevice,
  getSessions,
  getState,
  localTimeIn,
  resetService,
  ringAndTap,
  setupReady,
  startTestAlarmAndRing,
  waitForDeviceActive,
} from './helpers';

// US-08 / FR-08: the presenter can run, stop and reset the demo without breaking the real alarm.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-08 presenter', () => {
  test('a test alarm leaves the armed study alarm armed', async ({ page, request }) => {
    await setupReady(page);
    const item = await createAlarm(page, 'Real alarm', localTimeIn(120));
    await item.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for /);
    const armedHeadline = await page.locator('#setupHeadline').innerText();

    await ringAndTap(page, request);
    await page.getByRole('button', { name: 'Back to setup' }).click();

    await expect(page.locator('#setupView')).toBeVisible();
    await expect(page.locator('#setupHeadline')).toHaveText(armedHeadline);
    await expect(page.locator('#armStatus')).toHaveText('“Real alarm” is armed');
    await expect(page.locator('#alarmList li.alarm').filter({ hasText: 'Real alarm' }).locator('.status')).toHaveText('Armed');
    expect((await getState(request)).armedAlarm.name).toBe('Real alarm');
    expect((await getState(request)).points.study).toBe(0);
  });

  test('"stop run (aborted)" during ringing ends the run without points', async ({ page, request }) => {
    await setupReady(page);
    await startTestAlarmAndRing(page);
    await waitForDeviceActive(request);
    await page.getByRole('button', { name: 'Presenter: stop run (aborted)' }).click();

    await expect(page.locator('#doneView')).toBeVisible();
    await expect(page.locator('#doneHeadline')).toHaveText('Run stopped.');
    await expect(page.locator('#donePoints')).toHaveText('No points for this run.');
    await expect(page.locator('#doneTime')).toHaveText('The presenter stopped it.');
    await expect(page.locator('body')).toHaveAttribute('data-outcome', 'aborted');
    await expect(page.locator('#sun .ray')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);
    await expect.poll(async () => (await fakeDevice(request)).indicator).toBe(false);
    const state = await getState(request);
    expect(state.run.state).toBe('aborted');
    expect(state.run.session.abortReason).toBe('manual');
    expect(state.run.session.result.points).toBe(0);
  });

  test('reset returns to ready in under 30 s (from ringing and from the result view)', async ({ page, request }, testInfo) => {
    await setupReady(page);

    // From ringing, through the service (the reset button is in the setup view).
    await startTestAlarmAndRing(page);
    let t0 = Date.now();
    expect((await request.post('/api/demo/reset')).ok()).toBeTruthy();
    await expect(page.locator('#setupView')).toBeVisible();
    await expect(page.locator('#setupHeadline')).toHaveText('No alarm armed yet.');
    const fromRingMs = Date.now() - t0;
    expect(fromRingMs).toBeLessThan(30_000);
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeEnabled();
    await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);

    // From the result view, with the UI button.
    await ringAndTap(page, request);
    t0 = Date.now();
    await page.getByRole('button', { name: 'Back to setup' }).click();
    await expect(page.locator('#setupView')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeEnabled();
    const fromDoneMs = Date.now() - t0;
    expect(fromDoneMs).toBeLessThan(30_000);

    // And again with the presenter's "Reset demo" button after another run, then the next demo starts cleanly.
    await startTestAlarmAndRing(page);
    await page.getByRole('button', { name: 'Presenter: stop run (aborted)' }).click();
    await expect(page.locator('#doneView')).toBeVisible();
    await page.getByRole('button', { name: 'Back to setup' }).click();
    await page.getByRole('button', { name: 'Reset demo' }).click();
    await expect(page.locator('#setupView')).toBeVisible();
    await ringAndTap(page, request);

    testInfo.annotations.push({ type: 'reset-from-ringing-ms', description: String(fromRingMs) });
    testInfo.annotations.push({ type: 'reset-from-done-ms', description: String(fromDoneMs) });
    console.log(`[measure] reset to ready: from ringing ${fromRingMs} ms, from result view ${fromDoneMs} ms`);
  });

  test('cancelling a pending test alarm returns to idle and nothing rings', async ({ page, request }) => {
    await setupReady(page);
    await request.post('/api/_test/test-alarm-delay', { data: { seconds: 6 } });
    await page.getByRole('button', { name: 'Test alarm in 10 seconds' }).click();
    await expect(page.locator('#setupHeadline')).toHaveText(/^Test alarm rings in \d+ s\.$/);

    await page.getByRole('button', { name: 'Stop run (aborted)', exact: true }).click();
    await expect(page.locator('#setupHeadline')).toHaveText('No alarm armed yet.');
    await expect(page.getByRole('button', { name: 'Stop run (aborted)', exact: true })).toBeHidden();
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeEnabled();
    expect((await getState(request)).run.state).toBe('idle');

    // Past the original fire time: still idle, light off, no sound.
    await page.waitForTimeout(6_500);
    expect((await getState(request)).run.state).toBe('idle');
    await expect(page.locator('#setupView')).toBeVisible();
    expect((await fakeDevice(request)).indicator).toBe(false);
    expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);
  });

  test('device lost during ringing: out-of-contact notice, sound keeps playing, tap after power-on completes once', async ({
    page,
    request,
  }) => {
    await setupReady(page);
    const completedBefore = (await getSessions(request)).filter((s) => s.outcome === 'successful_tap').length;
    await startTestAlarmAndRing(page);
    await waitForDeviceActive(request);
    await expect
      .poll(async () => (await getState(request)).run.session.activation.status, { timeout: 10_000 })
      .toBe('acked');

    await fake(request, 'power-off');
    await expect(page.locator('#ringIssues')).toContainText('The device is out of contact', { timeout: 8_000 });
    await expect(page.locator('#ringIssues')).toContainText('The alarm keeps ringing until the tap gets through.');
    await expect(page.locator('#ringHeadline')).toHaveText('Pick up your device and tap it');
    expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
    // No way out from the screen while the device is gone.
    expect(await page.locator('body button:visible').allInnerTexts()).toEqual(['Presenter: stop run (aborted)']);

    await fake(request, 'power-on');
    // The service re-syncs the session; the device lights up again and acknowledges it.
    await expect(page.locator('#ringIssues')).not.toContainText('out of contact', { timeout: 15_000 });
    await waitForDeviceActive(request);
    await expect
      .poll(async () => (await getState(request)).run.session.activation.status, { timeout: 10_000 })
      .toBe('acked');
    expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);

    const tap = await fake(request, 'tap');
    expect((await tap.json()).sent).toBe(true);
    await expect(page.locator('#doneView')).toBeVisible();
    await expect(page.locator('#doneHeadline')).toHaveText('Morning quest completed!');
    await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);
    const state = await getState(request);
    expect(state.run.session.connectionLostDuringRun).toBe(true);
    expect(state.run.session.technicalIssues).toContain('connection_lost');
    expect(state.points.study).toBe(0);
    expect((await getSessions(request)).filter((s) => s.outcome === 'successful_tap')).toHaveLength(completedBefore + 1);
  });
});
