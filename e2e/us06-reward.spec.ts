import { expect, test } from '@playwright/test';
import { fake, getSessions, getState, resetService, ringAndTap, setupReady } from './helpers';

// US-06 / FR-07: the reward, shown once, with the alarm response time.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-06 reward', () => {
  test('completion view, a second tap changes nothing, and "Back to setup" returns', async ({ page, request }) => {
    await setupReady(page);
    const completedBefore = (await getSessions(request)).filter((s) => s.outcome === 'successful_tap').length;
    const studyBefore = (await getState(request)).points.study;

    await ringAndTap(page, request);

    await expect(page.locator('#doneHeadline')).toHaveText('Morning quest completed!');
    await expect(page.locator('#doneLabel')).toBeVisible();
    await expect(page.locator('#doneLabel')).toHaveText('Test alarm (demo run)');
    await expect(page.locator('#donePoints')).toContainText('+10 points, demo preview');
    await expect(page.locator('#donePoints')).toContainText('Not added to your study points');
    await expect(page.locator('#doneTime')).toHaveText(/^Alarm response time: \d+( min \d{2})? s$/);
    await expect(page.locator('#doneNote')).toContainText('It is not the time you got out of bed.');
    await expect(page.locator('body')).toHaveAttribute('data-outcome', 'completed');
    // The sun has ten rays: one per point.
    await expect(page.locator('#sun .ray')).toHaveCount(10);

    const sessions = await getSessions(request);
    expect(sessions.filter((s) => s.outcome === 'successful_tap')).toHaveLength(completedBefore + 1);
    expect((await getState(request)).points.study).toBe(studyBefore);

    // A second tap after completion changes nothing.
    const seq = (await getState(request)).device.lastTap?.seq;
    const again = await fake(request, 'tap');
    expect((await again.json()).sent).toBe(true);
    await expect
      .poll(async () => (await getState(request)).device.lastTap?.seq)
      .not.toBe(seq);
    expect((await getState(request)).device.lastTap.accepted).toBe(false);
    await page.waitForTimeout(500);
    await expect(page.locator('#doneHeadline')).toHaveText('Morning quest completed!');
    expect((await getState(request)).points.study).toBe(studyBefore);
    expect((await getSessions(request)).filter((s) => s.outcome === 'successful_tap')).toHaveLength(completedBefore + 1);
    expect((await getState(request)).run.state).toBe('completed');

    // Back to setup.
    await page.getByRole('button', { name: 'Back to setup' }).click();
    await expect(page.locator('#setupView')).toBeVisible();
    await expect(page.locator('#doneView')).toBeHidden();
    await expect(page.locator('#setupHeadline')).toHaveText('No alarm armed yet.');
    await expect(page.locator('#studyPoints')).toHaveText(String(studyBefore));
  });
});
