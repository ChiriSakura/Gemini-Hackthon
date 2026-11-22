/**
 * HTTP API Client for StageState Backend Integration.
 *
 * Feature & Use Cases:
 * - Connects the frontend to P1 (`GET /api/branches`, `POST /api/director/fork`)
 *   and P2 (`POST /api/media/render-scene`, `POST /api/media/create-prop`)
 *   via Vite's `/api` proxy (http://localhost:8000).
 * - Falls back to local mock branches only for the initial branch list when the
 *   backend is offline; a failed fork is reported to the caller as an error.
 * - Reads `GET /api/health` so the UI can tell live models from canned answers.
 */
import { INITIAL_MOCK_BRANCHES } from '../data/mockBranches';
import type {
  BranchState,
  DirectorInputParsed,
  PropEntryMotion,
  PropRegistryItem,
  Scene,
  SceneMedia,
} from '../types/schema';
import {
  getPropNameEn,
  sanitizeBranchToEn,
  sanitizeRulesToEn,
  sanitizeSceneMediaToEn,
  sanitizeTextToEn,
} from '../utils/displayLocale';

export interface CreatePropPayload {
  name: string;
  description?: string;
  branchId: string;
  sceneId?: number;
  placement?: 'table' | 'held' | 'floor';
  entryMotion?: PropEntryMotion;
  detectThrowPeak?: boolean;
  mediaFilename?: string;
  imageBlob?: Blob | null;
}

export interface EvalSummaryResponse {
  stress_report?: unknown;
  latest_eval?: unknown;
  dual_cam_stats?: unknown;
  dual_cam_fixed_room?: unknown;
  fast_live_latency?: unknown;
  calibration_20_clips?: unknown;
  decisions_multimodal?: unknown;
  audio_mood?: unknown;
  branch_matrix?: unknown;
  prop_persistence?: unknown;
  cache_savings?: unknown;
  resolution_bench?: unknown;
  failover_rehearsal?: unknown;
  submission_summary?: unknown;
  preflight?: unknown;
}

export interface SceneRenderProgress {
  scene_id: number;
  status: string;
  qa_score?: number;
  fallback_to_preview?: boolean;
}

export interface RenderProgressSnapshot {
  branch_id: string;
  status: string;
  updated_at?: number;
  scenes: Record<string, SceneRenderProgress>;
}

export interface CreatePropResponse {
  prop_id: string;
  image_url: string;
  item: PropRegistryItem;
  placement?: 'table' | 'held' | 'floor';
  entry_motion?: PropEntryMotion;
  appearance_summary?: string;
  simulated?: boolean;
}

const DEFAULT_FALLBACK_RULES = [
  'Once crystal_vase is broken, shards remain visible on the floor in all subsequent scenes.',
  'Actors must keep consistent identity, outfit, and spatial left/right placement across Camera A & B.',
  'Newly introduced props must persist in props_registry and subsequent scenes unless explicitly removed.',
];

export interface ForkRequestPayload {
  parentBranchId: string;
  targetSceneId: number;
  clip?: Blob | null;
  text?: string;
  confirmContradiction?: boolean;
}

export interface PerceiveRequestPayload {
  targetSceneId: number;
  clip?: Blob | null;
  text?: string;
}

export interface InjectedPropPayload {
  prop_id: string;
  item: PropRegistryItem;
  placement?: 'table' | 'held' | 'floor';
  entry_motion?: PropEntryMotion;
}

export interface ExtendStoryPayload {
  branchId: string;
  fromSceneId?: number;
  clip?: Blob | null;
  text?: string;
  parsedDirective?: DirectorInputParsed | null;
  injectedProp?: InjectedPropPayload | null;
  endFreezeMode?: 'sculpture' | 'none';
  detectThrowPeak?: boolean;
}

export interface RenderScenePayload {
  branchId: string;
  scene: Scene;
  camAPreview?: Blob | null;
  camBPreview?: Blob | null;
  camAStartPng?: string | null;
  camAEndPng?: string | null;
  camBStartPng?: string | null;
  camBEndPng?: string | null;
  prevTailFrameUrl?: string | null;
  prevTailCamBUrl?: string | null;
  fastLiveMode?: boolean;
}

/**
 * Fetch all story branches from backend, falling back to locked mock branches
 * if the FastAPI server is not reachable yet.
 */
export async function fetchBranches(): Promise<{
  branches: BranchState[];
  fromMock: boolean;
}> {
  try {
    const res = await fetch('/api/branches', { signal: AbortSignal.timeout(2500) });
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    const data = await res.json();
    const branches: BranchState[] = Array.isArray(data) ? data : data.branches ?? [];
    if (branches.length === 0) {
      return {
        branches: INITIAL_MOCK_BRANCHES.map(sanitizeBranchToEn),
        fromMock: true,
      };
    }
    return { branches: branches.map(sanitizeBranchToEn), fromMock: false };
  } catch {
    return {
      branches: INITIAL_MOCK_BRANCHES.map(sanitizeBranchToEn),
      fromMock: true,
    };
  }
}

export interface BackendHealth {
  reachable: boolean;
  live: boolean;
  demoMode: boolean;
}

/**
 * Read `GET /api/health`. `live` is true only when the backend answers and
 * reports real model access; otherwise every backend answer is canned.
 */
export async function fetchHealth(): Promise<BackendHealth> {
  try {
    const res = await fetch('/api/health', { signal: AbortSignal.timeout(2500) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return {
      reachable: true,
      live: data?.live === true,
      demoMode: data?.demo_mode === true,
    };
  } catch {
    return { reachable: false, live: false, demoMode: false };
  }
}

const PERCEIVE_TIMEOUT_MS = 65000;
const FORK_TIMEOUT_MS = 65000;
const RENDER_TIMEOUT_MS = 105000;
const STANDARD_RENDER_TIMEOUT_MS = 210000;
const PROP_TIMEOUT_MS = 90000;

/**
 * Fast Stage-1 perception (`POST /api/director/perceive`, ~2s): returns Heard/Saw
 * cues and `needs_prop_prompt` before Neon planning or video rendering starts.
 */
export async function perceiveDirectorInput(
  payload: PerceiveRequestPayload,
): Promise<{ parsed: DirectorInputParsed | null; error: string | null }> {
  const formData = new FormData();
  formData.append('target_scene_id', String(Math.max(1, payload.targetSceneId)));
  if (payload.clip) {
    formData.append('clip', payload.clip, 'director_input.webm');
  } else if (payload.text && payload.text.trim()) {
    formData.append('text', payload.text.trim());
  }
  try {
    const res = await fetch('/api/director/perceive', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(PERCEIVE_TIMEOUT_MS),
    });
    if (!res.ok) {
      return { parsed: null, error: await readErrorDetail(res) };
    }
    const raw = (await res.json()) as DirectorInputParsed;
    return {
      parsed: {
        ...raw,
        instruction: sanitizeTextToEn(raw.instruction, payload.text?.trim() || 'Continue scene'),
        prop_name: raw.prop_name ? sanitizeTextToEn(raw.prop_name, raw.prop_name) : null,
        prop_prompt_hint: raw.prop_prompt_hint
          ? sanitizeTextToEn(raw.prop_prompt_hint, 'Hold or toss an object into your camera.')
          : null,
      },
      error: null,
    };
  } catch (err) {
    return { parsed: null, error: describeRequestError(err, PERCEIVE_TIMEOUT_MS) };
  }
}

/**
 * Unbounded Living-Sculpture continuation (`POST /api/director/extend`):
 * generates Scene N+1 from the current frozen stage (or forks cleanly if
 * rewound to an earlier historical node). Never fabricates a fake branch.
 */
export async function extendDirectorStory(
  payload: ExtendStoryPayload,
): Promise<{ branch: BranchState | null; error: string | null }> {
  const formData = new FormData();
  formData.append('branch_id', payload.branchId);
  if (payload.fromSceneId != null) {
    formData.append('from_scene_id', String(Math.max(1, payload.fromSceneId)));
  }
  if (payload.clip) {
    formData.append('clip', payload.clip, 'director_input.webm');
  } else if (payload.text && payload.text.trim()) {
    formData.append('text', payload.text.trim());
  }
  if (payload.parsedDirective) {
    formData.append('parsed_directive_json', JSON.stringify(payload.parsedDirective));
  }
  if (payload.injectedProp) {
    formData.append('injected_prop_json', JSON.stringify(payload.injectedProp));
  }
  formData.append('end_freeze_mode', payload.endFreezeMode ?? 'sculpture');
  formData.append('detect_throw_peak', String(payload.detectThrowPeak ?? true));

  try {
    const res = await fetch('/api/director/extend', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(FORK_TIMEOUT_MS),
    });
    if (!res.ok) {
      return { branch: null, error: await readErrorDetail(res) };
    }
    const raw = (await res.json()) as BranchState;
    if (!raw || !Array.isArray(raw.scenes) || raw.scenes.length === 0) {
      return { branch: null, error: 'backend returned a branch with no scenes' };
    }
    return { branch: sanitizeBranchToEn(raw), error: null };
  } catch (err) {
    return { branch: null, error: describeRequestError(err, FORK_TIMEOUT_MS) };
  }
}

/**
 * Fetch consolidated offline evaluation summaries from `GET /api/eval/summary`.
 * Returns `null` when the backend is offline so `EvalModal` falls back to
 * exact audited repo JSON values.
 */
export async function fetchEvalSummary(): Promise<EvalSummaryResponse | null> {
  try {
    const res = await fetch('/api/eval/summary', {
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) return null;
    return (await res.json()) as EvalSummaryResponse;
  } catch {
    return null;
  }
}

/**
 * Turn a failed request into a short human-readable reason.
 */
function describeRequestError(err: unknown, timeoutMs: number): string {
  if (err instanceof DOMException && (err.name === 'TimeoutError' || err.name === 'AbortError')) {
    return `timed out after ${Math.round(timeoutMs / 1000)}s`;
  }
  if (err instanceof TypeError) {
    return 'network error (backend unreachable)';
  }
  if (err instanceof Error && err.message) {
    return err.message.length > 180 ? `${err.message.slice(0, 180)}…` : err.message;
  }
  return 'unknown error';
}

/**
 * Read an error body from FastAPI (`{"detail": ...}`) or plain text.
 */
async function readErrorDetail(res: Response): Promise<string> {
  let detail = '';
  try {
    const raw = await res.text();
    try {
      const parsed = JSON.parse(raw);
      const d = parsed?.detail;
      if (typeof d === 'string') {
        detail = d;
      } else if (d && typeof d === 'object' && typeof d.message === 'string') {
        detail = d.message;
      } else {
        detail = d ? JSON.stringify(d) : raw;
      }
    } catch {
      detail = raw;
    }
  } catch {
    detail = '';
  }
  const cleaned = sanitizeTextToEn(detail.replace(/\s+/g, ' ').trim(), '');
  return cleaned ? `HTTP ${res.status} — ${cleaned}` : `HTTP ${res.status}`;
}

/**
 * Submit a 3s webcam+mic `.webm` clip or text rewrite to `POST /api/director/fork`.
 * Returns the newly forked `BranchState`, or `branch: null` plus a reason when the
 * request fails (HTTP status, network error, or timeout). Never fabricates a branch.
 */
export async function submitDirectorFork(
  payload: ForkRequestPayload,
): Promise<{ branch: BranchState | null; error: string | null }> {
  const formData = new FormData();
  formData.append('parent_branch_id', payload.parentBranchId);
  formData.append('target_scene_id', String(payload.targetSceneId));
  if (payload.clip) {
    formData.append('clip', payload.clip, 'director_input.webm');
  }
  if (payload.text && payload.text.trim()) {
    formData.append('text', payload.text.trim());
  }
  if (payload.confirmContradiction) {
    formData.append('confirm_contradiction', 'true');
  }

  try {
    const res = await fetch('/api/director/fork', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(FORK_TIMEOUT_MS),
    });
    if (!res.ok) {
      return { branch: null, error: await readErrorDetail(res) };
    }
    const raw = (await res.json()) as BranchState;
    if (!raw || !Array.isArray(raw.scenes) || raw.scenes.length === 0) {
      return { branch: null, error: 'backend returned a branch with no scenes' };
    }
    return { branch: sanitizeBranchToEn(raw), error: null };
  } catch (err) {
    return { branch: null, error: describeRequestError(err, FORK_TIMEOUT_MS) };
  }
}

export interface RenderSceneResponse {
  scene_id: number;
  media: SceneMedia;
  environment_notes?: string;
}

/**
 * Send exported 2.5D preview clips (or start/end PNG keyframes fallback) to P2
 * `POST /api/media/render-scene` to obtain Omni Flash stylized video & QA results.
 */
export async function requestSceneRender(
  payload: RenderScenePayload,
): Promise<{ result: RenderSceneResponse | null; error: string | null }> {
  const formData = new FormData();
  formData.append('branch_id', payload.branchId);
  formData.append('scene_id', String(payload.scene.scene_id));
  formData.append('scene_json', JSON.stringify(payload.scene));
  if (payload.camAPreview) {
    formData.append('cam_a_preview', payload.camAPreview, 'cam_a_preview.webm');
  }
  if (payload.camBPreview) {
    formData.append('cam_b_preview', payload.camBPreview, 'cam_b_preview.webm');
  }
  if (payload.camAStartPng) formData.append('cam_a_start_png', payload.camAStartPng);
  if (payload.camAEndPng) formData.append('cam_a_end_png', payload.camAEndPng);
  if (payload.camBStartPng) formData.append('cam_b_start_png', payload.camBStartPng);
  if (payload.camBEndPng) formData.append('cam_b_end_png', payload.camBEndPng);
  if (payload.prevTailFrameUrl) {
    formData.append('prev_tail_frame_url', payload.prevTailFrameUrl);
  }
  if (payload.prevTailCamBUrl) {
    formData.append('prev_tail_cam_b_url', payload.prevTailCamBUrl);
  }
  if (payload.fastLiveMode) {
    formData.append('fast_live_mode', 'true');
  }

  const timeoutMs = payload.fastLiveMode ? RENDER_TIMEOUT_MS : STANDARD_RENDER_TIMEOUT_MS;
  try {
    const res = await fetch('/api/media/render-scene', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) {
      return { result: null, error: await readErrorDetail(res) };
    }
    const data = await res.json();
    const resolvedSceneId = data.scene_id ?? payload.scene.scene_id;
    return {
      result: {
        scene_id: resolvedSceneId,
        media: sanitizeSceneMediaToEn((data.media ?? data) as SceneMedia, resolvedSceneId),
        environment_notes: data.environment_notes
          ? sanitizeTextToEn(data.environment_notes, 'clean_stage')
          : undefined,
      },
      error: null,
    };
  } catch (err) {
    return { result: null, error: describeRequestError(err, timeoutMs) };
  }
}

/**
 * Fetch active continuity rules from `GET /api/director/rules`.
 */
export async function fetchDirectorRules(): Promise<string[]> {
  try {
    const res = await fetch('/api/director/rules', {
      signal: AbortSignal.timeout(2500),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rules: string[] = Array.isArray(data?.rules) ? data.rules : [];
    return rules.length > 0 ? sanitizeRulesToEn(rules) : DEFAULT_FALLBACK_RULES;
  } catch {
    return DEFAULT_FALLBACK_RULES;
  }
}

/**
 * Create a transparent 2D stage prop from a webcam snapshot (or text description)
 * via `POST /api/media/create-prop`.
 */
export async function createStageProp(
  payload: CreatePropPayload,
): Promise<CreatePropResponse> {
  const formData = new FormData();
  formData.append('name', payload.name.trim());
  formData.append('description', (payload.description ?? payload.name).trim());
  formData.append('branch_id', payload.branchId);
  formData.append('scene_id', String(payload.sceneId ?? 1));
  formData.append('placement', payload.placement ?? 'table');
  formData.append('entry_motion', payload.entryMotion ?? 'place');
  formData.append('detect_throw_peak', String(payload.detectThrowPeak ?? true));
  if (payload.imageBlob) {
    const defaultFilename = payload.imageBlob.type.includes('video')
      ? 'throw_prop.webm'
      : 'webcam_prop.png';
    formData.append('image', payload.imageBlob, payload.mediaFilename ?? defaultFilename);
  }

  try {
    const res = await fetch('/api/media/create-prop', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(PROP_TIMEOUT_MS),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rawItem = data.item as PropRegistryItem;
    const rawAppearance =
      typeof data.appearance_summary === 'string' && data.appearance_summary.trim()
        ? data.appearance_summary.trim()
        : typeof rawItem?.appearance_summary === 'string'
          ? rawItem.appearance_summary.trim()
          : '';
    const strippedAppearance = rawAppearance
      .replace(/[\u4e00-\u9fff]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const appearanceSummary = strippedAppearance
      ? sanitizeTextToEn(strippedAppearance, '')
      : '';
    return {
      prop_id: data.prop_id,
      image_url: data.image_url,
      placement: data.placement ?? payload.placement ?? 'table',
      entry_motion: data.entry_motion ?? payload.entryMotion ?? 'place',
      appearance_summary: appearanceSummary || undefined,
      item: {
        ...rawItem,
        name: getPropNameEn(data.prop_id, rawItem),
        state: sanitizeTextToEn(rawItem?.state, 'glowing'),
        appearance_summary: appearanceSummary || undefined,
      },
      simulated: false,
    };
  } catch {
    const slug =
      payload.name
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_|_$/g, '') || `prop_${Date.now().toString().slice(-4)}`;
    const isImageBlob =
      Boolean(payload.imageBlob) && !payload.imageBlob!.type.includes('video');
    const imageUrl = isImageBlob
      ? URL.createObjectURL(payload.imageBlob!)
      : '/assets/props/star_token.png';
    return {
      prop_id: slug,
      image_url: imageUrl,
      placement: payload.placement ?? 'table',
      entry_motion: payload.entryMotion ?? 'place',
      item: {
        name: payload.name.trim(),
        image_url: imageUrl,
        state: 'glowing',
        origin: payload.imageBlob ? 'camera_physical' : 'text_generated',
      },
      simulated: true,
    };
  }
}

/**
 * Poll live per-scene parallel render progress from `GET /api/media/progress/{branch_id}`.
 */
export async function fetchRenderProgress(
  branchId: string,
): Promise<RenderProgressSnapshot | null> {
  try {
    const res = await fetch(
      `/api/media/progress/${encodeURIComponent(branchId)}`,
      { signal: AbortSignal.timeout(2000) },
    );
    if (!res.ok) return null;
    return (await res.json()) as RenderProgressSnapshot;
  } catch {
    return null;
  }
}

/**
 * Subscribe to real-time render progress via Server-Sent Events
 * (`GET /api/media/progress/{branch_id}/stream`), automatically falling back to
 * lightweight polling when `EventSource` is unavailable or disconnects.
 */
export function subscribeRenderProgress(
  branchId: string,
  onSnapshot: (snap: RenderProgressSnapshot) => void,
): () => void {
  let closed = false;
  let pollTimer: number | null = null;
  let es: EventSource | null = null;

  const startPolling = () => {
    if (closed || pollTimer !== null) return;
    const poll = async () => {
      const snap = await fetchRenderProgress(branchId);
      if (!closed && snap) onSnapshot(snap);
    };
    void poll();
    pollTimer = window.setInterval(() => void poll(), 1200);
  };

  if (typeof EventSource !== 'undefined') {
    try {
      es = new EventSource(
        `/api/media/progress/${encodeURIComponent(branchId)}/stream`,
      );
      es.onmessage = (evt) => {
        if (closed) return;
        try {
          const parsed = JSON.parse(evt.data) as RenderProgressSnapshot;
          if (parsed && typeof parsed === 'object') {
            onSnapshot(parsed);
          }
        } catch {
          // Ignore malformed keepalive frames
        }
      };
      es.onerror = () => {
        if (es) {
          es.close();
          es = null;
        }
        startPolling();
      };
    } catch {
      startPolling();
    }
  } else {
    startPolling();
  }

  return () => {
    closed = true;
    if (es) {
      es.close();
      es = null;
    }
    if (pollTimer !== null) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  };
}

export interface BranchExportResult {
  branch_id: string;
  mode: 'cam_a' | 'cam_b' | 'dual_cam';
  scenes_stitched: number[];
  scene_count: number;
  duration_est_sec: number;
  video_url: string;
  bytes: number;
  is_h264: boolean;
}

/**
 * Stitch all rendered clips of `branchId` into a single downloadable H.264+AAC MP4
 * via `POST /api/media/export-branch`.
 */
export async function exportBranchMovie(
  branchId: string,
  mode: 'cam_a' | 'cam_b' | 'dual_cam' = 'dual_cam',
): Promise<{ result: BranchExportResult | null; error: string | null }> {
  const formData = new FormData();
  formData.append('branch_id', branchId);
  formData.append('mode', mode);
  try {
    const res = await fetch('/api/media/export-branch', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(65000),
    });
    if (!res.ok) {
      return { result: null, error: await readErrorDetail(res) };
    }
    return { result: (await res.json()) as BranchExportResult, error: null };
  } catch (err) {
    return { result: null, error: describeRequestError(err, 65000) };
  }
}

/**
 * Trigger concurrent rendering for all `needs_rerender` scenes of a branch.
 */
export async function requestBranchRender(
  branchId: string,
): Promise<{ branch: BranchState | null; error: string | null }> {
  const formData = new FormData();
  formData.append('branch_id', branchId);
  try {
    const res = await fetch('/api/media/render-scene', {
      method: 'POST',
      body: formData,
      signal: AbortSignal.timeout(RENDER_TIMEOUT_MS),
    });
    if (!res.ok) {
      return { branch: null, error: await readErrorDetail(res) };
    }
    return { branch: sanitizeBranchToEn((await res.json()) as BranchState), error: null };
  } catch (err) {
    return { branch: null, error: describeRequestError(err, RENDER_TIMEOUT_MS) };
  }
}


/**
 * Save a character's new stage position after the user dragged it on the 3D stage,
 * so the next scene is planned and rendered from where it now stands.
 */
export async function moveStageActor(
  branchId: string,
  sceneId: number,
  actorId: string,
  grid: [number, number],
): Promise<boolean> {
  const formData = new FormData();
  formData.append('branch_id', branchId);
  formData.append('scene_id', String(sceneId));
  formData.append('actor_id', actorId);
  formData.append('grid_x', String(grid[0]));
  formData.append('grid_y', String(grid[1]));
  try {
    const res = await fetch('/api/director/move-actor', { method: 'POST', body: formData });
    return res.ok;
  } catch {
    return false;
  }
}
