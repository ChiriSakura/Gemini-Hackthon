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
  baseScale: [number, number];
  targetPos: THREE.Vector3;
  action: string;
  facing: string;
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
  private actorEntities = new Map<string, SpriteEntity>();
  private propEntities = new Map<string, SpriteEntity>();

  private currentLighting: LightingPreset = 'warm_dim';
  private cameraShake = false;
  private cameraBTarget = new THREE.Vector3(0, 0.9, 0);
  private animFrameId: number | null = null;
  private clock = new THREE.Clock();
  private disposed = false;
  private pixelArtMode = false;

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

    this.startLoop();
  }

  private buildConservatoryStage(): void {
    // 3x3 reflective glasshouse floor
    const floorGeo = new THREE.PlaneGeometry(7.2, 6.0);
    const floorMat = new THREE.MeshStandardMaterial({
      color: '#111827',
      roughness: 0.28,
      metalness: 0.35,
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    this.scene.add(floor);

    // 3x3 grid lines
    const gridHelper = new THREE.GridHelper(6.3, 3, 0x38bdf8, 0x334155);
    gridHelper.position.y = 0.01;
    this.scene.add(gridHelper);

    // Subtle glasshouse back arch frame
    const backWallGeo = new THREE.PlaneGeometry(7.6, 4.4);
    const backWallMat = new THREE.MeshStandardMaterial({
      color: '#0f172a',
      roughness: 0.6,
      transparent: true,
      opacity: 0.85,
    });
    const backWall = new THREE.Mesh(backWallGeo, backWallMat);
    backWall.position.set(0, 2.2, -3.05);
    this.scene.add(backWall);

    // Center round table at grid [1, 1] -> world (0, 0)
    const tableTop = new THREE.Mesh(
      new THREE.CylinderGeometry(0.68, 0.64, 0.1, 24),
      new THREE.MeshStandardMaterial({ color: '#334155', roughness: 0.3, metalness: 0.5 }),
    );
    tableTop.position.set(0, 0.72, 0);
    this.scene.add(tableTop);

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

  private loadSpriteTexture(
    url: string,
    fallbackKey?: string,
    onSizeReady?: (w: number, h: number) => void,
  ): THREE.Texture {
    const cached = this.textureCache.get(url);
    if (cached) {
      const img = cached.image as { width?: number; height?: number } | undefined;
      if (img?.width && img?.height && onSizeReady) {
        onSizeReady(img.width, img.height);
      }
      return cached;
    }

    const fallbackTex = createFallbackStarTexture();
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
        if (fallbackKey) {
          this.textureCache.set(url, fallbackTex);
        }
      },
    );
    tex.colorSpace = THREE.SRGBColorSpace;
    this.textureCache.set(url, tex);
    return tex;
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
   */
  public applyScene(
    sceneData: Scene,
    propsRegistry: Record<string, PropRegistryItem>,
  ): void {
    this.currentLighting = sceneData.lighting;
    this.cameraShake = Boolean(sceneData.camera_shake);
    this.applyLightingPreset(sceneData.lighting);

    // Sync actors
    const activeActorIds = new Set<string>();
    const sumPos = new THREE.Vector3();

    for (const actor of sceneData.actors) {
      activeActorIds.add(actor.id);
      const { x, z } = gridToWorld(actor.grid);
      const isNyx = actor.id === 'nyx';
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
            if (spriteRef.entity) {
              spriteRef.entity.baseScale = fitted;
              spriteRef.entity.targetPos.y = fitted[1] / 2 + 0.02;
            }
          }),
          transparent: true,
        });
        const sprite = new THREE.Sprite(mat);
        sprite.position.set(x, y, z);
        sprite.scale.set(maxW, maxH, 1);
        this.scene.add(sprite);
        entity = {
          sprite,
          baseScale: [maxW, maxH],
          targetPos: new THREE.Vector3(x, y, z),
          action: actor.action,
          facing: actor.facing,
        };
        spriteRef.entity = entity;
        this.actorEntities.set(actor.id, entity);
      } else {
        entity.targetPos.set(x, entity.baseScale[1] / 2 + 0.02, z);
        entity.action = actor.action;
        entity.facing = actor.facing;
      }
      sumPos.add(entity.targetPos);
    }

    if (sceneData.actors.length > 0) {
      sumPos.divideScalar(sceneData.actors.length);
      this.cameraBTarget.set(sumPos.x, 0.92, sumPos.z);
    }

    // Hide unused actors
    for (const [id, ent] of this.actorEntities.entries()) {
      ent.sprite.visible = activeActorIds.has(id);
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
      const { x, z } = gridToWorld(prop.grid);
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
      const applyPropTexture = (ent: SpriteEntity, targetUrl: string, fallbackUrl: string) => {
        if (ent.currentTextureUrl === targetUrl) return;
        ent.currentTextureUrl = targetUrl;
        ent.usingBrokenAsset = false;

        const cached = this.textureCache.get(targetUrl);
        if (cached) {
          ent.sprite.material.map = cached;
          ent.sprite.material.needsUpdate = true;
          if (targetUrl.includes('crystal_vase_broken')) {
            ent.usingBrokenAsset = true;
            ent.sprite.material.opacity = 1.0;
            ent.sprite.material.rotation = 0;
          }
          return;
        }

        const tex = this.textureLoader.load(
          targetUrl,
          (loaded) => {
            loaded.colorSpace = THREE.SRGBColorSpace;
            this.textureCache.set(targetUrl, loaded);
            if (ent.currentTextureUrl === targetUrl) {
              ent.sprite.material.map = loaded;
              ent.sprite.material.needsUpdate = true;
              const img = loaded.image as { width?: number; height?: number } | undefined;
              if (img?.width && img?.height) {
                ent.baseScale = this.fitScaleToBox(img.width, img.height, maxPropW, maxPropH);
              }
              if (targetUrl.includes('crystal_vase_broken')) {
                ent.usingBrokenAsset = true;
                ent.sprite.material.opacity = 1.0;
                ent.sprite.material.rotation = 0;
              }
            }
          },
          undefined,
          () => {
            if (targetUrl !== fallbackUrl) {
              ent.usingBrokenAsset = false;
              const fbTex = this.loadSpriteTexture(fallbackUrl, prop.id, (iw, ih) => {
                ent.baseScale = this.fitScaleToBox(iw, ih, maxPropW, maxPropH);
              });
              ent.sprite.material.map = fbTex;
              ent.sprite.material.needsUpdate = true;
              ent.sprite.material.opacity = isBroken ? 0.55 : 1.0;
              ent.sprite.material.rotation = isBroken ? 0.65 : 0;
            }
          },
        );
        tex.colorSpace = THREE.SRGBColorSpace;
        ent.sprite.material.map = tex;
        ent.sprite.material.needsUpdate = true;
      };

      if (!entity) {
        const mat = new THREE.SpriteMaterial({
          map: this.loadSpriteTexture(baseImageUrl, prop.id),
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
        this.scene.background = new THREE.Color('#0c101d');
        this.ambientLight.color.set('#cbd5e1');
        this.ambientLight.intensity = 0.8;
        this.spotLight.color.set('#fde68a');
        this.spotLight.intensity = 26;
        this.rimLight.color.set('#60a5fa');
        break;
      case 'cold_blue':
        this.scene.background = new THREE.Color('#050b18');
        this.ambientLight.color.set('#60a5fa');
        this.ambientLight.intensity = 0.65;
        this.spotLight.color.set('#93c5fd');
        this.spotLight.intensity = 22;
        this.rimLight.color.set('#38bdf8');
        break;
      case 'warm_transition':
        this.scene.background = new THREE.Color('#131122');
        this.ambientLight.color.set('#fcd34d');
        this.ambientLight.intensity = 0.95;
        this.spotLight.color.set('#fef08a');
        this.spotLight.intensity = 32;
        this.rimLight.color.set('#f59e0b');
        break;
      case 'warm_gold_snow':
        this.scene.background = new THREE.Color('#1a1423');
        this.ambientLight.color.set('#fde68a');
        this.ambientLight.intensity = 1.1;
        this.spotLight.color.set('#fbbf24');
        this.spotLight.intensity = 38;
        this.rimLight.color.set('#f472b6');
        break;
    }
  }

  private startLoop(): void {
    const tick = () => {
      if (this.disposed) return;
      this.animFrameId = requestAnimationFrame(tick);
      const dt = Math.min(this.clock.getDelta(), 0.05);
      const elapsed = this.clock.elapsedTime;

      this.updateEntities(dt, elapsed);
      this.updateWeather(dt, elapsed);
      this.updateCameras(elapsed);

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
      const flipX = ent.facing === 'left' ? -1 : 1;
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
      ent.sprite.scale.set(w * flipX, scaleY, 1);
    }

    for (const ent of this.propEntities.values()) {
      if (!ent.sprite.visible) continue;
      ent.sprite.position.lerp(ent.targetPos, lerpFactor);
      if (ent.action === 'glowing') {
        const pulse = 1 + Math.sin(elapsed * 4) * 0.08;
        ent.sprite.scale.set(ent.baseScale[0] * pulse, ent.baseScale[1] * pulse, 1);
        ent.sprite.position.y = ent.targetPos.y + Math.sin(elapsed * 2.6) * 0.06;
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

  private updateCameras(elapsed: number): void {
    const shakeX = this.cameraShake ? Math.sin(elapsed * 38) * 0.08 : 0;
    const shakeY = this.cameraShake ? Math.cos(elapsed * 43) * 0.06 : 0;

    this.cameraA.position.set(shakeX, 2.75 + shakeY, 6.2);
    this.cameraA.lookAt(0, 0.9, 0);

    this.cameraB.position.set(3.25 + shakeX * 0.8, 1.95 + shakeY * 0.8, 3.55);
    this.cameraB.lookAt(this.cameraBTarget);
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
    if (this.animFrameId !== null) {
      cancelAnimationFrame(this.animFrameId);
    }
    this.rendererA.dispose();
    this.rendererB.dispose();
    for (const tex of this.textureCache.values()) {
      tex.dispose();
    }
    this.textureCache.clear();
  }
}
