// Alarm sound with the Web Audio API. One AudioContext for the page's life:
// it is unlocked by the "Enable and test sound" click and reused when the alarm
// rings. The alarm is a pre-rendered 2 s buffer played with loop = true, so it
// needs no timers (background tabs throttle timers).

const SAMPLE_RATE = 44100;
let ctx = null;
let buffer = null;
let loopSource = null;

function pattern(context) {
  // Four short two-tone beeps, then a pause; 2 s in total.
  const length = 2 * SAMPLE_RATE;
  const buf = context.createBuffer(1, length, SAMPLE_RATE);
  const data = buf.getChannelData(0);
  const beep = 0.12;
  const gap = 0.08;
  for (let i = 0; i < 4; i++) {
    const start = i * (beep + gap);
    const freq = i % 2 === 0 ? 880 : 1175;
    for (let n = 0; n < beep * SAMPLE_RATE; n++) {
      const t = n / SAMPLE_RATE;
      const envelope = Math.min(1, t / 0.005, (beep - t) / 0.01);
      data[Math.floor(start * SAMPLE_RATE) + n] = 0.6 * envelope * Math.sin(2 * Math.PI * freq * t);
    }
  }
  return buf;
}

// resume() can stay pending forever when the browser refuses it, so give up after 1 s.
async function ensureRunning() {
  if (!ctx) ctx = new AudioContext();
  if (ctx.state !== 'running') {
    await Promise.race([ctx.resume().catch(() => {}), new Promise((r) => setTimeout(r, 1000))]);
  }
  if (ctx.state !== 'running') {
    const err = new Error(`The browser kept audio ${ctx.state}.`);
    err.name = 'NotAllowedError';
    throw err;
  }
  if (!buffer) buffer = pattern(ctx);
}

export async function testSound() {
  await ensureRunning();
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  src.connect(ctx.destination);
  src.start(0, 0, 0.8);
}

export async function startLoop() {
  await ensureRunning();
  if (loopSource) return;
  loopSource = ctx.createBufferSource();
  loopSource.buffer = buffer;
  loopSource.loop = true;
  loopSource.connect(ctx.destination);
  loopSource.start();
}

export function stopLoop() {
  if (!loopSource) return;
  try {
    loopSource.stop();
  } catch {
    // already stopped
  }
  loopSource.disconnect();
  loopSource = null;
}

export function isPlaying() {
  return loopSource !== null && ctx !== null && ctx.state === 'running';
}

export function isUnlocked() {
  return ctx !== null && ctx.state === 'running';
}
