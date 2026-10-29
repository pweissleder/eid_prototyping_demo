import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

/* Shared helpers for the WakeQuest end-to-end suite.
 * The service under test runs with WAKEQUEST_DEVICE=fake and WAKEQUEST_TEST_MODE=1
 * (see playwright.config.ts), so the /api/_test/* hooks are available. */

const pad = (n: number) => String(n).padStart(2, '0');

/** `YYYY-MM-DDTHH:MM` in local time, `minutes` from now (negative = past). */
export function localTimeIn(minutes: number): string {
  const d = new Date(Date.now() + minutes * 60_000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** POST that tolerates the listed statuses (default: any 2xx plus 409). */
async function post(request: APIRequestContext, path: string, data?: unknown, okStatuses: number[] = [409]) {
  const res = await request.post(path, data === undefined ? {} : { data });
  if (!res.ok() && !okStatuses.includes(res.status())) {
    throw new Error(`POST ${path} failed: ${res.status()} ${await res.text()}`);
  }
  return res;
}

export async function getState(request: APIRequestContext) {
  const res = await request.get('/api/state');
  expect(res.ok()).toBeTruthy();
  return res.json();
}

export async function getSessions(request: APIRequestContext, mode?: 'study' | 'demo') {
  const res = await request.get(`/api/sessions${mode ? `?mode=${mode}` : ''}`);
  expect(res.ok()).toBeTruthy();
  return (await res.json()).sessions as Array<Record<string, any>>;
}

export async function fakeDevice(request: APIRequestContext) {
  const res = await request.get('/api/_test/fake-device');
  expect(res.ok()).toBeTruthy();
  return res.json();
}

export type FakeAction =
  | 'tap'
  | 'drop'
  | 'power-off'
  | 'power-on'
  | 'no-ack'
  | 'sensor-stable'
  | 'sensor-unstable';

export async function fake(request: APIRequestContext, action: FakeAction, body: unknown = {}) {
  return post(request, `/api/_test/fake-device/${action}`, body, []);
}

/** Make every test independent of the one before it. */
export async function resetService(request: APIRequestContext) {
  await post(request, '/api/demo/reset'); // 409 study_run_active is tolerated
  await post(request, '/api/session/abort'); // 409 nothing_to_stop is tolerated
  await post(request, '/api/demo/reset');
  await post(request, '/api/alarms/disarm');
  const { alarms } = await (await request.get('/api/alarms')).json();
  for (const alarm of alarms) {
    await request.delete(`/api/alarms/${alarm.id}`);
  }
  // Bring the fake device back to a pristine state, then let the UI connect it again.
  await post(request, '/api/device/disconnect');
  await fake(request, 'power-on'); // no-op while already powered
  await fake(request, 'sensor-stable');
  await fake(request, 'no-ack', { enabled: false });
  await post(request, '/api/_test/test-alarm-delay', { seconds: 1 }, []);
  // The service has settled once nothing is running and the device link is down.
  await expect
    .poll(async () => {
      const s = await getState(request);
      return `${s.run.state}/${s.device.status}/${s.alarms.length}`;
    })
    .toMatch(/^(idle)\/disconnected\/0$/);
}

// ------------------------------------------------------------------ page helpers

/** Wait until the page has received a snapshot satisfying `predicate` (a function body over `s`). */
export async function waitForSnapshot(page: Page, predicate: string, timeout = 10_000) {
  await page.waitForFunction(
    (src) => {
      const s = (window as any).__wq?.snapshot;
      return !!s && new Function('s', `return (${src});`)(s);
    },
    predicate,
    { timeout },
  );
}

export async function openApp(page: Page) {
  await page.goto('/');
  await expect(page.locator('#setupHeadline')).toBeVisible();
  await page.waitForFunction(() => !!(window as any).__wq?.snapshot);
}

export async function connectDevice(page: Page) {
  await page.getByRole('button', { name: 'Connect device' }).click();
  await expect(page.locator('#deviceStatus')).toHaveText(/^Connected to wq-/);
}

export async function enableSound(page: Page) {
  await page.getByRole('button', { name: 'Enable and test sound' }).click();
  await expect(page.locator('#soundStatus')).toHaveText('Sound works');
  await waitForSnapshot(page, 's.audio.tested === true');
}

/** Open the page, connect the device, enable sound: ready to arm. */
export async function setupReady(page: Page) {
  await openApp(page);
  await connectDevice(page);
  await enableSound(page);
  await waitForSnapshot(page, 's.readiness.canArm === true');
}

/** Fill and submit the alarm form. Does not assert the outcome. */
export async function submitAlarm(page: Page, name: string, localTime: string) {
  await page.locator('#alarmName').fill(name);
  await page.locator('#alarmTime').fill(localTime);
  await page.getByRole('button', { name: 'Save alarm' }).click();
}

/** Create an alarm through the UI and wait until it is listed. */
export async function createAlarm(page: Page, name: string, localTime: string): Promise<Locator> {
  await submitAlarm(page, name, localTime);
  const item = alarmItem(page, name);
  await expect(item).toBeVisible();
  return item;
}

export function alarmItem(page: Page, name: string): Locator {
  return page.locator('#alarmList li.alarm').filter({ hasText: name });
}

/** Start a test alarm (delay 1 s unless changed) and wait for the ring view. */
export async function startTestAlarmAndRing(page: Page) {
  await page.getByRole('button', { name: 'Test alarm in 10 seconds' }).click();
  await expect(page.locator('#ringView')).toBeVisible({ timeout: 15_000 });
  await waitForSnapshot(page, "s.run.state === 'ringing'");
}

/** Wait until the fake device holds the ringing session (so a tap will be accepted). */
export async function waitForDeviceActive(request: APIRequestContext) {
  await expect
    .poll(async () => (await fakeDevice(request)).indicator, { timeout: 10_000 })
    .toBe(true);
}

/** Full path to a completed demo run: ring, tap, completion view. */
export async function ringAndTap(page: Page, request: APIRequestContext) {
  await startTestAlarmAndRing(page);
  await waitForDeviceActive(request);
  await expect
    .poll(async () => (await getState(request)).run.session?.activation?.status, { timeout: 10_000 })
    .toBe('acked');
  const res = await fake(request, 'tap');
  expect((await res.json()).sent).toBe(true);
  await expect(page.locator('#doneView')).toBeVisible();
}

export async function visibleButtonNames(page: Page, scope: string): Promise<string[]> {
  return page.locator(`${scope} button:visible`).allInnerTexts();
}
