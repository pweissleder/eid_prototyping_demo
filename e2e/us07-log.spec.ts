import { expect, test } from '@playwright/test';
import { getSessions, getState, resetService, ringAndTap, setupReady, startTestAlarmAndRing } from './helpers';

// US-07 / FR-07: the run log and the CSV export, with demo runs kept apart from study results.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-07 log', () => {
  test('completed and aborted demo runs appear in the table and in the CSV; study points stay 0', async ({
    page,
    request,
  }) => {
    await setupReady(page);
    const before = await getSessions(request);

    // Run 1: completed by tap.
    await ringAndTap(page, request);
    await page.getByRole('button', { name: 'Back to setup' }).click();
    await expect(page.locator('#setupView')).toBeVisible();

    // Run 2: aborted by the presenter.
    await startTestAlarmAndRing(page);
    await page.getByRole('button', { name: 'Presenter: stop run (aborted)' }).click();
    await expect(page.locator('#doneHeadline')).toHaveText('Run stopped.');
    await page.getByRole('button', { name: 'Back to setup' }).click();
    await expect(page.locator('#setupView')).toBeVisible();

    // Table in the page.
    await page.locator('#logPanel > summary').click();
    await expect(page.locator('#logPanel')).toHaveJSProperty('open', true);
    const rows = page.locator('#sessionsTable tbody tr');
    await expect(rows).toHaveCount(before.length + 2);
    const newest = rows.nth(0); // newest first
    const older = rows.nth(1);
    await expect(newest).toContainText('Test alarm (demo)');
    await expect(newest).toContainText('Stopped (aborted)');
    await expect(older).toContainText('Completed by tap');
    await expect(older.locator('td').nth(3)).toHaveText(/^\d+( min \d{2})? s$/); // response time
    await expect(older.locator('td').nth(4)).toHaveText('preview'); // demo points are only a preview
    await expect(page.locator('#sessionsTable thead')).toContainText('Alarm response time');

    // CSV export.
    const res = await request.get('/api/sessions.csv');
    expect(res.ok()).toBeTruthy();
    expect(res.headers()['content-type']).toContain('text/csv');
    const csv = await res.text();
    const [header, ...lines] = csv.trim().split(/\r?\n/);
    expect(header.split(',')).toEqual(expect.arrayContaining(['sessionId', 'mode', 'outcome', 'responseTimeMs', 'points']));
    expect(lines).toHaveLength(before.length + 2);
    expect(csv).toContain('successful_tap');
    expect(csv).toContain('manual_abort');
    // Both new runs are demo runs.
    const newRows = lines.slice(-2);
    for (const line of newRows) expect(line).toContain('classroom_demo');

    // Study points stay 0; the study export has no demo runs.
    await expect(page.locator('#studyPoints')).toHaveText('0');
    expect((await getState(request)).points.study).toBe(0);
    const study = await request.get('/api/sessions.csv?mode=study');
    expect(study.ok()).toBeTruthy();
    expect(study.headers()['content-type']).toContain('text/csv');
    expect((await study.text()).trim().split(/\r?\n/).slice(1)).toHaveLength(0);
    expect((await getSessions(request, 'study'))).toHaveLength(0);
  });

  test('the events CSV is offered as a download link', async ({ page, request }) => {
    await setupReady(page);
    await page.locator('#logPanel > summary').click();
    await expect(page.getByRole('link', { name: 'runs (CSV)' })).toHaveAttribute('href', '/api/sessions.csv');
    await expect(page.getByRole('link', { name: 'all events (CSV)' })).toHaveAttribute('href', '/api/events.csv');
    const res = await request.get('/api/events.csv');
    expect(res.ok()).toBeTruthy();
    expect((await res.text()).split(/\r?\n/)[0]).toContain('ts,event,sessionId,mode,runType,category');
  });
});
