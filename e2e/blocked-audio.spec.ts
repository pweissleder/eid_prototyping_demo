import { expect, test, type Page } from '@playwright/test';
import {
  connectDevice,
  enableSound,
  getState,
  openApp,
  resetService,
  startTestAlarmAndRing,
  waitForSnapshot,
} from './helpers';

// FR-04 / QR-07: when the browser blocks the alarm sound, the page says so and offers a button.
// Runs only in the `chromium-blocked-audio` project, i.e. without --autoplay-policy=no-user-gesture-required.
//
// FINDING (Chromium 156, Playwright headless, macOS): even without the flag and even with an explicit
// --autoplay-policy=document-user-activation-required, a page loaded by Playwright has
// navigator.userActivation.hasBeenActive === true and `new AudioContext()` is 'running', so a reload does
// NOT block the alarm sound. The faithful test is therefore marked fixme, and a clearly labelled
// *simulation* of Chromium's autoplay refusal covers the page's blocked-audio behaviour instead.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

async function setUpRinging(page: Page) {
  await openApp(page);
  await connectDevice(page);
  await enableSound(page); // a click is a user gesture, so this works without the flag
  await startTestAlarmAndRing(page);
  await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
}

/** Blocked: button offered, nothing playing. Then one click starts the sound. */
async function expectButtonThenStartedByClick(page: Page, request: any) {
  await expect(page.locator('#ringView')).toBeVisible();
  await waitForSnapshot(page, "s.run.state === 'ringing'");
  const sessionId = (await getState(request)).run.session.sessionId;
  await expect(page.locator('#startSoundBtn')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#startSoundBtn')).toHaveText('Start the alarm sound');
  expect(await page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(false);

  // One click is a user gesture: the sound starts and the service is told.
  await page.getByRole('button', { name: 'Start the alarm sound' }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__wq.soundPlaying)).toBe(true);
  await expect(page.locator('#startSoundBtn')).toBeHidden();
  await expect(page.locator('#ringIssues')).not.toContainText('blocked');
  await waitForSnapshot(page, "s.run.session.sound.status === 'started'");
  expect(await page.evaluate(() => (window as any).__wq.soundSessionId)).toBe(sessionId);
  expect((await getState(request)).run.session.sound.status).toBe('started');
}

const BLOCKED_TEXT = 'The browser blocked the alarm sound.';

// The real thing. Does not block in headless Chromium (see FINDING above), so it is parked.
test.fixme('after a reload during ringing the browser blocks the sound; one click starts it (real policy)', async ({ page, request }) => {
  await setUpRinging(page);
  await page.reload(); // fresh document, no user gesture
  await expect(page.locator('#startSoundBtn')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#ringIssues')).toContainText(BLOCKED_TEXT);
  await expectButtonThenStartedByClick(page, request);
});

// Simulation: an init script makes every AudioContext report 'suspended' (and resume() hang) until the
// first trusted click or key press in the current document, which is what Chromium's default autoplay
// policy does on a normal desktop browser. It exercises the page's handling, not Chromium's policy.
async function simulateAutoplayBlock(page: Page) {
  await page.addInitScript(() => {
    let gesture = false;
    const unlock = (e: Event) => { if (e.isTrusted) gesture = true; };
    for (const type of ['pointerdown', 'mousedown', 'click', 'keydown', 'touchstart']) {
      window.addEventListener(type, unlock, true);
    }
    const proto = (window as any).BaseAudioContext.prototype;
    const stateDesc = Object.getOwnPropertyDescriptor(proto, 'state')!;
    Object.defineProperty(proto, 'state', {
      configurable: true,
      get() { return gesture ? stateDesc.get!.call(this) : 'suspended'; },
    });
    const realResume = (window as any).AudioContext.prototype.resume;
    (window as any).AudioContext.prototype.resume = function () {
      return gesture ? realResume.call(this) : new Promise(() => {});
    };
  });
}

test('SIMULATED block, reload during ringing: the page offers "Start the alarm sound" and one click plays it', async ({ page, request }) => {
  await simulateAutoplayBlock(page);
  await setUpRinging(page);
  await page.reload(); // new document: the simulated gesture flag is false again
  await expectButtonThenStartedByClick(page, request);
});

// The sound was already confirmed for this session before the reload, so the service keeps
// sound.status "started" (it ignores a later sound_failed); the page explains the block from its own state.
test('SIMULATED block, reload during ringing: the ring view explains that the browser blocked the sound', async ({ page }) => {
  await simulateAutoplayBlock(page);
  await setUpRinging(page);
  await page.reload();
  await expect(page.locator('#startSoundBtn')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#ringIssues')).toContainText(BLOCKED_TEXT, { timeout: 3_000 });
});

// First ring of the session blocked (tab reloaded during the countdown): sound_failed is the first report,
// so the service shows status "failed" and the page explains it.
test('SIMULATED block, reload during the countdown: "browser blocked the alarm sound" and the button', async ({ page, request }) => {
  await simulateAutoplayBlock(page);
  await openApp(page);
  await connectDevice(page);
  await enableSound(page);
  await request.post('/api/_test/test-alarm-delay', { data: { seconds: 5 } });
  await page.getByRole('button', { name: 'Test alarm in 10 seconds' }).click();
  await expect(page.locator('#setupHeadline')).toHaveText(/^Test alarm rings in \d+ s\.$/);
  await page.reload(); // no gesture in the new document
  await expect(page.locator('#ringView')).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('#startSoundBtn')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('#ringIssues')).toContainText(BLOCKED_TEXT);
  await expect.poll(async () => (await getState(request)).run.session.sound.status).toBe('failed');
  expect((await getState(request)).run.session.technicalIssues).toContain('sound_failed');
  await expectButtonThenStartedByClick(page, request);
});
