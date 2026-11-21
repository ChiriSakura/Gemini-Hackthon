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
  sprite: THREE.Sprite;
  shadow?: THREE.Mesh;
  haloRing?: THREE.Mesh;
  diffHighlighted?: boolean;
  baseScale: [number, number];
  targetPos: THREE.Vector3;
  action: string;
  facing: string;
  assetUrl?: string;
  appliedFlipped?: boolean;
  broken?: boolean;
  currentTextureUrl?: string;
  usingBrokenAsset?: boolean;
}

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
  private actorEntities = new Map<string, SpriteEntity>();
  private propEntities = new Map<string, SpriteEntity>();

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
  private contextLostHandler = (ev: Event) => {
    ev.preventDefault();
  };
  private animFrameId: number | null = null;
  private clock = new THREE.Clock();
  private disposed = false;
  private pixelArtMode = false;
  private diffHaloEnabled = true;

  /**
   * Initialize the shared 3D world and attach two WebGLRenderers for Camera A & B.
   */
  constructor(canvasA: HTMLCanvasElement, canvasB: HTMLCanvasElement) {
    this.canvasA = canvasA;
    this.canvasB = canvasB;

    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color('#090d18');
    this.scene.fog = new THREE.FogExp2('#090d18', 0.055);

    this.cameraA = new THREE.PerspectiveCamera(44, 16 / 9, 0.1, 50);
    this.cameraA.position.set(0, 2.75, 6.2);
    this.cameraA.lookAt(0, 0.9, 0);

    this.cameraB = new THREE.PerspectiveCamera(36, 16 / 9, 0.1, 50);
    this.cameraB.position.set(3.25, 1.95, 3.55);
    this.cameraB.lookAt(this.cameraBTarget);

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

    this.canvasA.addEventListener('webglcontextlost', this.contextLostHandler);
    this.canvasB.addEventListener('webglcontextlost', this.contextLostHandler);

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
      // Keep Nyx realistically cat-sized (~29% of Lia's 2.25 height)
      const maxW = isNyx ? 0.68 : 1.75;
      const maxH = isNyx ? 0.65 : 2.25;
      const y = maxH / 2 + 0.02;

      let entity = this.actorEntities.get(actor.id);
      if (!entity) {
        const spriteRef: { entity?: SpriteEntity } = {};
        const mat = new THREE.SpriteMaterial({
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
              spriteRef.entity.targetPos.y = fitted[1] / 2 + 0.02;
              spriteRef.entity.appliedFlipped = undefined;
              this.syncActorFacingTexture(spriteRef.entity);
            }
          }),
          transparent: true,
        });
        const sprite = new THREE.Sprite(mat);
        sprite.position.set(x, y, z);
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
          targetPos: new THREE.Vector3(x, y, z),
          action: actor.action,
          facing: actor.facing,
          assetUrl: actor.asset_url,
        };
        spriteRef.entity = entity;
        this.actorEntities.set(actor.id, entity);
        this.syncActorFacingTexture(entity);
      } else {
        entity.targetPos.set(x, entity.baseScale[1] / 2 + 0.02, z);
        entity.action = actor.action;
        entity.facing = actor.facing;
        entity.assetUrl = actor.asset_url;
        entity.diffHighlighted = changedFromBaseline;
        if (entity.haloRing) {
          entity.haloRing.visible = this.diffHaloEnabled && changedFromBaseline;
        }
        this.syncActorFacingTexture(entity);
      }
      sumPos.add(entity.targetPos);
    }

    if (sceneData.actors.length > 0) {
      sumPos.divideScalar(sceneData.actors.length);
      this.cameraBTarget.set(sumPos.x, 0.92, sumPos.z);
    }

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

    // Sync props (stage_props + any non-umbrella prop currently held by an actor)
    const activePropIds = new Set<string>();
    let anyBrokenVase = false;

    const propsToPlace: Array<{
      id: string;
      grid: [number, number];
      on_floor: boolean;
      state: string;
      heldByActor?: boolean;
    }> = [...sceneData.stage_props];

    for (const actor of sceneData.actors) {
      if (actor.holding_prop && actor.holding_prop !== 'red_umbrella') {
        const alreadyListed = propsToPlace.some((p) => p.id === actor.holding_prop);
        if (!alreadyListed) {
          propsToPlace.push({
            id: actor.holding_prop,
            grid: [Math.min(2, actor.grid[0] + 0.18), actor.grid[1]],
            on_floor: false,
            state: 'held',
            heldByActor: true,
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
      if (prop.id === 'red_umbrella' && !prop.on_floor) continue;
      activePropIds.add(prop.id);

      const regItem = propsRegistry[prop.id];
      const baseImageUrl = regItem?.image_url ?? `/assets/props/${prop.id}.png`;
      const isBroken = prop.state === 'broken';
      const preferredUrl =
        prop.id === 'crystal_vase' && isBroken
          ? '/assets/props/crystal_vase_broken.png'
          : baseImageUrl;
      const worldCoords = gridToWorld(prop.grid);
      let x = worldCoords.x;
      let z = worldCoords.z;
      const nearCenterTable =
        !prop.on_floor &&
        !prop.heldByActor &&
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

      // Normalize all props cleanly inside realistic stage bounding boxes
      const maxPropW = prop.id === 'crystal_vase' ? 0.42 : prop.heldByActor ? 0.30 : 0.38;
      const maxPropH = prop.id === 'crystal_vase' ? 0.46 : prop.heldByActor ? 0.30 : 0.38;
      const y = prop.heldByActor
        ? 0.92
        : prop.on_floor
          ? maxPropH / 2 + 0.02
          : 0.77 + maxPropH / 2;

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
          const fbTex = this.getFallbackStarTexture();
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
            ent.sprite.material.rotation = 0;
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
                ent.sprite.material.rotation = 0;
              }
            }
          },
          undefined,
          () => {
            this.failedUrls.add(effectiveUrl);
            if (effectiveUrl !== fallbackUrl) {
              ent.usingBrokenAsset = false;
              ent.currentTextureUrl = fallbackUrl;
              const fbTex = this.loadSpriteTexture(
                fallbackUrl,
                prop.id,
                (iw, ih) => {
                  ent.baseScale = this.fitScaleToBox(iw, ih, maxPropW, maxPropH);
                },
                (proceduralFb) => {
                  ent.sprite.material.map = proceduralFb;
                  ent.sprite.material.needsUpdate = true;
                },
              );
              ent.sprite.material.map = fbTex;
              ent.sprite.material.needsUpdate = true;
              ent.sprite.material.opacity = isBroken ? 0.55 : 1.0;
              ent.sprite.material.rotation = isBroken ? 0.65 : 0;
            } else {
              const proceduralFb = this.getFallbackStarTexture();
              this.textureCache.set(effectiveUrl, proceduralFb);
              ent.sprite.material.map = proceduralFb;
              ent.sprite.material.needsUpdate = true;
            }
          },
        );
        tex.colorSpace = THREE.SRGBColorSpace;
        ent.sprite.material.map = tex;
        ent.sprite.material.needsUpdate = true;
      };

      if (!entity) {
        const mat = new THREE.SpriteMaterial({
          map: this.getFallbackStarTexture(),
          transparent: true,
        });
        const sprite = new THREE.Sprite(mat);
        sprite.position.set(x, y, z);
        sprite.scale.set(maxPropW, maxPropH, 1);
        this.scene.add(sprite);
        entity = {
          sprite,
          baseScale: [maxPropW, maxPropH],
          targetPos: new THREE.Vector3(x, y, z),
          action: prop.state,
          facing: 'front',
          broken: isBroken,
        };
        this.propEntities.set(prop.id, entity);
        applyPropTexture(entity, preferredUrl, baseImageUrl);
      } else {
        entity.targetPos.set(x, y, z);
        entity.action = prop.state;
        entity.broken = isBroken;
        applyPropTexture(entity, preferredUrl, baseImageUrl);
      }

      if (isBroken && !entity.usingBrokenAsset) {
        entity.sprite.material.opacity = 0.55;
        entity.sprite.material.rotation = 0.65;
      } else {
        entity.sprite.material.opacity = 1.0;
        entity.sprite.material.rotation = 0;
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

  private updateLighting(dt: number): void {
    const t = Math.min(1, dt * 4.5);
    if (this.scene.background instanceof THREE.Color) {
      this.scene.background.lerp(this.targetBgColor, t);
    }
    if (this.scene.fog instanceof THREE.FogExp2) {
      this.scene.fog.color.lerp(this.targetBgColor, t);
    }
    this.ambientLight.color.lerp(this.targetAmbientColor, t);
    this.ambientLight.intensity += (this.targetAmbientIntensity - this.ambientLight.intensity) * t;
    this.spotLight.color.lerp(this.targetSpotColor, t);
    this.spotLight.intensity += (this.targetSpotIntensity - this.spotLight.intensity) * t;
    this.rimLight.color.lerp(this.targetRimColor, t);
  }

  private startLoop(): void {
    const tick = () => {
      if (this.disposed) return;
      this.animFrameId = requestAnimationFrame(tick);
      if (typeof document !== 'undefined' && document.hidden) {
        this.clock.getDelta();
        return;
      }
      const dt = Math.min(this.clock.getDelta(), 0.05);
      const elapsed = this.clock.elapsedTime;

      this.updateLighting(dt);
      this.updateEntities(dt, elapsed);
      this.updateWeather(dt, elapsed);
      this.updateCameras(dt, elapsed);

      this.rendererA.render(this.scene, this.cameraA);
      this.rendererB.render(this.scene, this.cameraB);
    };
    tick();
  }

  private updateEntities(dt: number, elapsed: number): void {
    const lerpFactor = Math.min(1, dt * 6.5);
    for (const [id, ent] of this.actorEntities.entries()) {
      if (!ent.sprite.visible) continue;
      ent.sprite.position.lerp(ent.targetPos, lerpFactor);

      const [w, h] = ent.baseScale;
      this.syncActorFacingTexture(ent);
      let scaleY = h;
      let offsetY = Math.sin(elapsed * 2.4 + (id === 'nyx' ? 1.4 : 0)) * 0.03;

      if (ent.action === 'kneel' || ent.action === 'crouch') {
        scaleY = h * 0.78;
        offsetY -= 0.18;
      } else if (ent.action === 'jump') {
        offsetY += Math.abs(Math.sin(elapsed * 5.2)) * 0.42;
      } else if (ent.action === 'flee') {
        offsetY += Math.abs(Math.sin(elapsed * 9.0)) * 0.1;
      } else if (ent.action === 'nuzzle' || ent.action === 'shared_umbrella') {
        offsetY += Math.sin(elapsed * 3.2) * 0.04;
      }

      ent.sprite.position.y = ent.targetPos.y + offsetY;
      // Keep positive scale.x in Three.js r170 (horizontal flip handled via UV-mirrored texture)
      ent.sprite.scale.set(w, scaleY, 1);
      if (ent.shadow) {
        ent.shadow.position.x = ent.sprite.position.x;
        ent.shadow.position.z = ent.sprite.position.z;
      }
      if (ent.haloRing && ent.haloRing.visible) {
        ent.haloRing.position.x = ent.sprite.position.x;
        ent.haloRing.position.z = ent.sprite.position.z;
        const pulse = 1 + Math.sin(elapsed * 4.2) * 0.07;
        ent.haloRing.scale.set(pulse, pulse, 1);
        const haloMat = ent.haloRing.material as THREE.MeshBasicMaterial;
        haloMat.opacity = 0.55 + Math.sin(elapsed * 4.2) * 0.25;
      }
    }

    for (const ent of this.propEntities.values()) {
      if (!ent.sprite.visible) continue;
      ent.sprite.position.lerp(ent.targetPos, lerpFactor);
      if (ent.action === 'glowing') {
        const pulse = 1 + Math.sin(elapsed * 4) * 0.08;
        ent.sprite.scale.set(ent.baseScale[0] * pulse, ent.baseScale[1] * pulse, 1);
        ent.sprite.position.y = ent.targetPos.y + Math.sin(elapsed * 2.6) * 0.06;
      } else {
        const sx = ent.sprite.scale.x + (ent.baseScale[0] - ent.sprite.scale.x) * lerpFactor;
        const sy = ent.sprite.scale.y + (ent.baseScale[1] - ent.sprite.scale.y) * lerpFactor;
        ent.sprite.scale.set(sx, sy, 1);
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

  private updateCameras(dt: number, elapsed: number): void {
    const shakeX = this.cameraShake ? Math.sin(elapsed * 38) * 0.08 : 0;
    const shakeY = this.cameraShake ? Math.cos(elapsed * 43) * 0.06 : 0;

    this.cameraA.position.set(shakeX, 2.75 + shakeY, 6.2);
    this.cameraA.lookAt(0, 0.9, 0);

    this.currentCameraBTarget.lerp(this.cameraBTarget, Math.min(1, dt * 5.0));
    this.cameraB.position.set(3.25 + shakeX * 0.8, 1.95 + shakeY * 0.8, 3.55);
    this.cameraB.lookAt(this.currentCameraBTarget);
  }

  /**
   * Capture start+end PNG keyframes and attempt 4s `.webm` `captureStream()` export
   * for both Camera A & B simultaneously. Falls back cleanly to `png_fallback` if
   * MediaRecorder/captureStream is unavailable or times out.
   */
  public async exportScenePreview(durationMs = 4000): Promise<PreviewExportResult> {
    const startTime = performance.now();
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
    this.canvasA.removeEventListener('webglcontextlost', this.contextLostHandler);
    this.canvasB.removeEventListener('webglcontextlost', this.contextLostHandler);
    if (this.animFrameId !== null) {
      cancelAnimationFrame(this.animFrameId);
    }
    this.rendererA.dispose();
    this.rendererB.dispose();
    for (const tex of this.textureCache.values()) {
      tex.dispose();
    }
    this.textureCache.clear();
    for (const tex of this.flippedTextureCache.values()) {
      tex.dispose();
    }
    this.flippedTextureCache.clear();
  }
}
