/**
 * Offline Vite Preview HTTP 200 Smoke Test (W2-T11C).
 *
 * Feature & Use Cases:
 * - Spawns `vite preview --port 4173 --strictPort` against the production `dist/`
 *   bundle, verifies HTTP 200 OK for `/` and core static assets (`lia.png`,
 *   `crystal_vase_broken.png`, `star_token.png`), and terminates cleanly.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(__dirname, '..');
const PORT = 4173;
const BASE_URL = `http://127.0.0.1:${PORT}`;

const ENDPOINTS = [
  '/',
  '/assets/actors/lia.png',
  '/assets/props/crystal_vase_broken.png',
  '/assets/props/star_token.png',
];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForServer(timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BASE_URL}/`);
      if (res.ok) return;
    } catch {
      // Server still booting
    }
    await sleep(150);
  }
  throw new Error(`Timed out waiting for vite preview on ${BASE_URL}`);
}

const server = spawn(
  process.platform === 'win32' ? 'npx.cmd' : 'npx',
  ['vite', 'preview', '--host', '127.0.0.1', '--port', String(PORT), '--strictPort'],
  {
    cwd: frontendDir,
    stdio: ['ignore', 'pipe', 'pipe'],
  },
);

let stderrLog = '';
server.stderr.on('data', (chunk) => {
  stderrLog += chunk.toString();
});

try {
  await waitForServer();

  for (const endpoint of ENDPOINTS) {
    const res = await fetch(`${BASE_URL}${endpoint}`);
    assert.equal(
      res.status,
      200,
      `Expected HTTP 200 for ${endpoint}, got ${res.status}`,
    );
    const buf = await res.arrayBuffer();
    assert.ok(buf.byteLength > 0, `Empty body returned for ${endpoint}`);
    console.log(`[smoke-preview] PASS 200 OK ${endpoint} (${buf.byteLength} bytes)`);
  }

  console.log('[smoke-preview] OK: All offline vite preview smoke checks passed!');
} catch (err) {
  console.error('[smoke-preview] FAIL:', err);
  if (stderrLog) console.error(stderrLog);
  process.exitCode = 1;
} finally {
  server.kill('SIGTERM');
  await new Promise((resolve) => {
    server.once('exit', resolve);
    setTimeout(() => {
      try {
        server.kill('SIGKILL');
      } catch {
        // Ignore
      }
      resolve();
    }, 1500);
  });
}
