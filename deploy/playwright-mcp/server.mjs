// Playwright MCP sidecar for the job-finder apply agent.
//
// The FastAPI Cloud backend has no Node/Chromium, so the apply agent connects to
// this service instead of spawning `npx @playwright/mcp` locally. This process:
//   1. spawns @playwright/mcp in streamable-HTTP mode on an internal port
//      (--isolated: every MCP session gets its own browser context, so several
//      apply runs share one sidecar), and
//   2. fronts it with a tiny proxy that enforces a bearer token, accepts resume
//      uploads (Playwright MCP can only browser_file_upload files that live under
//      its --output-dir), and answers /healthz for availability probes.
//
// Env: PLAYWRIGHT_MCP_TOKEN (required — requests without it are rejected),
//      PORT (public port, default 8080), MCP_PORT (internal, default 8931),
//      DATA_DIR (default /data/runs).

import http from 'node:http';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, readdirSync, statSync } from 'node:fs';
import { join, basename } from 'node:path';
import { randomUUID } from 'node:crypto';

const PORT = Number(process.env.PORT || 8080);
const MCP_PORT = Number(process.env.MCP_PORT || 8931);
const DATA_DIR = process.env.DATA_DIR || '/data/runs';
const TOKEN = (process.env.PLAYWRIGHT_MCP_TOKEN || '').trim();
const UPLOAD_MAX_BYTES = 10 * 1024 * 1024;
const UPLOAD_TTL_MS = 2 * 60 * 60 * 1000; // uploads older than 2h are swept

if (!TOKEN) {
  console.error('PLAYWRIGHT_MCP_TOKEN is required — refusing to start an open browser service.');
  process.exit(1);
}
mkdirSync(join(DATA_DIR, 'uploads'), { recursive: true });

// ── the actual Playwright MCP server (internal only) ────────────────────────
const mcp = spawn(
  'npx',
  [
    '-y', '@playwright/mcp@latest',
    '--headless', '--isolated', '--no-sandbox',
    '--host', '127.0.0.1', '--port', String(MCP_PORT),
    '--output-dir', DATA_DIR,
  ],
  { stdio: 'inherit' },
);
mcp.on('exit', (code) => {
  console.error(`@playwright/mcp exited with code ${code} — shutting down.`);
  process.exit(code ?? 1);
});
for (const sig of ['SIGINT', 'SIGTERM']) {
  process.on(sig, () => { mcp.kill(sig); process.exit(0); });
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

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end('{"ok": true}');
    return;
  }
  if (!authorized(req)) {
    res.writeHead(401, { 'content-type': 'application/json' });
    res.end('{"error": "unauthorized"}');
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

server.listen(PORT, '0.0.0.0', () => {
  console.log(`playwright-mcp sidecar listening on :${PORT} (mcp internal :${MCP_PORT})`);
});
