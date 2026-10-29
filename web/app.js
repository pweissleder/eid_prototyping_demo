// WakeQuest website. Renders the service's snapshot (docs/api.md) and holds no
// business logic: every action is a REST call, every state comes back over /ws.

import { isPlaying, isUnlocked, startLoop, stopLoop, testSound } from './sound.js';

const $ = (id) => document.getElementById(id);

let snapshot = null;
let socket = null;
let socketRetry = 0;
let serverOffsetMs = 0; // serverTime - browser time
let audioTested = false; // this tab passed "Enable and test sound"
let soundAnnouncedFor = null; // sessionId we sent sound_started/sound_failed for
let wakeLock = null;

// Read-only view for the Playwright suite and debugging.
window.__wq = {
  get snapshot() { return snapshot; },
  get soundPlaying() { return isPlaying(); },
  get soundSessionId() { return soundAnnouncedFor; },
};

// ---------------------------------------------------------------- helpers

function serverNow() {
  return Date.now() + serverOffsetMs;
}

function fmtTime(iso) {
  return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function fmtDay(iso) {
  const d = new Date(iso);
  const today = new Date(serverNow());
  const tomorrow = new Date(today);
  tomorrow.setDate(today.getDate() + 1);
  const same = (a, b) => a.toDateString() === b.toDateString();
  if (same(d, today)) return 'today';
  if (same(d, tomorrow)) return 'tomorrow';
  return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' });
}

function fmtDuration(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m > 0 ? `${m} min ${String(s).padStart(2, '0')} s` : `${s} s`;
}

function setText(id, text) {
  const el = $(id);
  if (el.textContent !== text) el.textContent = text;
}

function announce(text) {
  setText('srStatus', text);
}

async function api(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.message || `The service refused the request (HTTP ${res.status}).`);
    err.data = data;
    throw err;
  }
  return data;
}

function showActionError(err) {
  setText('actionMsg', err.message);
}

async function act(fn) {
  setText('actionMsg', '');
  try {
    await fn();
  } catch (err) {
    showActionError(err);
  }
}

function send(msg) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(msg));
}

// ---------------------------------------------------------------- socket

function connectSocket() {
  socket = new WebSocket(`ws://${location.host}/ws`);
  socket.addEventListener('open', () => {
    socketRetry = 0;
    $('serviceBanner').hidden = true;
    // Audio readiness and sound confirmation are per connection on the service side.
    if (audioTested && isUnlocked()) send({ type: 'audio_test_ok' });
    soundAnnouncedFor = null;
    if (snapshot) syncSound(snapshot);
  });
  socket.addEventListener('message', (event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    if (msg.type === 'snapshot') {
      if (snapshot && msg.rev < snapshot.rev) return;
      onSnapshot(msg);
    } else if (msg.type === 'error' && msg.error !== 'stale_session') {
      setText('actionMsg', msg.message);
    }
  });
  socket.addEventListener('close', () => {
    const banner = $('serviceBanner');
    banner.textContent = 'This page lost contact with the WakeQuest service. Reconnecting… If an alarm is ringing, the sound keeps playing.';
    banner.hidden = false;
    const delay = Math.min(5000, 500 * 2 ** socketRetry++);
    setTimeout(connectSocket, delay);
  });
}

// ---------------------------------------------------------------- sound

async function announceSound(sessionId) {
  try {
    await startLoop();
    soundAnnouncedFor = sessionId;
    send({ type: 'sound_started', sessionId });
    $('startSoundBtn').hidden = true;
  } catch (err) {
    soundAnnouncedFor = sessionId;
    send({ type: 'sound_failed', sessionId, error: err.name || String(err) });
    $('startSoundBtn').hidden = false;
  }
}

function syncSound(s) {
  const run = s.run;
  if (run.state === 'ringing' && run.session) {
    const sid = run.session.sessionId;
    if (soundAnnouncedFor !== sid) {
      soundAnnouncedFor = sid; // set before the await so a second snapshot does not retry
      announceSound(sid);
    }
  } else {
    stopLoop();
    soundAnnouncedFor = null;
    $('startSoundBtn').hidden = true;
  }
}

// ---------------------------------------------------------------- render

function onSnapshot(s) {
  const previous = snapshot;
  snapshot = s;
  serverOffsetMs = Date.parse(s.serverTime) - Date.now();
  syncSound(s);
  render(s, previous);
}

function viewFor(state) {
  if (state === 'ringing') return 'ring';
  if (state === 'completed' || state === 'aborted') return 'done';
  return 'setup';
}

function render(s, previous) {
  const view = viewFor(s.run.state);
  const prevView = previous ? viewFor(previous.run.state) : null;
  document.body.dataset.view = view;
  $('setupView').hidden = view !== 'setup';
  $('ringView').hidden = view !== 'ring';
  $('doneView').hidden = view !== 'done';

  if (view === 'setup') renderSetup(s);
  if (view === 'ring') renderRing(s);
  if (view === 'done') renderDone(s, prevView !== 'done');

  if (view !== prevView) {
    const headline = { setup: $('setupHeadline'), ring: $('ringHeadline'), done: $('doneHeadline') }[view];
    announce(headline.textContent);
  }
  if ($('logPanel').open) refreshLog();
}

function renderSetup(s) {
  const { device, readiness, run, armedAlarm } = s;

  // Headline: what will happen next.
  let headline;
  let sub;
  if (run.state === 'test_pending' && run.testAlarm) {
    const left = Date.parse(run.testAlarm.fireAt) - serverNow();
    headline = `Test alarm rings in ${Math.max(0, Math.ceil(left / 1000))} s.`;
    sub = 'This is a demo run. Get ready to pick up the device and tap it.';
  } else if (armedAlarm) {
    headline = `Armed for ${fmtTime(armedAlarm.scheduledAt)} ${fmtDay(armedAlarm.scheduledAt)}.`;
    sub = `“${armedAlarm.name}” will ring then. The device light stays off until it rings.`;
  } else {
    headline = 'No alarm armed yet.';
    sub = readiness.canArm
      ? 'Everything is ready. Arm an alarm from the list.'
      : 'Work through the three steps below, then arm an alarm.';
  }
  setText('setupHeadline', headline);
  setText('setupSub', sub);

  // Step 1: device.
  const deviceText = {
    connected: `Connected to ${device.deviceId}`,
    connecting: 'Connecting…',
    reconnecting: 'Connection lost. Reconnecting…',
    disconnected: 'Not connected',
  }[device.status];
  setText('deviceStatus', deviceText);
  let deviceDetail = device.lastError || '';
  if (device.status === 'connected') {
    deviceDetail = device.sensorStable
      ? 'Tap sensor is resting and ready.'
      : 'Tap sensor is moving. Put the device down and keep it still for 2 seconds.';
  }
  setText('deviceDetail', deviceDetail);
  $('stepDevice').dataset.done = String(device.status === 'connected' && !!device.sensorStable);
  $('stepDevice').dataset.state = device.status;
  $('connectBtn').textContent = device.status === 'disconnected' && device.lastError ? 'Try again' : 'Connect device';
  $('connectBtn').hidden = device.status === 'connected';
  $('connectBtn').disabled = device.status === 'connecting';

  // Step 2: sound (per tab).
  const tested = s.audio.tested && audioTested;
  setText('soundStatus', tested ? 'Sound works' : 'Not tested');
  $('stepSound').dataset.done = String(tested);
  $('soundBtn').textContent = tested ? 'Test sound again' : 'Enable and test sound';

  // Step 3: arm.
  setText('armStatus', armedAlarm ? `“${armedAlarm.name}” is armed` : 'No alarm armed');
  const missing = readiness.missing.map((m) => m.message).join(' ');
  setText('armDetail', armedAlarm
    ? (s.lastArmChange && s.lastArmChange.armedAlarmId === armedAlarm.id ? s.lastArmChange.message : '')
    : (readiness.canArm ? 'Pick an alarm in the list and press “Arm alarm”.' : missing));
  $('stepArm').dataset.done = String(!!armedAlarm);

  renderAlarms(s);

  // Presenter.
  const pending = run.state === 'test_pending';
  $('testBtn').disabled = pending || !readiness.canArm;
  $('cancelTestBtn').hidden = !pending;
  setText('presenterMsg', pending
    ? 'Test alarm counting down. It uses the same sound, light, tap and reward as a real alarm, and its points are only a preview.'
    : (readiness.canArm ? 'The test alarm never changes the armed alarm or the study points.' : `Test alarm needs the same setup: ${missing}`));
  setText('studyPoints', String(s.points.study));
}

function renderAlarms(s) {
  const list = $('alarmList');
  const now = serverNow();
  const canArm = s.readiness.canArm && s.run.state !== 'ringing';
  $('alarmEmpty').hidden = s.alarms.length > 0;
  list.replaceChildren(...s.alarms.map((alarm) => {
    const li = document.createElement('li');
    li.className = 'alarm';
    li.dataset.status = alarm.status;
    li.dataset.alarmId = alarm.id;

    const info = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = alarm.name;
    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = `${fmtTime(alarm.scheduledAt)} ${fmtDay(alarm.scheduledAt)}`;
    const status = document.createElement('span');
    status.className = 'status';
    const past = Date.parse(alarm.scheduledAt) <= now;
    status.textContent = {
      armed: 'Armed',
      rang: 'Rang',
      missed: 'Missed: the service was not running',
      saved: past ? 'Time has passed' : 'Saved',
    }[alarm.status];
    info.append(name, when, status);

    const actions = document.createElement('div');
    actions.className = 'alarm-actions';
    if (alarm.status === 'armed') {
      actions.append(button('Disarm', () => act(() => api('POST', '/api/alarms/disarm')), 'quiet'));
    } else if (alarm.status === 'saved' && !past) {
      const arm = button('Arm alarm', () => act(() => api('POST', `/api/alarms/${alarm.id}/arm`)));
      arm.disabled = !canArm;
      actions.append(arm);
    }
    actions.append(button('Delete', () => act(() => api('DELETE', `/api/alarms/${alarm.id}`)), 'quiet'));
    li.append(info, actions);
    return li;
  }));
}

function button(label, onClick, cls) {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  if (cls) b.className = cls;
  b.addEventListener('click', onClick);
  return b;
}

function renderRing(s) {
  const session = s.run.session;
  const demo = session.mode === 'demo';
  setText('ringLabel', demo ? 'Test alarm (demo run)' : session.alarmName);
  document.body.dataset.mode = session.mode;

  const since = session.sound.startedAt || session.ringStartedAt;
  setText('ringElapsed', `Ringing for ${fmtDuration(serverNow() - Date.parse(since))}`);

  const issues = [];
  if (s.device.status !== 'connected') {
    issues.push('The device is out of contact. Reconnecting… The alarm keeps ringing until the tap gets through.');
  }
  if (session.activation.status === 'missing') {
    issues.push('The device has not confirmed its light yet. Taps count as soon as it does.');
  }
  // After a reload the service may still say "started" from before, so also go by this tab.
  if (!isPlaying() && (session.sound.status === 'failed' || !$('startSoundBtn').hidden)) {
    issues.push('The browser blocked the alarm sound. Press “Start the alarm sound”.');
  }
  if (s.device.indicatorTimedOut) {
    issues.push('The device light switched itself off after 15 minutes. Tapping still stops the alarm.');
  }
  const list = $('ringIssues');
  list.replaceChildren(...issues.map((text) => {
    const li = document.createElement('li');
    li.textContent = text;
    return li;
  }));
}

let sunDrawn = false;

function drawSun(points) {
  const svg = $('sun');
  const ns = 'http://www.w3.org/2000/svg';
  svg.replaceChildren();
  for (let i = 0; i < points; i++) {
    const ray = document.createElementNS(ns, 'rect');
    ray.setAttribute('class', 'ray');
    ray.setAttribute('x', '-4');
    ray.setAttribute('y', '-92');
    ray.setAttribute('width', '8');
    ray.setAttribute('height', '30');
    ray.setAttribute('rx', '4');
    ray.setAttribute('transform', `rotate(${(360 / points) * i})`);
    ray.style.setProperty('--i', String(i));
    svg.append(ray);
  }
  const disc = document.createElementNS(ns, 'circle');
  disc.setAttribute('class', 'disc');
  disc.setAttribute('r', '50');
  svg.append(disc);
}

function renderDone(s, entering) {
  const session = s.run.session;
  const result = session.result || { points: 0 };
  const completed = s.run.state === 'completed';
  const demo = session.mode === 'demo';
  document.body.dataset.mode = session.mode;
  document.body.dataset.outcome = s.run.state;

  if (entering || !sunDrawn) {
    drawSun(completed ? 10 : 0);
    sunDrawn = true;
  }

  $('doneLabel').hidden = !demo;
  if (completed) {
    setText('doneHeadline', 'Morning quest completed!');
    setText('donePoints', demo
      ? '+10 points, demo preview. Not added to your study points.'
      : `+${result.points} points. Study total: ${s.points.study}.`);
    setText('doneTime', result.responseTimeMs == null
      ? 'Alarm response time: unavailable, because the alarm sound was never confirmed.'
      : `Alarm response time: ${fmtDuration(result.responseTimeMs)}`);
    setText('doneNote', 'Measured from the alarm sound starting to your tap. It is not the time you got out of bed.');
  } else {
    setText('doneHeadline', 'Run stopped.');
    setText('donePoints', 'No points for this run.');
    const why = {
      manual: 'The presenter stopped it.',
      reset: 'The demo was reset.',
      preempted_by_study_alarm: 'A real alarm took over from the demo.',
      service_shutdown: 'The service shut down while it was ringing.',
    }[session.abortReason] || '';
    setText('doneTime', why);
    setText('doneNote', '');
  }
}

async function refreshLog() {
  try {
    const { sessions } = await api('GET', '/api/sessions');
    const rows = sessions.slice().reverse().map((r) => {
      const tr = document.createElement('tr');
      const outcome = {
        successful_tap: 'Completed by tap',
        manual_abort: 'Stopped (aborted)',
        technical_failure: 'Technical failure',
        incomplete: 'Not finished',
      }[r.outcome] || r.outcome;
      const cells = [
        r.ringStartedAt ? `${fmtTime(r.ringStartedAt)} ${fmtDay(r.ringStartedAt)}` : '—',
        r.mode === 'demo' ? 'Test alarm (demo)' : r.alarmName,
        outcome,
        r.responseTimeMs == null || r.responseTimeMs === '' ? '—' : fmtDuration(Number(r.responseTimeMs)),
        r.mode === 'demo' ? 'preview' : String(r.points ?? 0),
      ];
      tr.append(...cells.map((c) => {
        const td = document.createElement('td');
        td.textContent = c;
        return td;
      }));
      return tr;
    });
    $('sessionsTable').tBodies[0].replaceChildren(...rows);
  } catch {
    // The log is optional; the next snapshot retries.
  }
}

// ---------------------------------------------------------------- actions

function defaultAlarmTime() {
  const d = new Date(serverNow());
  d.setDate(d.getDate() + 1);
  d.setHours(7, 0, 0, 0);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T07:00`;
}

$('connectBtn').addEventListener('click', () => act(() => api('POST', '/api/device/connect')));

$('soundBtn').addEventListener('click', async () => {
  setText('actionMsg', '');
  try {
    await testSound();
    audioTested = true;
    send({ type: 'audio_test_ok' });
    setText('soundDetail', 'If you did not hear four beeps, turn the volume up and test again.');
  } catch (err) {
    audioTested = false;
    send({ type: 'audio_test_failed', error: err.name || String(err) });
    setText('soundDetail', `The browser did not play the sound (${err.message}). Check the volume and that this tab may play audio, then test again.`);
  }
  if (snapshot) renderSetup(snapshot);
});

$('alarmForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  setText('formError', '');
  try {
    await api('POST', '/api/alarms', {
      name: $('alarmName').value,
      localTime: $('alarmTime').value,
    });
    $('alarmName').value = '';
  } catch (err) {
    setText('formError', err.message);
    const field = err.data && err.data.field;
    if (field === 'name') $('alarmName').focus();
    if (field === 'localTime') $('alarmTime').focus();
  }
});

$('testBtn').addEventListener('click', () => act(() => api('POST', '/api/test-alarm')));
$('cancelTestBtn').addEventListener('click', () => act(() => api('POST', '/api/session/abort')));
$('resetBtn').addEventListener('click', () => act(() => api('POST', '/api/demo/reset')));
$('ringAbortBtn').addEventListener('click', () => act(() => api('POST', '/api/session/abort')));
$('doneBtn').addEventListener('click', () => act(() => api('POST', '/api/demo/reset')));
$('startSoundBtn').addEventListener('click', () => {
  const session = snapshot && snapshot.run.session;
  if (session) announceSound(session.sessionId);
});
$('logPanel').addEventListener('toggle', () => {
  if ($('logPanel').open) refreshLog();
});

// Keep the screen on while the page is visible (released automatically when hidden).
async function holdWakeLock() {
  if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
  try {
    wakeLock = await navigator.wakeLock.request('screen');
  } catch {
    wakeLock = null;
  }
}
document.addEventListener('visibilitychange', holdWakeLock);

// Countdown and elapsed time tick between snapshots.
setInterval(() => {
  if (!snapshot) return;
  const state = snapshot.run.state;
  if (state === 'ringing') renderRing(snapshot);
  if (state === 'test_pending') renderSetup(snapshot);
}, 500);

$('alarmTime').value = defaultAlarmTime();
holdWakeLock();
connectSocket();
