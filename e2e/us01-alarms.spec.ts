import { expect, test } from '@playwright/test';
import {
  alarmItem,
  createAlarm,
  localTimeIn,
  openApp,
  resetService,
  setupReady,
  submitAlarm,
  waitForSnapshot,
} from './helpers';

// US-01 / FR-01 / FR-03: create, validate, arm (one at a time), disarm, delete.

test.beforeEach(async ({ request }) => {
  await resetService(request);
});

test.describe('US-01 alarms', () => {
  test('a valid alarm appears in the list with its time and "Saved"', async ({ page, request }) => {
    await openApp(page);
    await expect(page.locator('#alarmEmpty')).toBeVisible();
    const when = localTimeIn(120);
    const item = await createAlarm(page, 'Gym morning', when);
    await expect(item).toContainText(when.slice(11, 16)); // HH:MM, 24-hour (locale en-GB)
    await expect(item.locator('.status')).toHaveText('Saved');
    await expect(page.locator('#formError')).toHaveText('');
    await expect(page.locator('#alarmEmpty')).toBeHidden();
    const { alarms } = await (await request.get('/api/alarms')).json();
    expect(alarms).toHaveLength(1);
    expect(alarms[0]).toMatchObject({ name: 'Gym morning', localTime: when, status: 'saved' });
  });

  test('missing name, missing time and a past time are refused with an understandable message', async ({
    page,
    request,
  }) => {
    await openApp(page);

    await submitAlarm(page, '', localTimeIn(120));
    await expect(page.locator('#formError')).toHaveText('Please give the alarm a name.');

    await submitAlarm(page, 'No time', '');
    await expect(page.locator('#formError')).toHaveText('Please choose a date and time for the alarm.');

    await submitAlarm(page, 'Yesterday', localTimeIn(-120));
    await expect(page.locator('#formError')).toContainText('That time is in the past');
    await expect(page.locator('#formError')).toContainText('Choose a future date and time.');

    // Nothing was saved, neither in the UI nor in the service.
    await expect(page.locator('#alarmList li')).toHaveCount(0);
    await expect(page.locator('#alarmEmpty')).toBeVisible();
    const { alarms } = await (await request.get('/api/alarms')).json();
    expect(alarms).toHaveLength(0);

    // A later valid save clears the error.
    await submitAlarm(page, 'Fixed', localTimeIn(180));
    await expect(alarmItem(page, 'Fixed')).toBeVisible();
    await expect(page.locator('#formError')).toHaveText('');
  });

  test('arming a second alarm replaces the first; disarm and delete work', async ({ page, request }) => {
    await setupReady(page);
    const first = await createAlarm(page, 'First alarm', localTimeIn(120));
    const second = await createAlarm(page, 'Second alarm', localTimeIn(180));

    await first.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(first.locator('.status')).toHaveText('Armed');
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for /);
    await expect(page.locator('#armStatus')).toHaveText('“First alarm” is armed');
    await expect(second.locator('.status')).toHaveText('Saved');

    await second.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(second.locator('.status')).toHaveText('Armed');
    await expect(first.locator('.status')).toHaveText('Saved');
    await expect(page.locator('#armStatus')).toHaveText('“Second alarm” is armed');
    await expect(page.locator('#armDetail')).toContainText('it replaces “First alarm”');
    // Only one alarm is armed in the service.
    const { alarms, armedAlarmId } = await (await request.get('/api/alarms')).json();
    expect(alarms.filter((a: any) => a.status === 'armed')).toHaveLength(1);
    expect(armedAlarmId).toBe(alarms.find((a: any) => a.name === 'Second alarm').id);

    // Disarm.
    await second.getByRole('button', { name: 'Disarm' }).click();
    await expect(second.locator('.status')).toHaveText('Saved');
    await expect(page.locator('#setupHeadline')).toHaveText('No alarm armed yet.');
    await expect(page.locator('#armStatus')).toHaveText('No alarm armed');
    await waitForSnapshot(page, 's.armedAlarm === null');

    // Delete.
    await first.getByRole('button', { name: 'Delete' }).click();
    await expect(alarmItem(page, 'First alarm')).toHaveCount(0);
    await second.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('#alarmList li')).toHaveCount(0);
    await expect(page.locator('#alarmEmpty')).toBeVisible();
  });

  test('deleting the armed alarm disarms it', async ({ page }) => {
    await setupReady(page);
    const item = await createAlarm(page, 'Doomed', localTimeIn(120));
    await item.getByRole('button', { name: 'Arm alarm' }).click();
    await expect(page.locator('#setupHeadline')).toHaveText(/^Armed for /);
    await item.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('#alarmList li')).toHaveCount(0);
    await expect(page.locator('#setupHeadline')).toHaveText('No alarm armed yet.');
  });
});
