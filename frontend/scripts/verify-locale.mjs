/**
 * Automated Zero-CJK & English Localization Regression Check for StageState Frontend.
 *
 * Feature & Use Cases:
 * - Verifies zero CJK characters exist anywhere in `frontend/src/**` or `frontend/index.html`.
 * - Compiles `frontend/src/utils/displayLocale.ts` in-memory via esbuild and runs
 *   `sanitizeBranchToEn()` / `sanitizeRulesToEn()` against real backend cached branch JSONs
 *   plus an adversarial CJK payload to guarantee zero CJK leakage into the live UI.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { transform } from 'esbuild';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(__dirname, '..');
const repoRoot = path.resolve(frontendDir, '..');
const CJK_REGEX = /[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]/;

function walkFiles(dir, exts, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === 'dist') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkFiles(full, exts, out);
    } else if (exts.has(path.extname(entry.name))) {
      out.push(full);
    }
  }
  return out;
}

// 1. Audit all frontend source files for zero CJK
const sourceFiles = [
  path.join(frontendDir, 'index.html'),
  ...walkFiles(path.join(frontendDir, 'src'), new Set(['.ts', '.tsx', '.css'])),
];
const cjkSourceHits = [];
for (const filePath of sourceFiles) {
  const content = fs.readFileSync(filePath, 'utf8');
  if (CJK_REGEX.test(content)) {
    cjkSourceHits.push(path.relative(repoRoot, filePath));
  }
}
assert.deepEqual(
  cjkSourceHits,
  [],
  `Unexpected CJK found in frontend source files: ${cjkSourceHits.join(', ')}`,
);

// 2. Load displayLocale.ts via esbuild and run deep sanitizer assertions
const localeTsPath = path.join(frontendDir, 'src/utils/displayLocale.ts');
const tsSource = fs.readFileSync(localeTsPath, 'utf8');
const { code } = await transform(tsSource, {
  loader: 'ts',
  format: 'esm',
  target: 'es2022',
});
const dataUrl = `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`;
const localeMod = await import(dataUrl);

const cachedBranchFiles = [
  'backend/assets/branches/branch_v1.json',
  'backend/assets/branches/branch_v2_cached.json',
  'backend/assets/branches/branch_v3_starlight_cached.json',
];

let checkedBranches = 0;
for (const relPath of cachedBranchFiles) {
  const absPath = path.join(repoRoot, relPath);
  if (!fs.existsSync(absPath)) continue;
  const raw = JSON.parse(fs.readFileSync(absPath, 'utf8'));
  const sanitized = localeMod.sanitizeBranchToEn(raw);
  const serialized = JSON.stringify(sanitized);
  assert.equal(
    CJK_REGEX.test(serialized),
    false,
    `CJK leaked after sanitizeBranchToEn() for ${relPath}`,
  );
  assert.equal(
    sanitized.scenes.length,
    raw.scenes.length,
    `Scene count mismatch for ${relPath}`,
  );
  checkedBranches += 1;
}

// 3. Adversarial test: verify [CAM A]/[CAM B] prefix preservation & full CJK stripping
const adversarial = {
  branch_id: 'branch_adv_test',
  branch_label: '\u6d4b\u8bd5\u5206\u652f',
  parent_branch_id: 'branch_v1',
  forked_from_scene: 2,
  rewrite_summary: '\u6d4b\u8bd5\u6539\u5199\u6458\u8981',
  contradiction_warning: '\u68c0\u6d4b\u5230\u77db\u76fe\u8b66\u544a',
  props_registry: {
    star_token: {
      name: '\u661f\u5149\u4fe1\u7269',
      image_url: '/assets/props/star_token.png',
      state: '\u53d1\u5149',
      origin: 'camera_physical',
    },
  },
  scenes: [
    {
      scene_id: 2,
      title: '\u6d4b\u8bd5\u5e55',
      duration_sec: 4,
      narration: '\u6d4b\u8bd5\u65c1\u767d',
      vocal_emotion: '\u6e29\u67d4',
      gesture_intent: '\u62db\u624b',
      direction_hint: '\u5de6\u4fa7',
      lighting: 'warm_transition',
      camera_shake: false,
      audio_mood: '\u8f7b\u67d4',
      environment_notes: '\u5730\u9762\u65e0\u788e\u73bb\u7483',
      needs_rerender: true,
      reused_from_branch: null,
      actors: [
        {
          id: 'lia',
          name: '\u8389\u4e9a',
          asset_url: '/assets/actors/lia.png',
          grid: [0, 1],
          facing: 'right',
          action: 'stand',
          holding_prop: null,
        },
      ],
      stage_props: [],
      qa_questions: ['\u82b1\u74f6\u662f\u5426\u5b8c\u597d\uff1f'],
      media: {
        cam_a_preview_url: null,
        cam_b_preview_url: null,
        cam_a_final_url: null,
        cam_b_final_url: null,
        cam_a_tail_frame_url: null,
        cam_b_tail_frame_url: null,
        qa_score: 0.95,
        qa_retries: 0,
        selected_candidate_index: 0,
        fallback_to_preview: false,
        qa_result: {
          passed: true,
          score: 0.95,
          items: [
            {
              question: '[CAM A] \u82b1\u74f6\u662f\u5426\u5b8c\u597d\uff1f',
              passed: true,
              confidence: 0.95,
              reason: '\u753b\u9762\u4e00\u81f4',
            },
            {
              question: '[CAM B] \u9ed1\u732b\u662f\u5426\u5728\u53f3\u4fa7\uff1f',
              passed: true,
              confidence: 0.96,
              reason: '\u4f4d\u7f6e\u6b63\u786e',
            },
          ],
        },
      },
    },
  ],
};

const sanitizedAdv = localeMod.sanitizeBranchToEn(adversarial);
assert.equal(CJK_REGEX.test(JSON.stringify(sanitizedAdv)), false);
assert.equal(
  sanitizedAdv.scenes[0].media.qa_result.items[0].question.startsWith('[CAM A] '),
  true,
);
assert.equal(
  sanitizedAdv.scenes[0].media.qa_result.items[1].question.startsWith('[CAM B] '),
  true,
);

const sanitizedRules = localeMod.sanitizeRulesToEn([
  '\u6d4b\u8bd5\u89c4\u5219\u4e00',
  'Actors must keep consistent identity.',
]);
assert.equal(CJK_REGEX.test(JSON.stringify(sanitizedRules)), false);
assert.equal(sanitizedRules[1], 'Actors must keep consistent identity.');

console.log(
  `[verify-locale] PASS: ${sourceFiles.length} frontend source files + ${checkedBranches} cached backend branches + adversarial CJK payload verified (0 CJK leaks).`,
);
