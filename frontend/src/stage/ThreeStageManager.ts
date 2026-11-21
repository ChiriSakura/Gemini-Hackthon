/**
 * Three.js 2.5D Dual-Camera Stage Engine for StageState.
 *
 * Feature & Use Cases:
 * - Renders a 3x3 lit glass-conservatory grid stage with 2.5D billboard sprites
 *   for actors (`lia`, `nyx`) and stage props (`crystal_vase`, `star_token`, etc.).
 * - Simultaneously renders Camera A (front wide) and Camera B (45-deg close-up)
 *   onto two separate WebGL canvases from the exact same Three.js scene graph,
 *   guaranteeing spatial consistency.
 * - Supports all 4 lighting presets (`warm_dim`, `cold_blue` rain particles,
 *   `warm_transition`, `warm_gold_snow` golden snow particles) + camera shake.
 * - Exports 4-second `.webm` preview clips via `canvas.captureStream()` with an
 *   automatic start+end PNG keyframe fallback (`preserveDrawingBuffer: true`).
 */
import * as THREE from 'three';
import type { LightingPreset, PropRegistryItem, Scene } from '../types/schema';

export interface PreviewExportResult {
  mode: 'webm' | 'png_fallback';
  camABlob: Blob | null;
  camBBlob: Blob | null;
  camAStartPng: string;
  camAEndPng: string;
  camBStartPng: string;
  camBEndPng: string;
  elapsedMs: number;
}

interface SpriteEntity {
  sprite: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  crossCard?: THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>;
  shadow?: THREE.Mesh;
  haloRing?: THREE.Mesh;
  diffHighlighted?: boolean;
  baseScale: [number, number];
  targetPos: THREE.Vector3;
  action: string;
  facing: string;
  worldYaw: number;
  assetUrl?: string;
  appliedFlipped?: boolean;
  broken?: boolean;
  currentTextureUrl?: string;
  usingBrokenAsset?: boolean;
  heightM?: number | null;
}

/**
 * Compute a rigid 3D world-space yaw (radians around Y) from a scene actor's facing direction.
 * Unlike camera-facing `THREE.Sprite` billboards, world-locked standee planes never rotate
 * toward Camera A or Camera B, so both views strictly obey rigid 3D viewpoint projection.
 */
function facingToWorldYaw(facing: string): number {
  switch (facing) {
    case 'right':
      return 0.24;
    case 'left':
      return -0.24;
    case 'back':
      return Math.PI;
    case 'front':
    default:
      return 0;
  }
}

interface PropFlightState {
  startPos: THREE.Vector3;
  endPos: THREE.Vector3;
  peakHeight: number;
  startedAt: number;
  durationSec: number;
}

const WORLD_UNITS_PER_METER = 1.36;
const PROP_ASSET_ROOT = `${'/assets'}/props`;

/**
 * Convert `[col, row]` in 0..2 into Three.js world `[x, z]` coordinates.
 */
function gridToWorld(grid: [number, number]): { x: number; z: number } {
  const x = (grid[0] - 1) * 2.1;
  const z = (grid[1] - 1) * 1.7;
  return { x, z };
}

/**
 * Create a procedural glowing star-token texture when a dynamic prop image
 * is not yet available on disk (e.g., offline mock mode).
 */
function createFallbackStarTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d')!;
  const grad = ctx.createRadialGradient(128, 128, 10, 128, 128, 110);
  grad.addColorStop(0, 'rgba(254, 240, 138, 1)');
  grad.addColorStop(0.45, 'rgba(251, 191, 36, 0.75)');
  grad.addColorStop(1, 'rgba(251, 191, 36, 0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 256, 256);

  ctx.fillStyle = '#fffbeb';
  ctx.beginPath();
  for (let i = 0; i < 8; i++) {
    const angle = (i * Math.PI) / 4;
    const r = i % 2 === 0 ? 58 : 22;
    const px = 128 + Math.cos(angle) * r;
    const py = 128 + Math.sin(angle) * r;
    if (i === 0) ctx.moveTo(px, py);
    else ctx.lineTo(px, py);
  }
  ctx.closePath();
  ctx.fill();

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * Procedural standalone crimson umbrella texture (closed furled vs open dome)
 * used as graceful fallback when P2's standalone PNG is not yet on disk (R-12).
 */
function createFallbackUmbrellaTexture(open: boolean): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, 256, 256);

  if (open) {
    // Open crimson vintage dome
    const domeGrad = ctx.createLinearGradient(36, 40, 220, 128);
    domeGrad.addColorStop(0, '#991b1b');
    domeGrad.addColorStop(0.5, '#dc2626');
    domeGrad.addColorStop(1, '#7f1d1d');
    ctx.fillStyle = domeGrad;
    ctx.beginPath();
    ctx.moveTo(28, 124);
    ctx.quadraticCurveTo(128, 22, 228, 124);
    ctx.quadraticCurveTo(195, 108, 162, 124);
    ctx.quadraticCurveTo(128, 106, 94, 124);
    ctx.quadraticCurveTo(60, 108, 28, 124);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth = 3;
    ctx.stroke();

    // Brass finial + shaft + curved wooden handle
    ctx.strokeStyle = '#d97706';
    ctx.lineWidth = 6;
    ctx.beginPath();
    ctx.moveTo(128, 36);
    ctx.lineTo(128, 232);
    ctx.arc(114, 232, 14, 0, Math.PI, false);
    ctx.stroke();
  } else {
    // Closed (furled) vertical crimson umbrella resting tip-down
    const furledGrad = ctx.createLinearGradient(104, 52, 152, 232);
    furledGrad.addColorStop(0, '#dc2626');
    furledGrad.addColorStop(0.6, '#991b1b');
    furledGrad.addColorStop(1, '#450a0a');
    ctx.fillStyle = furledGrad;
    ctx.beginPath();
    ctx.moveTo(128, 246);
    ctx.lineTo(108, 72);
    ctx.quadraticCurveTo(128, 56, 148, 72);
    ctx.closePath();
    ctx.fill();
    ctx.strokeStyle = '#fbbf24';
    ctx.lineWidth = 2.5;
    ctx.stroke();

    // Brass tie band
    ctx.fillStyle = '#f59e0b';
    ctx.fillRect(113, 140, 30, 8);

    // Curved handle at top + brass tip at bottom
    ctx.strokeStyle = '#d97706';
    ctx.lineWidth = 7;
    ctx.lineCap = 'round';
    ctx.beginPath();
    ctx.moveTo(128, 66);
    ctx.lineTo(128, 30);
    ctx.arc(144, 30, 16, Math.PI, 0, false);
    ctx.stroke();
  }

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * Procedural Victorian wooden conservatory chair fallback texture (R-12).
 */
function createFallbackChairTexture(): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d')!;
  ctx.clearRect(0, 0, 256, 256);

  ctx.strokeStyle = '#92400e';
  ctx.fillStyle = '#78350f';
  ctx.lineWidth = 10;
  ctx.lineCap = 'round';

  // Backrest posts & slats
  ctx.beginPath();
  ctx.moveTo(74, 36);
  ctx.lineTo(74, 244);
  ctx.moveTo(182, 36);
  ctx.lineTo(182, 244);
  ctx.moveTo(74, 48);
  ctx.lineTo(182, 48);
  ctx.moveTo(74, 88);
  ctx.lineTo(182, 88);
  ctx.stroke();

  // Seat plank
  ctx.fillStyle = '#b45309';
  ctx.fillRect(62, 134, 132, 18);
  ctx.strokeStyle = '#fcd34d';
  ctx.lineWidth = 2;
  ctx.strokeRect(62, 134, 132, 18);

  // Front legs & stretcher
  ctx.strokeStyle = '#78350f';
  ctx.lineWidth = 9;
  ctx.beginPath();
  ctx.moveTo(86, 152);
  ctx.lineTo(86, 244);
  ctx.moveTo(170, 152);
  ctx.lineTo(170, 244);
  ctx.moveTo(74, 206);
  ctx.lineTo(182, 206);
  ctx.stroke();

  const tex = new THREE.CanvasTexture(canvas);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/**
 * Create a circular soft particle texture for rain and golden snow.
 */
function createParticleTexture(color: string): THREE.CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d')!;
  const grad = ctx.createRadialGradient(32, 32, 2, 32, 32, 30);
  grad.addColorStop(0, color);
  grad.addColorStop(1, 'rgba(255,255,255,0)');
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(canvas);
}

/**
 * Pick the best supported WebM MIME type for MediaRecorder.
 */
function getSupportedWebmMime(): string | null {
  if (typeof MediaRecorder === 'undefined') return null;
  const candidates = [
    'video/webm;codecs=vp9',
    'video/webm;codecs=vp8',
    'video/webm',
  ];
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime;
  }
  return null;
}

export class ThreeStageManager {
  private scene: THREE.Scene;
  private cameraA: THREE.PerspectiveCamera;
  private cameraB: THREE.PerspectiveCamera;
  private rendererA: THREE.WebGLRenderer;
  private rendererB: THREE.WebGLRenderer;
  private canvasA: HTMLCanvasElement;
  private canvasB: HTMLCanvasElement;

  private ambientLight: THREE.AmbientLight;
  private spotLight: THREE.SpotLight;
  private rimLight: THREE.DirectionalLight;

  private rainPoints: THREE.Points;
  private snowPoints: THREE.Points;
  private glassShardsGroup: THREE.Group;

  private textureLoader = new THREE.TextureLoader();
  private textureCache = new Map<string, THREE.Texture>();
  private flippedTextureCache = new Map<string, THREE.Texture>();
  private failedUrls = new Set<string>();
  private fallbackStarTexture: THREE.CanvasTexture | null = null;
  private fallbackUmbrellaClosedTexture: THREE.CanvasTexture | null = null;
  private fallbackUmbrellaOpenTexture: THREE.CanvasTexture | null = null;
  private fallbackChairTexture: THREE.CanvasTexture | null = null;
  private actorEntities = new Map<string, SpriteEntity>();
  private propEntities = new Map<string, SpriteEntity>();
  private standeeGeometry: THREE.PlaneGeometry;
  private activePropFlights = new Map<string, PropFlightState>();
  private seenThrownPropKeys = new Set<string>();
  private cameraGrabState: { actorId: string; startedAt: number; durationSec: number } | null = null;
  private catchRippleMesh: THREE.Mesh;

  private currentLighting: LightingPreset = 'warm_dim';
  private cameraShake = false;
  private cameraBTarget = new THREE.Vector3(0, 0.9, 0);
  private currentCameraBTarget = new THREE.Vector3(0, 0.9, 0);
  private targetBgColor = new THREE.Color('#0c101d');
  private targetAmbientColor = new THREE.Color('#cbd5e1');
  private targetAmbientIntensity = 0.8;
  private targetSpotColor = new THREE.Color('#fde68a');
  private targetSpotIntensity = 26;
  private targetRimColor = new THREE.Color('#60a5fa');
  private sculptureFrozen = false;
  private sculptureTintBlend = 0;
  private frozenElapsed = 0;
  private animElapsed = 0;
  private readonly colorWhite = new THREE.Color('#ffffff');
  private readonly colorSculpture = new THREE.Color('#cbd7ea');
  private readonly colorSculptureAmbient = new THREE.Color('#9fb3d4');
  private readonly colorSculptureSpot = new THREE.Color('#e2ecff');
  private animFrameId: number | null = null;
  private clock = new THREE.Clock();
  private disposed = false;

  // Drag-to-orbit: both cameras turn around the stage by the same angle, so B stays 45 degrees from A.
  private cameraATarget = new THREE.Vector3(0, 0.9, 0);
  private orbitYaw = 0;
  private orbitPitch = 0;
  private orbitZoom = 1;
  private orbitDrag: { x: number; y: number } | null = null;
  private orbitScratch = new THREE.Vector3();
  private orbitSpherical = new THREE.Spherical();

  /** Called when the user drops a character at a new spot: (actor id, new grid position). */
  public onActorMoved: ((actorId: string, grid: [number, number]) => void) | null = null;
  private actorDrag: { id: string; camera: THREE.PerspectiveCamera } | null = null;
  private pickRay = new THREE.Raycaster();
  private pickNdc = new THREE.Vector2();
  private floorPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
  private floorHit = new THREE.Vector3();

  private aimPickRay(e: PointerEvent, camera: THREE.PerspectiveCamera): void {
    const rect = (e.currentTarget as HTMLCanvasElement).getBoundingClientRect();
    this.pickNdc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    this.pickRay.setFromCamera(this.pickNdc, camera);
  }

  /** Keep a dragged character on the stage floor and outside the round table, so nothing clips through it. */
  private clampToFreeFloor(x: number, z: number, actorId: string): [number, number] {
    const cx = Math.max(-2.1, Math.min(2.1, x));
    const cz = Math.max(-1.7, Math.min(1.7, z));
    const minDist = actorId === 'nyx' ? 0.8 : 1.05;
    const dist = Math.hypot(cx, cz);
    if (dist >= minDist) return [cx, cz];
    if (dist < 0.001) return [-minDist, 0];
    return [(cx / dist) * minDist, (cz / dist) * minDist];
  }

  private handleOrbitDown = (e: PointerEvent): void => {
    const camera = e.currentTarget === this.canvasB ? this.cameraB : this.cameraA;
    this.aimPickRay(e, camera);
    const sprites = [...this.actorEntities.values()].map((entity) => entity.sprite);
    const hit = this.pickRay.intersectObjects(sprites, false)[0];
    const hitId = hit ? [...this.actorEntities.entries()].find(([, entity]) => entity.sprite === hit.object)?.[0] : null;
    if (hitId) {
      this.actorDrag = { id: hitId, camera };
    } else {
      this.orbitDrag = { x: e.clientX, y: e.clientY };
    }
    (e.currentTarget as HTMLCanvasElement).setPointerCapture?.(e.pointerId);
  };

  private handleOrbitMove = (e: PointerEvent): void => {
    if (this.actorDrag) {
      const entity = this.actorEntities.get(this.actorDrag.id);
      this.aimPickRay(e, this.actorDrag.camera);
      if (entity && this.pickRay.ray.intersectPlane(this.floorPlane, this.floorHit)) {
        const [x, z] = this.clampToFreeFloor(this.floorHit.x, this.floorHit.z, this.actorDrag.id);
        entity.targetPos.x = x;
        entity.targetPos.z = z;
      }
      return;
    }
    if (!this.orbitDrag) return;
    this.orbitYaw -= (e.clientX - this.orbitDrag.x) * 0.008;
    this.orbitPitch = Math.max(-0.45, Math.min(0.6, this.orbitPitch + (e.clientY - this.orbitDrag.y) * 0.005));
    this.orbitDrag = { x: e.clientX, y: e.clientY };
  };

  private handleOrbitUp = (): void => {
    if (this.actorDrag) {
      const entity = this.actorEntities.get(this.actorDrag.id);
      if (entity) {
        const grid: [number, number] = [
          Math.round((entity.targetPos.x / 2.1 + 1) * 100) / 100,
          Math.round((entity.targetPos.z / 1.7 + 1) * 100) / 100,
        ];
        this.onActorMoved?.(this.actorDrag.id, grid);
      }
      this.actorDrag = null;
    }
    this.orbitDrag = null;
  };

  private handleOrbitWheel = (e: WheelEvent): void => {
    e.preventDefault();
    this.orbitZoom = Math.max(0.55, Math.min(1.7, this.orbitZoom * (e.deltaY > 0 ? 1.06 : 0.94)));
  };

  /** Return both cameras to their fixed positions (double-click, or before a preview export). */
  public resetOrbit = (): void => {
    this.orbitYaw = 0;
    this.orbitPitch = 0;
    this.orbitZoom = 1;
  };

  private placeCamera(camera: THREE.PerspectiveCamera, x: number, y: number, z: number, target: THREE.Vector3): void {
    if (this.orbitYaw === 0 && this.orbitPitch === 0 && this.orbitZoom === 1) {
      camera.position.set(x, y, z);
    } else {
      this.orbitScratch.set(x, y, z).sub(target);
      this.orbitSpherical.setFromVector3(this.orbitScratch);
      this.orbitSpherical.theta += this.orbitYaw;
      this.orbitSpherical.phi = Math.max(0.25, Math.min(1.5, this.orbitSpherical.phi - this.orbitPitch));
      this.orbitSpherical.radius *= this.orbitZoom;
      camera.position.setFromSpherical(this.orbitSpherical).add(target);
    }
    camera.lookAt(target);
  }
  private contextLost = false;
  private pixelArtMode = false;
  private diffHaloEnabled = true;

  private handleContextLost = (event: Event): void => {
    event.preventDefault();
    this.contextLost = true;
    if (this.animFrameId !== null) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }
  };

  private handleContextRestored = (): void => {
    this.contextLost = false;
    const width = this.pixelArtMode ? 160 : 640;
    const height = this.pixelArtMode ? 90 : 360;
    const dpr = this.pixelArtMode ? 1 : Math.min(window.devicePixelRatio, 2);
    this.rendererA.setPixelRatio(dpr);
    this.rendererA.setSize(width, height, false);
    this.rendererB.setPixelRatio(dpr);
    this.rendererB.setSize(width, height, false);

    for (const tex of this.textureCache.values()) {
      tex.needsUpdate = true;
    }
    for (const tex of this.flippedTextureCache.values()) {
      tex.needsUpdate = true;
    }
    if (!this.disposed && this.animFrameId === null) {
      this.startLoop();
    }
  };

  /**
   * Initialize the shared 3D world and attach two WebGLRenderers for Camera A & B.
   */
  constructor(canvasA: HTMLCanvasElement, canvasB: HTMLCanvasElement) {
    this.canvasA = canvasA;
    this.canvasB = canvasB;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#090d18');
    this.scene.fog = new THREE.FogExp2('#090d18', 0.055);

    // Bottom-center anchored 3D standee card (pivot at y = 0) shared across world-locked actors & props
    this.standeeGeometry = new THREE.PlaneGeometry(1, 1);
    this.standeeGeometry.translate(0, 0.5, 0);

    this.cameraA = new THREE.PerspectiveCamera(44, 16 / 9, 0.1, 50);
    this.cameraA.position.set(0, 2.75, 6.2);
    this.cameraA.lookAt(0, 0.9, 0);

    this.cameraB = new THREE.PerspectiveCamera(36, 16 / 9, 0.1, 50);
    this.cameraB.position.set(3.25, 1.95, 3.55);
    this.cameraB.lookAt(this.cameraBTarget);

    for (const canvas of [canvasA, canvasB]) {
      canvas.style.cursor = 'grab';
      canvas.style.touchAction = 'none';
      canvas.addEventListener('pointerdown', this.handleOrbitDown);
      canvas.addEventListener('pointermove', this.handleOrbitMove);
      canvas.addEventListener('pointerup', this.handleOrbitUp);
      canvas.addEventListener('pointercancel', this.handleOrbitUp);
      canvas.addEventListener('wheel', this.handleOrbitWheel, { passive: false });
      canvas.addEventListener('dblclick', this.resetOrbit);
    }

    this.rendererA = new THREE.WebGLRenderer({
      canvas: canvasA,
      antialias: true,
      preserveDrawingBuffer: true,
    });
    this.rendererA.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.rendererA.setSize(640, 360, false);

    this.rendererB = new THREE.WebGLRenderer({
      canvas: canvasB,
      antialias: true,
      preserveDrawingBuffer: true,
    });
    this.rendererB.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.rendererB.setSize(640, 360, false);

    this.ambientLight = new THREE.AmbientLight('#c7d2fe', 0.85);
    this.scene.add(this.ambientLight);

    this.spotLight = new THREE.SpotLight('#fde68a', 28, 20, Math.PI / 3, 0.45, 1.2);
    this.spotLight.position.set(0, 5.5, 2.5);
    this.scene.add(this.spotLight);

    this.rimLight = new THREE.DirectionalLight('#38bdf8', 1.2);
    this.rimLight.position.set(-4, 4, -3);
    this.scene.add(this.rimLight);

    this.buildConservatoryStage();
    this.glassShardsGroup = this.buildGlassShards();
    this.scene.add(this.glassShardsGroup);

    this.rainPoints = this.buildWeatherParticles(420, '#7dd3fc', 0.14);
    this.snowPoints = this.buildWeatherParticles(320, '#fde68a', 0.22);
    this.scene.add(this.rainPoints);
    this.scene.add(this.snowPoints);

    // Golden outstretched-arm catch ripple ring shown during `catch_from_camera` (W2-V2-03)
    this.catchRippleMesh = new THREE.Mesh(
      new THREE.RingGeometry(0.24, 0.36, 36),
      new THREE.MeshBasicMaterial({
        color: '#fde68a',
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.82,
        depthWrite: false,
      }),
    );
    this.catchRippleMesh.visible = false;
    this.scene.add(this.catchRippleMesh);

    this.canvasA.addEventListener('webglcontextlost', this.handleContextLost, false);
    this.canvasB.addEventListener('webglcontextlost', this.handleContextLost, false);
    this.canvasA.addEventListener('webglcontextrestored', this.handleContextRestored, false);
    this.canvasB.addEventListener('webglcontextrestored', this.handleContextRestored, false);

    this.startLoop();
  }

  private createConservatoryBackdropTexture(): THREE.CanvasTexture {
    const canvas = document.createElement('canvas');
    canvas.width = 1024;
    canvas.height = 512;
    const ctx = canvas.getContext('2d')!;

    // Night sky gradient
    const sky = ctx.createLinearGradient(0, 0, 0, 512);
    sky.addColorStop(0, '#091124');
    sky.addColorStop(0.6, '#101d3a');
    sky.addColorStop(1, '#0b1020');
    ctx.fillStyle = sky;
    ctx.fillRect(0, 0, 1024, 512);

    // Subtle stars through glass roof
    for (let i = 0; i < 65; i++) {
      const sx = (i * 173) % 1024;
      const sy = (i * 89) % 340;
      const r = (i % 3) * 0.7 + 0.7;
      ctx.fillStyle = `rgba(254, 249, 195, ${0.25 + (i % 5) * 0.12})`;
      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fill();
    }

    // Three Victorian conservatory glass arches
    ctx.strokeStyle = 'rgba(148, 163, 184, 0.32)';
    ctx.lineWidth = 6;
    const archCenters = [200, 512, 824];
    for (const cx of archCenters) {
      ctx.beginPath();
      ctx.moveTo(cx - 125, 512);
      ctx.lineTo(cx - 125, 190);
      ctx.arc(cx, 190, 125, Math.PI, 0, false);
      ctx.lineTo(cx + 125, 512);
      ctx.stroke();

      // Inner mullions
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.moveTo(cx, 65);
      ctx.lineTo(cx, 512);
      ctx.moveTo(cx - 125, 290);
      ctx.lineTo(cx + 125, 290);
      ctx.stroke();
      ctx.lineWidth = 6;

      // Soft lantern glow at arch pillar
      const lantern = ctx.createRadialGradient(cx - 125, 270, 4, cx - 125, 270, 52);
      lantern.addColorStop(0, 'rgba(253, 224, 71, 0.55)');
      lantern.addColorStop(1, 'rgba(253, 224, 71, 0)');
      ctx.fillStyle = lantern;
      ctx.fillRect(cx - 180, 215, 110, 110);
    }

    const tex = new THREE.CanvasTexture(canvas);
    tex.colorSpace = THREE.SRGBColorSpace;
    return tex;
  }

  private buildConservatoryStage(): void {
    // 3x3 reflective glasshouse floor
    const floorGeo = new THREE.PlaneGeometry(7.2, 6.0);
    const floorMat = new THREE.MeshStandardMaterial({
      color: '#111827',
      roughness: 0.25,
      metalness: 0.4,
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    this.scene.add(floor);

    // 3x3 grid lines
    const gridHelper = new THREE.GridHelper(6.3, 3, 0x38bdf8, 0x334155);
    gridHelper.position.y = 0.012;
    this.scene.add(gridHelper);

    // Victorian glasshouse arched window backdrop
    const backWallGeo = new THREE.PlaneGeometry(8.2, 4.5);
    const backWallMat = new THREE.MeshBasicMaterial({
      map: this.createConservatoryBackdropTexture(),
      transparent: true,
      opacity: 0.92,
    });
    const backWall = new THREE.Mesh(backWallGeo, backWallMat);
    backWall.position.set(0, 2.25, -3.05);
    this.scene.add(backWall);

    // Center round table at grid [1, 1] -> world (0, 0)
    const tableTop = new THREE.Mesh(
      new THREE.CylinderGeometry(0.68, 0.64, 0.1, 28),
      new THREE.MeshStandardMaterial({ color: '#334155', roughness: 0.28, metalness: 0.55 }),
    );
    tableTop.position.set(0, 0.72, 0);
    this.scene.add(tableTop);

    // Subtle brass trim ring on center table
    const tableRim = new THREE.Mesh(
      new THREE.TorusGeometry(0.67, 0.018, 12, 36),
      new THREE.MeshStandardMaterial({ color: '#d97706', metalness: 0.75, roughness: 0.25 }),
    );
    tableRim.rotation.x = Math.PI / 2;
    tableRim.position.set(0, 0.76, 0);
    this.scene.add(tableRim);

    const tableLeg = new THREE.Mesh(
      new THREE.CylinderGeometry(0.1, 0.22, 0.72, 16),
      new THREE.MeshStandardMaterial({ color: '#1e293b', metalness: 0.6 }),
    );
    tableLeg.position.set(0, 0.36, 0);
    this.scene.add(tableLeg);
  }

  private buildGlassShards(): THREE.Group {
    const group = new THREE.Group();
    const shardMat = new THREE.MeshStandardMaterial({
      color: '#bae6fd',
      emissive: '#0ea5e9',
      emissiveIntensity: 0.35,
      transparent: true,
      opacity: 0.82,
      roughness: 0.1,
    });
    for (let i = 0; i < 14; i++) {
      const geo = new THREE.ConeGeometry(0.06 + (i % 3) * 0.02, 0.14, 3);
      const mesh = new THREE.Mesh(geo, shardMat);
      const angle = (i / 14) * Math.PI * 2;
      const radius = 0.2 + (i % 4) * 0.16;
      mesh.position.set(Math.cos(angle) * radius, 0.04, 0.3 + Math.sin(angle) * radius);
      mesh.rotation.set(Math.PI / 2, angle, i * 0.7);
      group.add(mesh);
    }
    group.visible = false;
    return group;
  }

  private buildWeatherParticles(count: number, color: string, size: number): THREE.Points {
    const positions = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      positions[i * 3] = (Math.random() - 0.5) * 9.0;
      positions[i * 3 + 1] = Math.random() * 5.2;
      positions[i * 3 + 2] = (Math.random() - 0.5) * 7.5;
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    const mat = new THREE.PointsMaterial({
      size,
      map: createParticleTexture(color),
      transparent: true,
      opacity: 0.85,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
    });
    const points = new THREE.Points(geo, mat);
    points.visible = false;
    return points;
  }

  private getFallbackStarTexture(): THREE.CanvasTexture {
    if (!this.fallbackStarTexture) {
      this.fallbackStarTexture = createFallbackStarTexture();
    }
    return this.fallbackStarTexture;
  }

  private getFallbackPropTexture(propId: string, state: string): THREE.CanvasTexture {
    if (propId === 'red_umbrella') {
      if (state === 'open') {
        if (!this.fallbackUmbrellaOpenTexture) {
          this.fallbackUmbrellaOpenTexture = createFallbackUmbrellaTexture(true);
        }
        return this.fallbackUmbrellaOpenTexture;
      }
      if (!this.fallbackUmbrellaClosedTexture) {
        this.fallbackUmbrellaClosedTexture = createFallbackUmbrellaTexture(false);
      }
      return this.fallbackUmbrellaClosedTexture;
    }
    if (propId === 'wooden_chair' || propId.includes('chair')) {
      if (!this.fallbackChairTexture) {
        this.fallbackChairTexture = createFallbackChairTexture();
      }
      return this.fallbackChairTexture;
    }
    return this.getFallbackStarTexture();
  }

  private loadSpriteTexture(
    url: string,
    fallbackKey?: string,
    onSizeReady?: (w: number, h: number) => void,
    onFallbackApplied?: (fallback: THREE.Texture) => void,
  ): THREE.Texture {
    if (this.failedUrls.has(url)) {
      const fb = this.getFallbackStarTexture();
      this.textureCache.set(url, fb);
      return fb;
    }

    const cached = this.textureCache.get(url);
    if (cached) {
      const img = cached.image as { width?: number; height?: number } | undefined;
      if (img?.width && img?.height && onSizeReady) {
        onSizeReady(img.width, img.height);
      }
      return cached;
    }

    const tex = this.textureLoader.load(
      url,
      (loaded) => {
        loaded.colorSpace = THREE.SRGBColorSpace;
        const img = loaded.image as { width?: number; height?: number } | undefined;
        if (img?.width && img?.height && onSizeReady) {
          onSizeReady(img.width, img.height);
        }
      },
      undefined,
      () => {
        this.failedUrls.add(url);
        const fallbackTex = this.getFallbackStarTexture();
        if (fallbackKey) {
          this.textureCache.set(url, fallbackTex);
        }
        onFallbackApplied?.(fallbackTex);
      },
    );
    tex.colorSpace = THREE.SRGBColorSpace;
    this.textureCache.set(url, tex);
    return tex;
  }

  private syncActorFacingTexture(ent: SpriteEntity): void {
    const url = ent.assetUrl;
    if (!url) return;
    const baseTex = this.textureCache.get(url);
    if (!baseTex) return;

    const shouldFlip = ent.facing === 'left';
    if (ent.appliedFlipped === shouldFlip && ent.sprite.material.map) {
      return;
    }

    if (!shouldFlip) {
      ent.sprite.material.map = baseTex;
      ent.sprite.material.needsUpdate = true;
      ent.appliedFlipped = false;
      return;
    }

    let flippedTex = this.flippedTextureCache.get(url);
    if (!flippedTex) {
      flippedTex = baseTex.clone();
      flippedTex.colorSpace = THREE.SRGBColorSpace;
      flippedTex.wrapS = THREE.RepeatWrapping;
      flippedTex.repeat.set(-1, 1);
      flippedTex.offset.set(1, 0);
      flippedTex.needsUpdate = true;
      this.flippedTextureCache.set(url, flippedTex);
    } else if (baseTex.image && flippedTex.image !== baseTex.image) {
      flippedTex.image = baseTex.image;
      flippedTex.needsUpdate = true;
    }

    ent.sprite.material.map = flippedTex;
    ent.sprite.material.needsUpdate = true;
    ent.appliedFlipped = true;
  }

  private fitScaleToBox(imgW: number, imgH: number, maxW: number, maxH: number): [number, number] {
    if (!imgW || !imgH) return [maxW, maxH];
    const ratio = imgW / imgH;
    let w = maxH * ratio;
    let h = maxH;
    if (w > maxW) {
      w = maxW;
      h = maxW / ratio;
    }
    return [Number(w.toFixed(3)), Number(h.toFixed(3))];
  }

  /**
   * Apply a `Scene` state and `props_registry` onto the 2.5D Three.js stage.
   * When `baselineScene` is provided and `sceneData.needs_rerender` is true,
   * actors whose `action` or `holding_prop` changed display a warm-gold floor diff halo.
   */
  public applyScene(
    sceneData: Scene,
    propsRegistry: Record<string, PropRegistryItem>,
    baselineScene?: Scene | null,
  ): void {
    this.currentLighting = sceneData.lighting;
    this.cameraShake = Boolean(sceneData.camera_shake);
    this.applyLightingPreset(sceneData.lighting);

    const baselineByActorId = new Map(
      (baselineScene?.actors ?? []).map((a) => [a.id, a]),
    );

    // Sync actors
    const activeActorIds = new Set<string>();
    const sumPos = new THREE.Vector3();

    for (const actor of sceneData.actors) {
      activeActorIds.add(actor.id);
      const { x, z } = gridToWorld(actor.grid);
      const isNyx = actor.id === 'nyx';
      const baseActor = baselineByActorId.get(actor.id);
      const changedFromBaseline = Boolean(
        sceneData.needs_rerender &&
          (!baseActor ||
            baseActor.action !== actor.action ||
            baseActor.holding_prop !== actor.holding_prop),
      );
      // R-24: Foot-bottom anchoring (`center = (0.5, 0)`) and optional `height_m` scaling
      const scaledH =
        typeof actor.height_m === 'number' && actor.height_m > 0
          ? Math.min(2.6, Math.max(0.35, actor.height_m * WORLD_UNITS_PER_METER))
          : isNyx
            ? 0.65
            : 2.25;
      const maxH = scaledH;
      const maxW = isNyx ? Math.max(0.68, maxH * 1.05) : Math.max(1.75, maxH * 0.78);
      const floorY = 0.015;

      let entity = this.actorEntities.get(actor.id);
      const actorYaw = facingToWorldYaw(actor.facing);
      if (!entity) {
        const spriteRef: { entity?: SpriteEntity } = {};
        const mat = new THREE.MeshBasicMaterial({
          map: this.loadSpriteTexture(actor.asset_url, undefined, (iw, ih) => {
            const fitted = this.fitScaleToBox(iw, ih, maxW, maxH);
            const loadedBase = this.textureCache.get(actor.asset_url);
            const cachedFlip = this.flippedTextureCache.get(actor.asset_url);
            if (loadedBase?.image && cachedFlip) {
              cachedFlip.image = loadedBase.image;
              cachedFlip.needsUpdate = true;
            }
            if (spriteRef.entity) {
              spriteRef.entity.baseScale = fitted;
              spriteRef.entity.targetPos.y = floorY;
              spriteRef.entity.appliedFlipped = undefined;
              this.syncActorFacingTexture(spriteRef.entity);
            }
          }),
          transparent: true,
          alphaTest: 0.12,
          side: THREE.DoubleSide,
        });
        // World-locked 3D standee plane: never billboards toward Camera A or Camera B
        const sprite = new THREE.Mesh(this.standeeGeometry, mat);
        sprite.position.set(x, floorY, z);
        sprite.rotation.order = 'YXZ';
        sprite.rotation.y = actorYaw;
        sprite.scale.set(maxW, maxH, 1);
        this.scene.add(sprite);

        // Soft floor contact shadow beneath 2.5D actor standee
        const shadowRadius = isNyx ? 0.32 : 0.56;
        const shadowMesh = new THREE.Mesh(
          new THREE.CircleGeometry(shadowRadius, 24),
          new THREE.MeshBasicMaterial({
            color: '#020617',
            transparent: true,
            opacity: 0.48,
            depthWrite: false,
          }),
        );
        shadowMesh.rotation.x = -Math.PI / 2;
        shadowMesh.position.set(x, 0.015, z);
        this.scene.add(shadowMesh);

        // W2-T7B: Warm-gold floor diff halo ring when action/holding_prop changed
        const haloRing = new THREE.Mesh(
          new THREE.RingGeometry(shadowRadius + 0.04, shadowRadius + 0.14, 32),
          new THREE.MeshBasicMaterial({
            color: '#fbbf24',
            side: THREE.DoubleSide,
            transparent: true,
            opacity: 0.78,
            depthWrite: false,
          }),
        );
        haloRing.rotation.x = -Math.PI / 2;
        haloRing.position.set(x, 0.022, z);
        haloRing.visible = this.diffHaloEnabled && changedFromBaseline;
        this.scene.add(haloRing);

        entity = {
          sprite,
          shadow: shadowMesh,
          haloRing,
          diffHighlighted: changedFromBaseline,
          baseScale: [maxW, maxH],
          targetPos: new THREE.Vector3(x, floorY, z),
          action: actor.action,
          facing: actor.facing,
          worldYaw: actorYaw,
          assetUrl: actor.asset_url,
          heightM: actor.height_m ?? null,
        };
        spriteRef.entity = entity;
        this.actorEntities.set(actor.id, entity);
        this.syncActorFacingTexture(entity);
      } else {
        entity.targetPos.set(x, floorY, z);
        entity.action = actor.action;
        entity.facing = actor.facing;
        entity.worldYaw = actorYaw;
        entity.assetUrl = actor.asset_url;
        entity.heightM = actor.height_m ?? null;
        entity.diffHighlighted = changedFromBaseline;
        if (entity.haloRing) {
          entity.haloRing.visible = this.diffHaloEnabled && changedFromBaseline;
        }
        this.syncActorFacingTexture(entity);
      }
      if (actor.action === 'catch_from_camera') {
        this.triggerCameraGrab(actor.id, 1.8);
      }
      sumPos.add(new THREE.Vector3(entity.targetPos.x, maxH * 0.45, entity.targetPos.z));
    }

    // Lock Camera B's target to the shared stage center (0, 0.9, 0) matching Camera A and `stage_blueprint.py`
    // so both views observe the exact same world coordinates purely from a +45 deg camera orbit.
    void sumPos;
    this.cameraBTarget.set(0, 0.9, 0);

    // Hide unused actors
    for (const [id, ent] of this.actorEntities.entries()) {
      const visible = activeActorIds.has(id);
      ent.sprite.visible = visible;
      if (ent.shadow) ent.shadow.visible = visible;
      if (ent.haloRing) {
        ent.haloRing.visible =
          visible && this.diffHaloEnabled && Boolean(ent.diffHighlighted);
      }
    }

    // Sync props (stage_props + any prop held by an actor, including standalone umbrella R-12)
    const activePropIds = new Set<string>();
    let anyBrokenVase = false;

    const propsToPlace: Array<{
      id: string;
      grid: [number, number];
      on_floor: boolean;
      state: string;
      entry_motion?: string;
      entry_origin?: number[] | null;
      heldByActor?: boolean;
      umbrellaOpen?: boolean;
    }> = sceneData.stage_props.map((p) => ({ ...p }));

    for (const actor of sceneData.actors) {
      if (actor.holding_prop) {
        const alreadyListed = propsToPlace.some((p) => p.id === actor.holding_prop);
        if (!alreadyListed) {
          const isUmbrella = actor.holding_prop === 'red_umbrella';
          const regState = propsRegistry[actor.holding_prop]?.state;
          const umbrellaOpen =
            isUmbrella && (regState === 'open' || actor.action === 'shared_umbrella');
          // Lia's own sprite already shows the closed umbrella in her hand; adding the prop too drew it twice.
          if (isUmbrella && !umbrellaOpen && actor.id === 'lia') continue;
          propsToPlace.push({
            id: actor.holding_prop,
            grid: [
              isUmbrella
                ? Math.max(0, actor.grid[0] - 0.16)
                : Math.min(2, actor.grid[0] + 0.18),
              actor.grid[1],
            ],
            on_floor: false,
            state: isUmbrella ? (umbrellaOpen ? 'open' : 'closed') : 'held',
            heldByActor: true,
            umbrellaOpen,
          });
        }
      }
    }

    // W2-T8F: Track non-vase props sharing the center table so they never overlap `crystal_vase`
    const hasTableVase = propsToPlace.some(
      (p) => p.id === 'crystal_vase' && !p.on_floor && !p.heldByActor,
    );
    const tableOffsets: Array<[number, number]> = [
      [0.26, 0.1],
      [-0.26, 0.12],
      [0.22, -0.16],
      [-0.22, -0.16],
    ];
    let extraTablePropIdx = 0;

    for (const prop of propsToPlace) {
      activePropIds.add(prop.id);

      const regItem = propsRegistry[prop.id];
      const isUmbrella = prop.id === 'red_umbrella';
      const isChair = prop.id === 'wooden_chair' || prop.id.includes('chair');
      const isBroken = prop.state === 'broken';
      const umbrellaVariant =
        prop.umbrellaOpen || prop.state === 'open' ? 'open' : 'closed';

      // R-12: Resolve standalone umbrella/chair PNGs dynamically (never draw Lia's standee as umbrella)
      const rawBaseUrl = regItem?.image_url ?? `${PROP_ASSET_ROOT}/${prop.id}.png`;
      const baseImageUrl =
        isUmbrella && rawBaseUrl.includes('/actors/lia')
          ? `${PROP_ASSET_ROOT}/red_umbrella_${umbrellaVariant}.png`
          : isChair
            ? `${PROP_ASSET_ROOT}/wooden_chair.png`
            : rawBaseUrl;
      const preferredUrl =
        prop.id === 'crystal_vase' && isBroken
          ? '/assets/props/crystal_vase_broken.png'
          : isUmbrella
            ? `${PROP_ASSET_ROOT}/red_umbrella_${umbrellaVariant}.png`
            : baseImageUrl;

      const worldCoords = gridToWorld(prop.grid);
      let x = worldCoords.x;
      let z = worldCoords.z;
      const nearCenterTable =
        !prop.on_floor &&
        !prop.heldByActor &&
        !isChair &&
        Math.abs(prop.grid[0] - 1) < 0.35 &&
        Math.abs(prop.grid[1] - 1) < 0.35;
      if (hasTableVase && nearCenterTable && prop.id !== 'crystal_vase') {
        const [ox, oz] = tableOffsets[extraTablePropIdx % tableOffsets.length];
        x += ox;
        z += oz;
        extraTablePropIdx += 1;
      }
      if (prop.id === 'crystal_vase' && isBroken) {
        anyBrokenVase = true;
      }

      // R-24: Foot-bottom anchoring (`center=(0.5, 0)`) + optional `height_m` scaling
      const customH =
        typeof regItem?.height_m === 'number' && regItem.height_m > 0
          ? Math.min(1.8, Math.max(0.24, regItem.height_m * WORLD_UNITS_PER_METER))
          : null;
      const maxPropH =
        customH ??
        (isChair
          ? 0.92
          : isUmbrella
            ? umbrellaVariant === 'open'
              ? 1.05
              : 0.85
            : prop.id === 'crystal_vase'
              ? 0.46
              : prop.heldByActor
                ? 0.34
                : 0.40);
      const maxPropW = isChair
        ? maxPropH * 0.9
        : isUmbrella && umbrellaVariant === 'open'
          ? maxPropH * 1.15
          : maxPropH * 0.95;

      const y = prop.heldByActor
        ? isUmbrella
          ? umbrellaVariant === 'open'
            ? 1.25
            : 0.14
          : 0.86
        : prop.on_floor || isChair
          ? 0.015
          : 0.77;

      let entity = this.propEntities.get(prop.id);
      const rescalePropToBox = (ent: SpriteEntity, tex?: THREE.Texture | null) => {
        const img = tex?.image as { width?: number; height?: number } | undefined;
        const fitted: [number, number] =
          img?.width && img?.height
            ? this.fitScaleToBox(img.width, img.height, maxPropW, maxPropH)
            : [maxPropW, maxPropH];
        ent.baseScale = fitted;
        ent.sprite.scale.set(fitted[0], fitted[1], 1);
      };

      const applyPropTexture = (ent: SpriteEntity, targetUrl: string, fallbackUrl: string) => {
        const effectiveUrl = this.failedUrls.has(targetUrl) ? fallbackUrl : targetUrl;
        if (ent.currentTextureUrl === effectiveUrl) {
          rescalePropToBox(ent, ent.sprite.material.map);
          return;
        }
        ent.currentTextureUrl = effectiveUrl;
        ent.usingBrokenAsset = false;

        if (this.failedUrls.has(effectiveUrl)) {
          const fbTex = this.getFallbackPropTexture(prop.id, prop.state);
          ent.sprite.material.map = fbTex;
          ent.sprite.material.needsUpdate = true;
          rescalePropToBox(ent, fbTex);
          return;
        }

        const cached = this.textureCache.get(effectiveUrl);
        if (cached) {
          ent.sprite.material.map = cached;
          ent.sprite.material.needsUpdate = true;
          rescalePropToBox(ent, cached);
          if (effectiveUrl.includes('crystal_vase_broken')) {
            ent.usingBrokenAsset = true;
            ent.sprite.material.opacity = 1.0;
            ent.sprite.rotation.z = 0;
          }
          return;
        }

        const tex = this.textureLoader.load(
          effectiveUrl,
          (loaded) => {
            loaded.colorSpace = THREE.SRGBColorSpace;
            this.textureCache.set(effectiveUrl, loaded);
            if (ent.currentTextureUrl === effectiveUrl) {
              ent.sprite.material.map = loaded;
              ent.sprite.material.needsUpdate = true;
              const img = loaded.image as { width?: number; height?: number } | undefined;
              if (img?.width && img?.height) {
                ent.baseScale = this.fitScaleToBox(img.width, img.height, maxPropW, maxPropH);
              }
              if (effectiveUrl.includes('crystal_vase_broken')) {
                ent.usingBrokenAsset = true;
                ent.sprite.material.opacity = 1.0;
                ent.sprite.rotation.z = 0;
              }
            }
          },
          undefined,
          () => {
            this.failedUrls.add(effectiveUrl);
            if (effectiveUrl !== fallbackUrl && !this.failedUrls.has(fallbackUrl)) {
              ent.usingBrokenAsset = false;
              ent.currentTextureUrl = fallbackUrl;
              const fbTex = this.loadSpriteTexture(
                fallbackUrl,
                prop.id,
                (iw, ih) => {
                  ent.baseScale = this.fitScaleToBox(iw, ih, maxPropW, maxPropH);
                },
                () => {
                  const proceduralFb = this.getFallbackPropTexture(prop.id, prop.state);
                  ent.sprite.material.map = proceduralFb;
                  ent.sprite.material.needsUpdate = true;
                },
              );
              ent.sprite.material.map = fbTex;
              ent.sprite.material.needsUpdate = true;
              ent.sprite.material.opacity = isBroken ? 0.55 : 1.0;
              ent.sprite.rotation.z = isBroken ? 0.65 : 0;
            } else {
              const proceduralFb = this.getFallbackPropTexture(prop.id, prop.state);
              this.textureCache.set(effectiveUrl, proceduralFb);
              ent.sprite.material.map = proceduralFb;
              ent.sprite.material.needsUpdate = true;
              rescalePropToBox(ent, proceduralFb);
            }
          },
        );
        tex.colorSpace = THREE.SRGBColorSpace;
        ent.sprite.material.map = tex;
        ent.sprite.material.needsUpdate = true;
      };

      if (!entity) {
        const mat = new THREE.MeshBasicMaterial({
          map: this.getFallbackPropTexture(prop.id, prop.state),
          transparent: true,
          alphaTest: 0.12,
          side: THREE.DoubleSide,
        });
        // World-locked 3D crossed double-card prop standee: stays rigid in 3D space (no billboarding)
        // and preserves solid volume from both Camera A (0 deg) and Camera B (+45 deg) or 360 deg orbit.
        const sprite = new THREE.Mesh(this.standeeGeometry, mat);
        sprite.position.set(x, y, z);
        sprite.rotation.order = 'YXZ';
        sprite.scale.set(maxPropW, maxPropH, 1);
        const crossCard = new THREE.Mesh(this.standeeGeometry, mat);
        crossCard.rotation.y = Math.PI / 2;
        sprite.add(crossCard);
        this.scene.add(sprite);
        entity = {
          sprite,
          crossCard,
          baseScale: [maxPropW, maxPropH],
          targetPos: new THREE.Vector3(x, y, z),
          action: prop.state,
          facing: 'front',
          worldYaw: 0,
          broken: isBroken,
          heightM: regItem?.height_m ?? null,
        };
        this.propEntities.set(prop.id, entity);
        applyPropTexture(entity, preferredUrl, baseImageUrl);
      } else {
        entity.targetPos.set(x, y, z);
        entity.action = prop.state;
        entity.broken = isBroken;
        entity.heightM = regItem?.height_m ?? null;
        applyPropTexture(entity, preferredUrl, baseImageUrl);
      }

      if (isBroken && !entity.usingBrokenAsset) {
        entity.sprite.material.opacity = 0.55;
        entity.sprite.rotation.z = 0.65;
      } else {
        entity.sprite.material.opacity = 1.0;
        entity.sprite.rotation.z = 0;
      }

      // W2-V2-03: Auto-trigger 3D parabolic throw-in arc once per scene for thrown props
      if (prop.entry_motion === 'throw_arc') {
        const throwKey = `${sceneData.scene_id}:${prop.id}`;
        if (!this.seenThrownPropKeys.has(throwKey)) {
          this.seenThrownPropKeys.add(throwKey);
          this.spawnThrownPropAnimation(prop.id, prop.entry_origin, prop.grid);
        }
      }
    }

    for (const [id, ent] of this.propEntities.entries()) {
      ent.sprite.visible = activePropIds.has(id);
    }

    this.glassShardsGroup.visible = anyBrokenVase;
  }

  private applyLightingPreset(preset: LightingPreset): void {
    this.rainPoints.visible = preset === 'cold_blue';
    this.snowPoints.visible = preset === 'warm_gold_snow';

    switch (preset) {
      case 'warm_dim':
        this.targetBgColor.set('#0c101d');
        this.targetAmbientColor.set('#cbd5e1');
        this.targetAmbientIntensity = 0.8;
        this.targetSpotColor.set('#fde68a');
        this.targetSpotIntensity = 26;
        this.targetRimColor.set('#60a5fa');
        break;
      case 'cold_blue':
        this.targetBgColor.set('#050b18');
        this.targetAmbientColor.set('#60a5fa');
        this.targetAmbientIntensity = 0.65;
        this.targetSpotColor.set('#93c5fd');
        this.targetSpotIntensity = 22;
        this.targetRimColor.set('#38bdf8');
        break;
      case 'warm_transition':
        this.targetBgColor.set('#131122');
        this.targetAmbientColor.set('#fcd34d');
        this.targetAmbientIntensity = 0.95;
        this.targetSpotColor.set('#fef08a');
        this.targetSpotIntensity = 32;
        this.targetRimColor.set('#f59e0b');
        break;
      case 'warm_gold_snow':
        this.targetBgColor.set('#1a1423');
        this.targetAmbientColor.set('#fde68a');
        this.targetAmbientIntensity = 1.1;
        this.targetSpotColor.set('#fbbf24');
        this.targetSpotIntensity = 38;
        this.targetRimColor.set('#f472b6');
        break;
    }
  }

  /**
   * Animate the protagonist (`lia`) reaching forward toward the front camera with
   * a warm golden catch ripple (`catch_from_camera`, W2-V2-03).
   */
  public triggerCameraGrab(actorId = 'lia', durationSec = 1.6): void {
    this.sculptureFrozen = false;
    this.cameraGrabState = {
      actorId,
      startedAt: this.animElapsed,
      durationSec,
    };
  }

  /**
   * Fly a newly captured physical prop along a smooth 3D parabolic arc from the
   * viewer's front camera (`z = +2.6`) straight into Lia's hand or tabletop (W2-V2-03).
   */
  public spawnThrownPropAnimation(
    propId: string,
    entryOrigin?: number[] | null,
    targetGrid?: [number, number] | null,
  ): void {
    this.sculptureFrozen = false;
    const ent = this.propEntities.get(propId);
    const startPos =
      entryOrigin && entryOrigin.length >= 3
        ? new THREE.Vector3(
            (entryOrigin[0] - 1) * 1.4,
            Math.max(1.1, Math.min(2.2, entryOrigin[1])),
            Math.max(2.0, entryOrigin[2] + 1.8),
          )
        : new THREE.Vector3(0.35, 1.45, 2.65);

    let endPos: THREE.Vector3;
    if (ent) {
      endPos = ent.targetPos.clone();
      ent.sprite.position.copy(startPos);
      ent.sprite.visible = true;
    } else if (targetGrid) {
      const { x, z } = gridToWorld(targetGrid);
      endPos = new THREE.Vector3(x, 0.85, z);
    } else {
      endPos = new THREE.Vector3(0, 0.85, 0.1);
    }

    this.activePropFlights.set(propId, {
      startPos,
      endPos,
      peakHeight: 1.1,
      startedAt: this.animElapsed,
      durationSec: 1.15,
    });
    this.triggerCameraGrab('lia', 1.55);
  }

  /**
   * Freeze or unfreeze the living stage into a moonlit silver/alabaster sculpture
   * tableau (`setSculptureFrozen`, W2-V2-03).
   */
  public setSculptureFrozen(frozen: boolean): void {
    if (this.sculptureFrozen === frozen) return;
    this.sculptureFrozen = frozen;
    if (frozen) {
      this.frozenElapsed = this.animElapsed;
    }
  }

  public isSculptureFrozen(): boolean {
    return this.sculptureFrozen;
  }

  private updateLighting(dt: number): void {
    const t = Math.min(1, dt * 4.5);
    const desiredBlend =
      this.sculptureFrozen && this.activePropFlights.size === 0 && !this.cameraGrabState ? 1 : 0;
    this.sculptureTintBlend += (desiredBlend - this.sculptureTintBlend) * Math.min(1, dt * 3.8);

    if (this.scene.background instanceof THREE.Color) {
      this.scene.background.lerp(this.targetBgColor, t);
    }
    if (this.scene.fog instanceof THREE.FogExp2) {
      this.scene.fog.color.lerp(this.targetBgColor, t);
    }
    const blendedAmbient = this.targetAmbientColor
      .clone()
      .lerp(this.colorSculptureAmbient, this.sculptureTintBlend * 0.72);
    const blendedSpot = this.targetSpotColor
      .clone()
      .lerp(this.colorSculptureSpot, this.sculptureTintBlend * 0.75);

    this.ambientLight.color.lerp(blendedAmbient, t);
    this.ambientLight.intensity += (this.targetAmbientIntensity - this.ambientLight.intensity) * t;
    this.spotLight.color.lerp(blendedSpot, t);
    this.spotLight.intensity += (this.targetSpotIntensity - this.spotLight.intensity) * t;
    this.rimLight.color.lerp(this.targetRimColor, t);
  }

  private startLoop(): void {
    const tick = () => {
      if (this.disposed || this.contextLost) return;
      this.animFrameId = requestAnimationFrame(tick);
      if (typeof document !== 'undefined' && document.hidden) {
        this.clock.getDelta();
        return;
      }
      const dt = Math.min(this.clock.getDelta(), 0.05);
      const isEffectivelyFrozen =
        this.sculptureFrozen && this.activePropFlights.size === 0 && !this.cameraGrabState;
      if (!isEffectivelyFrozen) {
        this.animElapsed += dt;
      }
      const elapsed = isEffectivelyFrozen ? this.frozenElapsed : this.animElapsed;

      this.updateLighting(dt);
      this.updateEntities(dt, elapsed, isEffectivelyFrozen);
      if (!isEffectivelyFrozen) {
        this.updateWeather(dt, elapsed);
      }
      this.updateCameras(dt, elapsed, isEffectivelyFrozen);

      this.rendererA.render(this.scene, this.cameraA);
      this.rendererB.render(this.scene, this.cameraB);
    };
    tick();
  }

  private updateEntities(dt: number, elapsed: number, frozen: boolean): void {
    const lerpFactor = Math.min(1, dt * 6.5);
    const spriteTint = this.colorWhite.clone().lerp(this.colorSculpture, this.sculptureTintBlend);
    let showCatchRipple = false;

    for (const [id, ent] of this.actorEntities.entries()) {
      if (!ent.sprite.visible) continue;
      ent.sprite.material.color.copy(spriteTint);

      const [w, h] = ent.baseScale;
      this.syncActorFacingTexture(ent);

      // Compute optional `catch_from_camera` forward reach surge toward Camera A
      let grabSurge = 0;
      if (this.cameraGrabState && this.cameraGrabState.actorId === id) {
        const progress = (elapsed - this.cameraGrabState.startedAt) / this.cameraGrabState.durationSec;
        if (progress >= 1) {
          this.cameraGrabState = null;
        } else if (progress >= 0) {
          grabSurge = Math.sin(progress * Math.PI);
        }
      } else if (ent.action === 'catch_from_camera' && !frozen) {
        grabSurge = 0.45 + Math.sin(elapsed * 3.4) * 0.35;
      }

      const desiredPos = ent.targetPos.clone();
      if (grabSurge > 0) {
        desiredPos.z += grabSurge * 1.15;
      }
      if (!frozen) {
        ent.sprite.position.lerp(desiredPos, lerpFactor);
      }

      let scaleW = w;
      let scaleY = h;
      let offsetY = frozen ? 0 : Math.sin(elapsed * 2.4 + (id === 'nyx' ? 1.4 : 0)) * 0.025;

      if (ent.action === 'kneel' || ent.action === 'crouch') {
        scaleY = h * 0.74;
        offsetY = 0;
      } else if (ent.action === 'jump' && !frozen) {
        offsetY += Math.abs(Math.sin(elapsed * 5.2)) * 0.42;
      } else if (ent.action === 'flee' && !frozen) {
        offsetY += Math.abs(Math.sin(elapsed * 9.0)) * 0.1;
      } else if ((ent.action === 'nuzzle' || ent.action === 'shared_umbrella') && !frozen) {
        offsetY += Math.sin(elapsed * 3.2) * 0.035;
      } else if ((ent.action === 'drink' || ent.action === 'eat' || ent.action === 'hold_up') && !frozen) {
        offsetY += Math.sin(elapsed * 4.2) * 0.04;
      }

      if (grabSurge > 0) {
        const swell = 1 + grabSurge * 0.16;
        scaleW = w * swell;
        scaleY = scaleY * swell;
        ent.sprite.rotation.z = -grabSurge * 0.07;
        showCatchRipple = true;
        this.catchRippleMesh.position.set(
          ent.sprite.position.x,
          ent.sprite.position.y + scaleY * 0.56,
          ent.sprite.position.z + 0.38,
        );
        const rippleScale = 0.75 + grabSurge * 0.9;
        this.catchRippleMesh.scale.set(rippleScale, rippleScale, 1);
        (this.catchRippleMesh.material as THREE.MeshBasicMaterial).opacity =
          0.35 + grabSurge * 0.55;
      } else {
        ent.sprite.rotation.z = 0;
      }
      // Smoothly converge rigid world-space facing yaw (independent of which camera renders)
      ent.sprite.rotation.y += (ent.worldYaw - ent.sprite.rotation.y) * lerpFactor;

      ent.sprite.position.y = ent.targetPos.y + Math.max(0, offsetY);
      // Keep positive scale.x in Three.js r170 (horizontal flip handled via UV-mirrored texture)
      ent.sprite.scale.set(scaleW, scaleY, 1);
      if (ent.shadow) {
        ent.shadow.position.x = ent.sprite.position.x;
        ent.shadow.position.z = ent.sprite.position.z;
      }
      if (ent.haloRing && ent.haloRing.visible) {
        ent.haloRing.position.x = ent.sprite.position.x;
        ent.haloRing.position.z = ent.sprite.position.z;
        const pulse = frozen ? 1 : 1 + Math.sin(elapsed * 4.2) * 0.07;
        ent.haloRing.scale.set(pulse, pulse, 1);
        const haloMat = ent.haloRing.material as THREE.MeshBasicMaterial;
        haloMat.opacity = frozen ? 0.4 : 0.55 + Math.sin(elapsed * 4.2) * 0.25;
      }
    }
    this.catchRippleMesh.visible = showCatchRipple;

    for (const [propId, ent] of this.propEntities.entries()) {
      if (!ent.sprite.visible) continue;
      ent.sprite.material.color.copy(spriteTint);

      const flight = this.activePropFlights.get(propId);
      if (flight) {
        const rawT = (elapsed - flight.startedAt) / flight.durationSec;
        const t = Math.min(1, Math.max(0, rawT));
        const eased = 1 - Math.pow(1 - t, 2);
        const px = flight.startPos.x + (flight.endPos.x - flight.startPos.x) * eased;
        const pz = flight.startPos.z + (flight.endPos.z - flight.startPos.z) * eased;
        const baseY = flight.startPos.y + (flight.endPos.y - flight.startPos.y) * eased;
        const arcY = 4 * flight.peakHeight * t * (1 - t);
        ent.sprite.position.set(px, baseY + arcY, pz);
        const airScale = 1 + (1 - t) * 0.42;
        ent.sprite.scale.set(ent.baseScale[0] * airScale, ent.baseScale[1] * airScale, 1);
        ent.sprite.rotation.z = (1 - t) * Math.PI * 2.0;
        if (rawT >= 1) {
          this.activePropFlights.delete(propId);
          ent.sprite.rotation.z = ent.broken && !ent.usingBrokenAsset ? 0.65 : 0;
        }
        continue;
      }

      if (!frozen) {
        ent.sprite.position.lerp(ent.targetPos, lerpFactor);
      }
      if (ent.action === 'glowing' && !frozen) {
        const pulse = 1 + Math.sin(elapsed * 4) * 0.08;
        ent.sprite.scale.set(ent.baseScale[0] * pulse, ent.baseScale[1] * pulse, 1);
        ent.sprite.position.y = ent.targetPos.y + Math.sin(elapsed * 2.6) * 0.05;
      } else {
        const sx = ent.sprite.scale.x + (ent.baseScale[0] - ent.sprite.scale.x) * lerpFactor;
        const sy = ent.sprite.scale.y + (ent.baseScale[1] - ent.sprite.scale.y) * lerpFactor;
        ent.sprite.scale.set(sx, sy, 1);
        ent.sprite.position.y = ent.targetPos.y;
      }
    }
  }

  private updateWeather(dt: number, elapsed: number): void {
    if (this.currentLighting === 'cold_blue' && this.rainPoints.visible) {
      const attr = this.rainPoints.geometry.getAttribute('position') as THREE.BufferAttribute;
      for (let i = 0; i < attr.count; i++) {
        let y = attr.getY(i) - dt * 7.5;
        let x = attr.getX(i) - dt * 1.1;
        if (y < 0) {
          y = 5.0;
          x = (Math.random() - 0.5) * 9.0;
        }
        attr.setXY(i, x, y);
      }
      attr.needsUpdate = true;
    }

    if (this.currentLighting === 'warm_gold_snow' && this.snowPoints.visible) {
      const attr = this.snowPoints.geometry.getAttribute('position') as THREE.BufferAttribute;
      for (let i = 0; i < attr.count; i++) {
        let y = attr.getY(i) - dt * 0.85;
        let x = attr.getX(i) + Math.sin(elapsed * 1.5 + i) * dt * 0.35;
        if (y < 0) {
          y = 5.0;
          x = (Math.random() - 0.5) * 9.0;
        }
        attr.setXY(i, x, y);
      }
      attr.needsUpdate = true;
    }
  }

  private updateCameras(dt: number, elapsed: number, frozen = false): void {
    const shakeX = this.cameraShake && !frozen ? Math.sin(elapsed * 38) * 0.08 : 0;
    const shakeY = this.cameraShake && !frozen ? Math.cos(elapsed * 43) * 0.06 : 0;

    this.placeCamera(this.cameraA, shakeX, 2.75 + shakeY, 6.2, this.cameraATarget);

    this.currentCameraBTarget.lerp(this.cameraBTarget, Math.min(1, dt * 5.0));
    this.placeCamera(this.cameraB, 3.25 + shakeX * 0.8, 1.95 + shakeY * 0.8, 3.55, this.currentCameraBTarget);
  }

  /**
   * Capture start+end PNG keyframes and attempt 4s `.webm` `captureStream()` export
   * for both Camera A & B simultaneously. Falls back cleanly to `png_fallback` if
   * MediaRecorder/captureStream is unavailable or times out.
   */
  public async exportScenePreview(durationMs = 4000): Promise<PreviewExportResult> {
    const startTime = performance.now();
    if (this.orbitYaw !== 0 || this.orbitPitch !== 0 || this.orbitZoom !== 1) {
      // The renderer must receive the two fixed camera views, not a view the user dragged to.
      this.resetOrbit();
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    }
    const camAStartPng = this.canvasA.toDataURL('image/png');
    const camBStartPng = this.canvasB.toDataURL('image/png');

    const mime = getSupportedWebmMime();
    const canStream =
      typeof this.canvasA.captureStream === 'function' &&
      typeof this.canvasB.captureStream === 'function' &&
      mime !== null;

    if (!canStream) {
      await new Promise((r) => setTimeout(r, 350));
      return {
        mode: 'png_fallback',
        camABlob: null,
        camBBlob: null,
        camAStartPng,
        camAEndPng: this.canvasA.toDataURL('image/png'),
        camBStartPng,
        camBEndPng: this.canvasB.toDataURL('image/png'),
        elapsedMs: Math.round(performance.now() - startTime),
      };
    }

    try {
      const streamA = this.canvasA.captureStream(30);
      const streamB = this.canvasB.captureStream(30);

      const recordCanvas = (stream: MediaStream) =>
        new Promise<Blob>((resolve, reject) => {
          const chunks: BlobPart[] = [];
          const recorder = new MediaRecorder(stream, { mimeType: mime! });
          recorder.ondataavailable = (ev) => {
            if (ev.data.size > 0) chunks.push(ev.data);
          };
          recorder.onstop = () => {
            resolve(new Blob(chunks, { type: mime! }));
          };
          recorder.onerror = (err) => reject(err);
          recorder.start();
          setTimeout(() => {
            if (recorder.state !== 'inactive') recorder.stop();
          }, durationMs);
        });

      const timeoutPromise = new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error('captureStream timeout')), durationMs + 2500),
      );

      const [camABlob, camBBlob] = await Promise.race([
        Promise.all([recordCanvas(streamA), recordCanvas(streamB)]),
        timeoutPromise,
      ]);

      return {
        mode: 'webm',
        camABlob,
        camBBlob,
        camAStartPng,
        camAEndPng: this.canvasA.toDataURL('image/png'),
        camBStartPng,
        camBEndPng: this.canvasB.toDataURL('image/png'),
        elapsedMs: Math.round(performance.now() - startTime),
      };
    } catch {
      return {
        mode: 'png_fallback',
        camABlob: null,
        camBBlob: null,
        camAStartPng,
        camAEndPng: this.canvasA.toDataURL('image/png'),
        camBStartPng,
        camBEndPng: this.canvasB.toDataURL('image/png'),
        elapsedMs: Math.round(performance.now() - startTime),
      };
    }
  }

  /**
   * Toggle the warm-gold actor diff halo ring for forked causal scenes (W2-T7B).
   */
  public setDiffHaloEnabled(enabled: boolean): void {
    this.diffHaloEnabled = enabled;
    for (const ent of this.actorEntities.values()) {
      if (ent.haloRing) {
        ent.haloRing.visible =
          ent.sprite.visible && enabled && Boolean(ent.diffHighlighted);
      }
    }
  }

  /**
   * Toggle low-resolution upscaled Pixel-Art preview mode (`160x90` crisp pixelated).
   */
  public setPixelArtMode(enabled: boolean): void {
    if (this.pixelArtMode === enabled) return;
    this.pixelArtMode = enabled;
    const width = enabled ? 160 : 640;
    const height = enabled ? 90 : 360;
    const dpr = enabled ? 1 : Math.min(window.devicePixelRatio, 2);

    this.rendererA.setPixelRatio(dpr);
    this.rendererA.setSize(width, height, false);
    this.rendererB.setPixelRatio(dpr);
    this.rendererB.setSize(width, height, false);

    const rendering = enabled ? 'pixelated' : 'auto';
    this.canvasA.style.imageRendering = rendering;
    this.canvasB.style.imageRendering = rendering;
  }

  public dispose(): void {
    this.disposed = true;
    for (const canvas of [this.canvasA, this.canvasB]) {
      canvas.removeEventListener('pointerdown', this.handleOrbitDown);
      canvas.removeEventListener('pointermove', this.handleOrbitMove);
      canvas.removeEventListener('pointerup', this.handleOrbitUp);
      canvas.removeEventListener('pointercancel', this.handleOrbitUp);
      canvas.removeEventListener('wheel', this.handleOrbitWheel);
      canvas.removeEventListener('dblclick', this.resetOrbit);
    }
    this.canvasA.removeEventListener('webglcontextlost', this.handleContextLost);
    this.canvasB.removeEventListener('webglcontextlost', this.handleContextLost);
    this.canvasA.removeEventListener('webglcontextrestored', this.handleContextRestored);
    this.canvasB.removeEventListener('webglcontextrestored', this.handleContextRestored);
    if (this.animFrameId !== null) {
      cancelAnimationFrame(this.animFrameId);
      this.animFrameId = null;
    }

    // W2-T10C: Dispose all scene geometries, materials, and attached procedural textures cleanly
    const disposedTextures = new Set<THREE.Texture>();
    const disposeMaterial = (mat: THREE.Material) => {
      const m = mat as THREE.Material & { map?: THREE.Texture | null };
      if (m.map && !disposedTextures.has(m.map)) {
        disposedTextures.add(m.map);
        m.map.dispose();
      }
      mat.dispose();
    };

    this.scene.traverse((obj) => {
      const renderable = obj as THREE.Object3D & {
        geometry?: THREE.BufferGeometry;
        material?: THREE.Material | THREE.Material[];
      };
      if (renderable.geometry) {
        renderable.geometry.dispose();
      }
      if (renderable.material) {
        if (Array.isArray(renderable.material)) {
          renderable.material.forEach(disposeMaterial);
        } else {
          disposeMaterial(renderable.material);
        }
      }
    });

    for (const tex of this.textureCache.values()) {
      if (!disposedTextures.has(tex)) {
        disposedTextures.add(tex);
        tex.dispose();
      }
    }
    this.textureCache.clear();

    for (const tex of this.flippedTextureCache.values()) {
      if (!disposedTextures.has(tex)) {
        disposedTextures.add(tex);
        tex.dispose();
      }
    }
    this.flippedTextureCache.clear();

    const fallbacks = [
      this.fallbackStarTexture,
      this.fallbackUmbrellaClosedTexture,
      this.fallbackUmbrellaOpenTexture,
      this.fallbackChairTexture,
    ];
    for (const fb of fallbacks) {
      if (fb && !disposedTextures.has(fb)) {
        disposedTextures.add(fb);
        fb.dispose();
      }
    }
    this.fallbackStarTexture = null;
    this.fallbackUmbrellaClosedTexture = null;
    this.fallbackUmbrellaOpenTexture = null;
    this.fallbackChairTexture = null;
    this.activePropFlights.clear();
    this.seenThrownPropKeys.clear();
    this.cameraGrabState = null;

    this.actorEntities.clear();
    this.propEntities.clear();
    this.scene.clear();

    this.rendererA.dispose();
    this.rendererB.dispose();
  }
}
