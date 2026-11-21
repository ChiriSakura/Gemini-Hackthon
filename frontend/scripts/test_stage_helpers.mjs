/**
 * Pure-Node Unit Regression Tests for StageState Frontend Helpers (W2-T10D).
 *
 * Feature & Use Cases:
 * - Zero extra npm dependencies (uses esbuild already shipped with Vite).
 * - Tests:
 *   1) `soundCue.ts` safety in headless / muted / unmuted environments.
 *   2) `mockBranches.ts` structural invariants (4 unique branches, scene_ids
 *      [1,2,3,4], valid 3x3 grids [0..2], valid lighting presets, 0 CJK).
 *   3) `stageClient.ts` FormData + offline edge cases (blank prop name, custom
 *      sceneId, failed fork reported as an error with no fabricated branch,
 *      health check reporting not-live, progress null fallback).
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(__dirname, '..');

async function loadTsModule(entryRelPath) {
  const result = await build({
    entryPoints: [path.join(frontendDir, entryRelPath)],
    bundle: true,
    write: false,
    format: 'esm',
    platform: 'node',
    target: 'es2022',
  });
  const code = result.outputFiles[0].text;
  const dataUrl = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
  return import(dataUrl);
}

const VALID_LIGHTING = new Set([
  'warm_dim',
  'cold_blue',
  'warm_transition',
  'warm_gold_snow',
]);
const CJK_REGEX = /[\u4e00-\u9fff]/;

// 1. Test soundCue.ts safety
const soundCue = await loadTsModule('src/utils/soundCue.ts');
assert.doesNotThrow(() => soundCue.playCompletionChime(false));
assert.doesNotThrow(() => soundCue.playCompletionChime(true));

// 2. Test mockBranches.ts invariants
const mockBranches = await loadTsModule('src/data/mockBranches.ts');
const allBranches = mockBranches.INITIAL_MOCK_BRANCHES;
assert.equal(allBranches.length, 4, 'Expected 4 offline preset branches');

const seenIds = new Set();
for (const branch of allBranches) {
  assert.equal(seenIds.has(branch.branch_id), false, `Duplicate branch_id: ${branch.branch_id}`);
  seenIds.add(branch.branch_id);
  assert.equal(CJK_REGEX.test(JSON.stringify(branch)), false, `CJK found in ${branch.branch_id}`);
  assert.deepEqual(
    branch.scenes.map((s) => s.scene_id),
    [1, 2, 3, 4],
    `Invalid scene sequence in ${branch.branch_id}`,
  );

  for (const scene of branch.scenes) {
    assert.equal(
      VALID_LIGHTING.has(scene.lighting),
      true,
      `Invalid lighting '${scene.lighting}' in ${branch.branch_id} S${scene.scene_id}`,
    );
    for (const actor of scene.actors) {
      assert.equal(actor.grid.length, 2);
      assert.equal(actor.grid[0] >= 0 && actor.grid[0] <= 2, true, `Actor col out of bounds: ${actor.grid}`);
      assert.equal(actor.grid[1] >= 0 && actor.grid[1] <= 2, true, `Actor row out of bounds: ${actor.grid}`);
    }
    for (const prop of scene.stage_props) {
      assert.equal(prop.grid.length, 2);
      assert.equal(prop.grid[0] >= 0 && prop.grid[0] <= 2, true, `Prop col out of bounds: ${prop.grid}`);
      assert.equal(prop.grid[1] >= 0 && prop.grid[1] <= 2, true, `Prop row out of bounds: ${prop.grid}`);
    }
  }
}

// 3. Test stageClient.ts offline fallbacks & FormData edge cases
const originalFetch = globalThis.fetch;
const capturedRequests = [];
globalThis.fetch = async (url, init) => {
  capturedRequests.push({ url: String(url), init });
  throw new Error('Simulated offline backend');
};

try {
  const client = await loadTsModule('src/api/stageClient.ts');

  const branchRes = await client.fetchBranches();
  assert.equal(branchRes.fromMock, true);
  assert.equal(branchRes.branches.length, 4);

  const forkRes = await client.submitDirectorFork({
    parentBranchId: 'branch_v1',
    targetSceneId: 2,
    text: 'Lia shields the crystal vase.',
    confirmContradiction: true,
  });
  assert.equal(forkRes.branch, null, 'A failed fork must never fabricate a branch');
  assert.match(forkRes.error, /Simulated offline backend/);
  const forkReq = capturedRequests.find((r) => r.url === '/api/director/fork');
  assert.ok(forkReq && forkReq.init?.body instanceof FormData);
  assert.equal(forkReq.init.body.get('confirm_contradiction'), 'true');

  const propRes = await client.createStageProp({
    name: '   ',
    branchId: 'branch_v1',
    sceneId: 3,
    imageBlob: null,
  });
  assert.equal(propRes.simulated, true);
  assert.match(propRes.prop_id, /^prop_\d+$/);
  const propReq = capturedRequests.find((r) => r.url === '/api/media/create-prop');
  assert.ok(propReq && propReq.init?.body instanceof FormData);
  assert.equal(propReq.init.body.get('scene_id'), '3');

  const prog = await client.fetchRenderProgress('branch_v1');
  assert.equal(prog, null);

  const offlineHealth = await client.fetchHealth();
  assert.deepEqual(offlineHealth, { reachable: false, live: false, demoMode: false });

  // HTTP errors must surface the status and FastAPI detail, still with no branch
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: 'Parent branch branch_x not found' }), {
      status: 404,
      headers: { 'Content-Type': 'application/json' },
    });
  const httpFork = await client.submitDirectorFork({
    parentBranchId: 'branch_x',
    targetSceneId: 2,
    text: 'Lia shields the crystal vase.',
  });
  assert.equal(httpFork.branch, null);
  assert.match(httpFork.error, /^HTTP 404 — Parent branch branch_x not found$/);

  // Health reports canned mode when the backend answers with live=false
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ status: 'ok', demo_mode: true, live: false }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  const cannedHealth = await client.fetchHealth();
  assert.deepEqual(cannedHealth, { reachable: true, live: false, demoMode: true });
} finally {
  globalThis.fetch = originalFetch;
}

console.log(
  '[test-stage-helpers] PASS: soundCue, 4-branch mock invariants (grids/lighting/0-CJK), and stageClient FormData / fork-error / health edge cases all passed!',
);
