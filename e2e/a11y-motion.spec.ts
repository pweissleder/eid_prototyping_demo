import { expect, test } from '@playwright/test';
import { createAlarm, localTimeIn, resetService, ringAndTap, setupReady, startTestAlarmAndRing } from './helpers';

// QR-07 and basic accessibility: reduced motion, live-region announcements, narrow screens.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

const rayAnimation = (page: import('@playwright/test').Page) =>
  page.locator('#sun .ray').first().evaluate((el) => getComputedStyle(el).animationName);

test.describe('accessibility and motion basics', () => {
  test('reduced motion swaps the sun-ray animation for a fade', async ({ page, request }) => {
    await setupReady(page);
    await ringAndTap(page, request);
    await expect(page.locator('#sun .ray').first()).toBeAttached();

    await page.emulateMedia({ reducedMotion: 'no-preference' });
    expect(await rayAnimation(page)).toBe('ray-out');

    await page.emulateMedia({ reducedMotion: 'reduce' });
    await expect.poll(() => rayAnimation(page)).toBe('ray-fade');
  });

  test('reduced motion is honoured when the page is opened with it already set', async ({ page, request }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await setupReady(page);
    await ringAndTap(page, request);
    await expect.poll(() => rayAnimation(page)).toBe('ray-fade');
  });

  test('the live region announces the headline after each view change', async ({ page, request }) => {
    await setupReady(page);
    await expect(page.locator('#srStatus')).toHaveAttribute('role', 'status');
    await expect(page.locator('#srStatus')).toHaveAttribute('aria-live', 'polite');

    await startTestAlarmAndRing(page);
    await expect(page.locator('#srStatus')).toHaveText('Pick up your device and tap it');

    await page.getByRole('button', { name: 'Presenter: stop run (aborted)' }).click();
    await expect(page.locator('#doneView')).toBeVisible();
    await expect(page.locator('#srStatus')).toHaveText('Run stopped.');

    await page.getByRole('button', { name: 'Back to setup' }).click();
    await expect(page.locator('#srStatus')).toHaveText('No alarm armed yet.');

    await ringAndTap(page, request);
    await expect(page.locator('#srStatus')).toHaveText('Morning quest completed!');
  });

  test('no horizontal scrollbar at 375 px on the setup view', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await setupReady(page);
    await createAlarm(page, 'A fairly long alarm name for a narrow phone screen', localTimeIn(120));
    await page.locator('#logPanel > summary').click();
    const { scrollWidth, clientWidth } = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    expect(clientWidth).toBe(375);
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
    // The primary controls are still reachable on a phone.
    await expect(page.getByRole('button', { name: 'Test alarm in 10 seconds' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save alarm' })).toBeVisible();
  });
});
