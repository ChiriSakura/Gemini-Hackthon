/**
 * Automated Pre-Pitch Frontend Verification Script (W2-T9C).
 *
 * Feature & Use Cases:
 * - Zero extra npm dependencies; runs via `node scripts/verify_frontend.mjs`
 *   inside `frontend/`.
 * - Verifies:
 *   1) Zero CJK characters across `frontend/src/**` and `frontend/index.html`.
 *   2) All referenced `/assets/actors/*` & `/assets/props/*` files exist under
 *      `frontend/public/assets/` or `backend/assets/`.
 *   3) Production build output (`dist/index.html`, JS/CSS bundles, and vendor
 *      chunks `three-vendor` / `react-vendor`) exist cleanly with size metrics.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(__dirname, '..');
const REPO_ROOT = path.resolve(FRONTEND_DIR, '..');

const CJK_REGEX = /[\u4e00-\u9fff]/g;
const ASSET_REF_REGEX = /\/assets\/(?:actors|props)\/[a-zA-Z0-9._-]+\.(?:png|jpg|webp)/g;

function walkFiles(dir, exts) {
  const results = [];
  if (!fs.existsSync(dir)) return results;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      results.push(...walkFiles(full, exts));
    } else if (exts.includes(path.extname(entry.name))) {
      results.push(full);
    }
  }
  return results;
}

let failed = false;

// 1. Zero CJK scan across frontend/src + frontend/index.html
const sourceFiles = [
  path.join(FRONTEND_DIR, 'index.html'),
  ...walkFiles(path.join(FRONTEND_DIR, 'src'), ['.ts', '.tsx', '.html', '.css']),
];
const cjkHits = [];
const referencedAssets = new Set();

for (const file of sourceFiles) {
  if (!fs.existsSync(file)) continue;
  const text = fs.readFileSync(file, 'utf8');
  const matches = text.match(CJK_REGEX);
  if (matches && matches.length > 0) {
    cjkHits.push({ file: path.relative(REPO_ROOT, file), count: matches.length });
  }
  const assetMatches = text.match(ASSET_REF_REGEX) ?? [];
  for (const ref of assetMatches) {
    referencedAssets.add(ref);
  }
}

if (cjkHits.length > 0) {
  console.error('[FAIL] CJK characters detected in frontend:', cjkHits);
  failed = true;
} else {
  console.log(`[PASS] 0-CJK UI audit clean (${sourceFiles.length} files scanned)`);
}

// 2. Verify all referenced /assets/actors/* & /assets/props/* exist in public or backend/assets
const missingAssets = [];
for (const assetUrl of [...referencedAssets].sort()) {
  const rel = assetUrl.replace(/^\/+/, '');
  const inPublic = path.join(FRONTEND_DIR, 'public', rel);
  const inBackend = path.join(REPO_ROOT, 'backend', rel);
  if (!fs.existsSync(inPublic) && !fs.existsSync(inBackend)) {
    missingAssets.push(assetUrl);
  }
}

if (missingAssets.length > 0) {
  console.error('[FAIL] Missing referenced stage assets:', missingAssets);
  failed = true;
} else {
  console.log(`[PASS] All ${referencedAssets.size} referenced actor/prop assets verified on disk`);
}

// 2b. Verify all EvalModal submission reels, storyboard, and desk showcase props exist on disk (W2-T10B)
const requiredSubmissionAssets = [
  'backend/assets/finals/demo_comparison_be_vs_he.mp4',
  'backend/assets/finals/pitch_demo_reel_1min.mp4',
  'backend/assets/submission/hook_10s_dualcam_twist.mp4',
  'backend/assets/submission/eval_dashboard_12s.mp4',
  'backend/assets/submission/eval_charts_board.png',
  'backend/assets/showcase_storyboard_strip.png',
  'backend/assets/props/showcase_desk_brass_key.png',
  'backend/assets/props/showcase_desk_coffee_mug.png',
  'backend/assets/props/showcase_desk_paper_heart.png',
];
const missingSubmission = requiredSubmissionAssets.filter(
  (rel) => !fs.existsSync(path.join(REPO_ROOT, rel)),
);
if (missingSubmission.length > 0) {
  console.error('[FAIL] Missing EvalModal submission/desk assets:', missingSubmission);
  failed = true;
} else {
  console.log(`[PASS] All ${requiredSubmissionAssets.length} EvalModal submission & desk assets verified on disk`);
}

// 3. Verify dist/index.html & vendor chunks exist cleanly after npm run build
const distDir = path.join(FRONTEND_DIR, 'dist');
const distIndex = path.join(distDir, 'index.html');
const distAssetsDir = path.join(distDir, 'assets');

if (!fs.existsSync(distIndex) || !fs.existsSync(distAssetsDir)) {
  console.error('[FAIL] Missing dist/index.html or dist/assets/ — run `npm run build` first.');
  failed = true;
} else {
  const assetFiles = fs.readdirSync(distAssetsDir).sort();
  const hasThreeVendor = assetFiles.some((f) => f.startsWith('three-vendor-') && f.endsWith('.js'));
  const hasReactVendor = assetFiles.some((f) => f.startsWith('react-vendor-') && f.endsWith('.js'));
  const hasAppJs = assetFiles.some((f) => f.startsWith('index-') && f.endsWith('.js'));
  const hasAppCss = assetFiles.some((f) => f.startsWith('index-') && f.endsWith('.css'));

  if (!hasThreeVendor || !hasReactVendor || !hasAppJs || !hasAppCss) {
    console.error('[FAIL] Expected build chunks missing in dist/assets/:', assetFiles);
    failed = true;
  } else {
    const summary = assetFiles.map((f) => {
      const kb = (fs.statSync(path.join(distAssetsDir, f)).size / 1024).toFixed(2);
      return `${f} (${kb} kB)`;
    });
    console.log('[PASS] Build chunks verified:', summary.join(', '));
  }
}

if (failed) {
  process.exit(1);
}
console.log('[OK] All StageState frontend sanity checks passed 100%!');
