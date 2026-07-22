// Playwright MCP sidecar for the job-finder apply agent.
//
// The FastAPI Cloud backend has no Node/Chromium, so the apply agent connects to
// this service instead of spawning `npx @playwright/mcp` locally. This process:
//   1. launches Chromium itself with a CDP debug port (so we OWN the browser and
//      can attach a second CDP client for the live screencast),
//   2. spawns @playwright/mcp in streamable-HTTP mode pointed at that browser via
//      --cdp-endpoint (--isolated: every MCP session gets its own context, so
//      several apply runs share one sidecar), and
//   3. fronts both with a tiny proxy that enforces a bearer token, accepts resume
//      uploads (Playwright MCP can only browser_file_upload files under --output-dir),
//      answers /healthz for availability probes, and exposes GET /screencast — a live
//      MJPEG stream of the browser (Chrome DevTools Page.startScreencast) so the
//      dashboard can show what the agent is doing in real time.
//
// Env: PLAYWRIGHT_MCP_TOKEN (required — requests without it are rejected),
//      PORT (public port, default 8080), MCP_PORT (internal, default 8931),
//      CDP_PORT (internal Chromium debug port, default 9222), DATA_DIR (default /data/runs).

import http from 'node:http';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, readdirSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';
import { randomUUID } from 'node:crypto';
import { WebSocket } from 'ws';
import { chromium } from 'playwright-core';

const PORT = Number(process.env.PORT || 8080);
const MCP_PORT = Number(process.env.MCP_PORT || 8931);
const CDP_PORT = Number(process.env.CDP_PORT || 9222);
const DATA_DIR = process.env.DATA_DIR || '/data/runs';
const TOKEN = (process.env.PLAYWRIGHT_MCP_TOKEN || '').trim();
const UPLOAD_MAX_BYTES = 10 * 1024 * 1024;
const UPLOAD_TTL_MS = 2 * 60 * 60 * 1000; // uploads older than 2h are swept

// Screencast tuning: JPEG frames are cheap enough at this size/quality for the
// default 3-way apply concurrency on a 2 vCPU / 2 GiB sidecar.
const SC_FORMAT = 'jpeg';
const SC_QUALITY = Number(process.env.SC_QUALITY || 50);
const SC_MAX_WIDTH = Number(process.env.SC_MAX_WIDTH || 1024);
const SC_MAX_HEIGHT = Number(process.env.SC_MAX_HEIGHT || 768);

if (!TOKEN) {
  console.error('PLAYWRIGHT_MCP_TOKEN is required — refusing to start an open browser service.');
  process.exit(1);
}
mkdirSync(join(DATA_DIR, 'uploads'), { recursive: true });

// ── 1. Chromium with a CDP debug port (we own it; MCP connects over CDP) ─────
const chromePath = chromium.executablePath();
const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    '--no-sandbox',
    '--disable-gpu',
    '--disable-dev-shm-usage',
    '--remote-debugging-address=127.0.0.1',
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${join(DATA_DIR, 'cdp-profile')}`,
    'about:blank',
  ],
  { stdio: 'inherit' },
);
chrome.on('exit', (code) => {
  console.error(`chromium exited with code ${code} — shutting down.`);
  process.exit(code ?? 1);
});

// ── 2. Playwright MCP, pointed at our browser over CDP ──────────────────────
// Run the copy of @playwright/mcp installed into the image at build time — NOT
// `npx @playwright/mcp@latest`, which resolves "latest" at runtime and can fetch
// a version newer than the baked Chromium.
const MCP_CLI = process.env.MCP_CLI || '/app/node_modules/@playwright/mcp/cli.js';
let mcp = null;
function startMcp() {
  mcp = spawn(
    process.execPath,
    [
      MCP_CLI,
      '--isolated',
      '--cdp-endpoint', `http://127.0.0.1:${CDP_PORT}`,
      '--host', '127.0.0.1', '--port', String(MCP_PORT),
      '--output-dir', DATA_DIR,
    ],
    { stdio: 'inherit' },
  );
  mcp.on('exit', (code) => {
    console.error(`@playwright/mcp exited with code ${code} — shutting down.`);
    process.exit(code ?? 1);
  });
}
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { try { mcp?.kill(sig); } catch {} try { chrome.kill(sig); } catch {} process.exit(0); });
}

const authorized = (req) =>
  (req.headers.authorization || '') === `Bearer ${TOKEN}`;

// Periodically clean up old upload dirs so the disk doesn't fill.
setInterval(() => {
  const root = join(DATA_DIR, 'uploads');
  let entries = [];
  try { entries = readdirSync(root); } catch { return; }
  for (const name of entries) {
    const dir = join(root, name);
    try {
      if (Date.now() - statSync(dir).mtimeMs > UPLOAD_TTL_MS) rmSync(dir, { recursive: true, force: true });
    } catch { /* raced with another sweep — ignore */ }
  }
}, 15 * 60 * 1000).unref();

// ── 3. CDP screencast bridge ────────────────────────────────────────────────
// One CDP WebSocket to the browser; auto-attach to every page target and run
// Page.startScreencast on each. Latest JPEG frame per target is fanned out to
// GET /screencast subscribers, matched to a run by the page URL.
const targets = new Map();      // cdpSessionId → { targetId, url, frame: Buffer|null }
const subscribers = new Set();  // { res, match, sessionId | null }
let cdpMsgId = 0;
const cdpPending = new Map();
let cdpWs = null;

function cdpSend(method, params = {}, sessionId = undefined) {
  if (!cdpWs || cdpWs.readyState !== WebSocket.OPEN) return Promise.reject(new Error('cdp closed'));
  const id = ++cdpMsgId;
  const msg = { id, method, params };
  if (sessionId) msg.sessionId = sessionId;
  cdpWs.send(JSON.stringify(msg));
  return new Promise((resolve, reject) => {
    cdpPending.set(id, { resolve, reject });
    setTimeout(() => { if (cdpPending.delete(id)) reject(new Error(`cdp ${method} timeout`)); }, 15000);
  });
}

function pickTargetForMatch(match) {
  // Prefer a page whose URL contains `match`; newest such wins. Else newest page.
  let best = null;
  for (const [sid, t] of targets) {
    if (!t.url || t.url === 'about:blank') continue;
    if (match && !t.url.includes(match)) continue;
    best = sid; // Map preserves insertion order; last matching = most recent
  }
  if (best || match) return best;
  // No match filter and nothing non-blank: fall back to any page.
  for (const [sid] of targets) best = sid;
  return best;
}

function writeFrame(res, jpeg) {
  res.write(`--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.length}\r\n\r\n`);
  res.write(jpeg);
  res.write('\r\n');
}

async function startScreencastOn(sessionId) {
  try {
    await cdpSend('Page.enable', {}, sessionId);
    await cdpSend('Page.startScreencast', {
      format: SC_FORMAT, quality: SC_QUALITY, maxWidth: SC_MAX_WIDTH, maxHeight: SC_MAX_HEIGHT, everyNthFrame: 1,
    }, sessionId);
  } catch (e) { /* page not active yet — activateAndStart retries when a viewer locks it */ }
}

// Chromium only screencasts the ACTIVE tab; with several targets (e.g. the agent
// opened a new tab, or concurrent runs share the browser) a background page's
// startScreencast fails with "Not attached to an active page". When a viewer locks
// onto a target we bring it to the front and (re)start its screencast so the run the
// user is watching always streams. (Concurrent viewers of DIFFERENT pages contend on
// the single active tab — a documented limitation of one shared browser.)
async function activateAndStart(sessionId) {
  const t = targets.get(sessionId);
  if (!t) return;
  try { await cdpSend('Target.activateTarget', { targetId: t.targetId }); } catch {}
  await startScreencastOn(sessionId);
}

function onCdpMessage(raw) {
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }
  if (msg.id && cdpPending.has(msg.id)) {
    const { resolve, reject } = cdpPending.get(msg.id);
    cdpPending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    return;
  }
  const { method, params, sessionId } = msg;
  if (method === 'Target.attachedToTarget') {
    const info = params.targetInfo || {};
    if (info.type === 'page') {
      targets.set(params.sessionId, { targetId: info.targetId, url: info.url || '', frame: null });
      startScreencastOn(params.sessionId);
    }
  } else if (method === 'Target.detachedFromTarget') {
    const sid = params.sessionId;
    for (const sub of subscribers) if (sub.sessionId === sid) { try { sub.res.end(); } catch {} subscribers.delete(sub); }
    targets.delete(sid);
  } else if (method === 'Target.targetInfoChanged') {
    const info = params.targetInfo || {};
    for (const [sid, t] of targets) {
      if (t.targetId !== info.targetId) continue;
      t.url = info.url || t.url;
      // A waiting subscriber's match may only become satisfiable now (the page just
      // navigated to the job host) — lock it on and activate the screencast.
      for (const sub of subscribers) {
        if (sub.sessionId === null && sub.match && t.url.includes(sub.match)) {
          sub.sessionId = sid;
          activateAndStart(sid);
        }
      }
    }
  } else if (method === 'Page.screencastFrame' && sessionId) {
    const t = targets.get(sessionId);
    if (t) {
      t.frame = Buffer.from(params.data, 'base64');
      for (const sub of subscribers) {
        if (sub.sessionId === null) {
          const chosen = pickTargetForMatch(sub.match);
          if (chosen === sessionId) sub.sessionId = sessionId; else continue;
        }
        if (sub.sessionId === sessionId) { try { writeFrame(sub.res, t.frame); } catch {} }
      }
    }
    cdpSend('Page.screencastFrameAck', { sessionId: params.sessionId }, sessionId).catch(() => {});
  }
}

async function connectCdpBridge() {
  const version = await new Promise((resolve, reject) => {
    const r = http.get({ host: '127.0.0.1', port: CDP_PORT, path: '/json/version' }, (resp) => {
      let body = ''; resp.on('data', (c) => (body += c)); resp.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(e); } });
    });
    r.on('error', reject);
  });
  cdpWs = new WebSocket(version.webSocketDebuggerUrl, { maxPayload: 64 * 1024 * 1024 });
  cdpWs.on('open', async () => {
    await cdpSend('Target.setDiscoverTargets', { discover: true });
    await cdpSend('Target.setAutoAttach', { autoAttach: true, waitForDebuggerOnStart: false, flatten: true });
  });
  cdpWs.on('message', onCdpMessage);
  cdpWs.on('close', () => { console.error('CDP bridge closed — reconnecting in 1s'); targets.clear(); setTimeout(connectCdpBridge, 1000); });
  cdpWs.on('error', (e) => { console.error('CDP bridge error:', e.message); });
}

// ── HTTP front ──────────────────────────────────────────────────────────────
const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  // Google's front end intercepts /healthz on *.run.app and answers its own 404,
  // so /health is the alias that actually works on Cloud Run.
  if (url.pathname === '/healthz' || url.pathname === '/health') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"ok": true}');
    return;
  }
  if (!authorized(req)) {
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end('{"error": "unauthorized"}');
    return;
  }

  // Live browser screencast (MJPEG). ?match=<substr> locks the stream to the
  // page target whose URL contains the substring (the run's job host).
  if (url.pathname === '/screencast' && req.method === 'GET') {
    const match = url.searchParams.get('match') || '';
    res.writeHead(200, {
      'content-type': 'multipart/x-mixed-replace; boundary=frame',
      'cache-control': 'no-cache, no-store',
      'connection': 'keep-alive',
      'x-accel-buffering': 'no',
    });
    const initial = pickTargetForMatch(match);
    const sub = { res, match, sessionId: initial };
    subscribers.add(sub);
    if (initial) {
      if (targets.get(initial)?.frame) { try { writeFrame(res, targets.get(initial).frame); } catch {} }
      activateAndStart(initial); // active-tab-only screencast: bring the watched page to front
    }
    req.on('close', () => subscribers.delete(sub));
    return;
  }

  // Resume upload: raw body + x-filename header → a path browser_file_upload
  // is allowed to read (under the MCP --output-dir).
  if (url.pathname === '/upload' && req.method === 'POST') {
    const chunks = [];
    let size = 0;
    req.on('data', (c) => {
      size += c.length;
      if (size > UPLOAD_MAX_BYTES) { req.destroy(); return; }
      chunks.push(c);
    });
    req.on('end', () => {
      const safeName = (basename(req.headers['x-filename'] || 'resume.pdf') || 'resume.pdf')
        .replace(/[^A-Za-z0-9._-]/g, '_');
      const dir = join(DATA_DIR, 'uploads', randomUUID());
      mkdirSync(dir, { recursive: true });
      const path = join(dir, safeName);
      writeFileSync(path, Buffer.concat(chunks));
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ path, dir }));
    });
    req.on('error', () => {
      res.writeHead(400, { 'content-type': 'application/json' });
      res.end('{"error": "upload failed"}');
    });
    return;
  }

  // Everything else (/mcp — both POST messages and the SSE stream) proxies to
  // the internal Playwright MCP server, preserving streaming.
  // Host header must read `localhost` — @playwright/mcp's DNS-rebinding guard
  // rejects anything else (including 127.0.0.1).
  const upstream = http.request(
    { host: '127.0.0.1', port: MCP_PORT, path: req.url, method: req.method, headers: { ...req.headers, host: `localhost:${MCP_PORT}` } },
    (up) => {
      res.writeHead(up.statusCode, up.headers);
      up.pipe(res);
    },
  );
  upstream.on('error', (err) => {
    console.error('proxy error:', err.message);
    if (!res.headersSent) res.writeHead(502, { 'content-type': 'application/json' });
    res.end('{"error": "playwright-mcp unavailable"}');
  });
  req.pipe(upstream);
});

// ── Boot sequence (all inside Cloud Run's unthrottled startup window) ─────────
// Cloud Run only guarantees full CPU until the container starts listening on
// $PORT; after that CPU is throttled between requests. So bring Chromium, the
// CDP bridge, and MCP all up BEFORE opening the public port.
async function waitForPort(port, label) {
  for (let waited = 0; ; waited += 500) {
    const ready = await new Promise((resolve) => {
      const probe = net.connect({ host: '127.0.0.1', port });
      probe.once('connect', () => { probe.destroy(); resolve(true); });
      probe.once('error', () => resolve(false));
    });
    if (ready) return;
    if (waited >= 120_000) { console.error(`${label} did not become ready within 120s — exiting.`); process.exit(1); }
    await new Promise((r) => setTimeout(r, 500));
  }
}

await waitForPort(CDP_PORT, 'chromium CDP');
await connectCdpBridge();
startMcp();
await waitForPort(MCP_PORT, '@playwright/mcp');

server.listen(PORT, '0.0.0.0', () => {
  console.log(`playwright-mcp sidecar listening on :${PORT} (mcp :${MCP_PORT}, cdp :${CDP_PORT})`);
});
