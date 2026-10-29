import { expect, test } from '@playwright/test';
import {
  createAlarm,
  fake,
  fakeDevice,
  getState,
  localTimeIn,
  resetService,
  setupReady,
  startTestAlarmAndRing,
  waitForDeviceActive,
} from './helpers';

// US-05 / FR-06: a tap stops the alarm, but only a valid tap during ringing.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

async function lastTap(request: any) {
  return (await getState(request)).device.lastTap;
}

async function activationAcked(request: any) {
  await expect
    .poll(async () => (await getState(request)).run.session?.activation?.status, { timeout: 10_000 })
    .toBe('acked');
}

test.describe('US-05 tap', () => {
  test('a tap before any run does not complete anything (idle and armed)', async ({ page, request }) => {
    await setupReady(page);

    const before = await lastTap(request);
    const res = await fake(request, 'tap');
    expect((await res.json()).sent).toBe(true);
    await expect.poll(() => lastTap(request)).not.toEqual(before);
    expect(await lastTap(request)).toMatchObject({ accepted: false, reason: 'idle' });
    expect((await getState(request)).run.state).toBe('idle');
    await expect(page.locator('#setupView')).toBeVisible();
    await expect(page.locator('#doneView')).toBeHidden();

    // Same with an armed alarm waiting for its time.
    const item = await createAlarm(page, 'Not yet', localTimeIn(120));
    await item.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for /);
    const seq = (await lastTap(request)).seq;
    await fake(request, 'tap');
    await expect.poll(async () => (await lastTap(request)).seq).not.toBe(seq);
    expect(await lastTap(request)).toMatchObject({ accepted: false, reason: 'armed' });
    expect((await getState(request)).run.state).toBe('armed');
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for /);
    await expect(page.locator('#doneView')).toBeHidden();
  });

  test('a tap with a stale session id does not complete; the right tap does and stops sound and light', async ({
    page,
    request,
  }) => {
    await setupReady(page);
    await startTestAlarmAndRing(page);
    await waitForDeviceActive(request);
    await activationAcked(request);

    const before = await lastTap(request);
    const stale = await fake(request, 'tap', { sessionId: 's-old' });
    expect((await stale.json()).sent).toBe(true);
    await expect.poll(() => lastTap(request)).not.toEqual(before);
    expect(await lastTap(request)).toMatchObject({ accepted: false, reason: 'stale_session', tapSessionId: 's-old' });
    expect((await getState(request)).run.state).toBe('ringing');
    await expect(page.locator('#ringView')).toBeVisible();
    await expect(page.locator('#doneView')).toBeHidden();
    expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
    expect((await fakeDevice(request)).indicator).toBe(true);

    // The real tap.
    await fake(request, 'tap');
    await expect(page.locator('#doneView')).toBeVisible();
    await expect(page.locator('#doneHeadline')).toHaveText('Morning quest completed!');
    await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);
    await expect.poll(async () => (await fakeDevice(request)).indicator).toBe(false);
    expect((await getState(request)).run.state).toBe('completed');
    expect(await lastTap(request)).toMatchObject({ accepted: true });
  });
});
