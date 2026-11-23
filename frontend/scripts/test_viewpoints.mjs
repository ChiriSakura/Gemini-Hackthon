/**
 * The browser stage and the backend stage must put the same world point on the same pixel.
 *
 * Both read the two viewpoints from shared/viewpoints.json. backend/test_world_rules.py writes
 * shared/viewpoints_check.json (world points and the pixels backend/media/stage3d.py projects them to);
 * this script projects the same points with a Three.js PerspectiveCamera and compares.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const viewpoints = JSON.parse(fs.readFileSync(path.join(root, 'shared', 'viewpoints.json'), 'utf8'));
const check = JSON.parse(fs.readFileSync(path.join(root, 'shared', 'viewpoints_check.json'), 'utf8'));
const TOLERANCE_PX = 1.0;

let worst = 0;
let failed = 0;
for (const camId of ['cam_a', 'cam_b']) {
  const spec = viewpoints[camId];
  const camera = new THREE.PerspectiveCamera(spec.fov, check.width / check.height, 0.1, 50);
  camera.position.set(...spec.pos);
  camera.lookAt(...spec.look_at);
  camera.updateMatrixWorld(true);
  camera.updateProjectionMatrix();
  for (const item of check.points[camId]) {
    const ndc = new THREE.Vector3(...item.world).project(camera);
    const px = [(ndc.x * 0.5 + 0.5) * check.width, (1 - (ndc.y * 0.5 + 0.5)) * check.height];
    const err = Math.hypot(px[0] - item.pixel[0], px[1] - item.pixel[1]);
    worst = Math.max(worst, err);
    if (err > TOLERANCE_PX) {
      failed += 1;
      console.error(`FAIL ${camId} ${item.name}: browser ${px.map((v) => v.toFixed(2))} vs backend ${item.pixel}`);
    }
  }
}
if (failed) {
  console.error(`viewpoints: ${failed} point(s) differ by more than ${TOLERANCE_PX}px`);
  process.exit(1);
}
console.log(`viewpoints OK: browser and backend agree on every point (worst ${worst.toFixed(3)}px at ${check.width}x${check.height})`);
