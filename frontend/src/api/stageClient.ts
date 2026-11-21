/**
 * HTTP API Client for StageState Backend Integration.
 *
 * Feature & Use Cases:
 * - Connects the frontend to P1 (`GET /api/branches`, `POST /api/director/fork`)
 *   and P2 (`POST /api/media/render-scene`, `POST /api/media/create-prop`)
 *   via Vite's `/api` proxy (http://localhost:8000).
 * - Provides graceful fallback to local mock branches when backend is offline.
 */
import { INITIAL_MOCK_BRANCHES, MOCK_BRANCH_2 } from '../data/mockBranches';
import type { BranchState, PropRegistryItem, Scene, SceneMedia } from '../types/schema';

export interface CreatePropPayload {
  name: string;
  description?: string;
  branchId: string;
  imageBlob?: Blob | null;
}

export interface CreatePropResponse {
  prop_id: string;
  image_url: string;
  item: PropRegistryItem;
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
      return { branches: INITIAL_MOCK_BRANCHES, fromMock: true };
    }
    return { branches, fromMock: false };
  } catch {
    return { branches: INITIAL_MOCK_BRANCHES, fromMock: true };
  }
}

/**
 * Submit a 3s webcam+mic `.webm` clip or text rewrite to `POST /api/director/fork`.
 * Returns the newly forked `BranchState` (or simulated `branch_v2` in offline mode).
 */
export async function submitDirectorFork(
  payload: ForkRequestPayload,
): Promise<{ branch: BranchState; simulated: boolean }> {
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
    });
    if (!res.ok) {
      const errText = await res.text();
      throw new Error(errText || `HTTP ${res.status}`);
    }
    const branch = (await res.json()) as BranchState;
    return { branch, simulated: false };
  } catch {
    // Offline/pre-integration simulation using locked mock_branch_2
    const simulatedBranch: BranchState = {
      ...MOCK_BRANCH_2,
      branch_id: `branch_sim_${Date.now().toString().slice(-4)}`,
      parent_branch_id: payload.parentBranchId,
      forked_from_scene: payload.targetSceneId,
      rewrite_summary: payload.text?.trim()
        ? `Simulated Scene ${payload.targetSceneId} rewrite: "${payload.text.trim()}"`
        : MOCK_BRANCH_2.rewrite_summary,
    };
    return { branch: simulatedBranch, simulated: true };
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
): Promise<RenderSceneResponse | null> {
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

  try {
    const res = await fetch('/api/media/render-scene', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) return null;
    const data = await res.json();
    return {
      scene_id: data.scene_id ?? payload.scene.scene_id,
      media: (data.media ?? data) as SceneMedia,
      environment_notes: data.environment_notes,
    };
  } catch {
    return null;
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
    return rules.length > 0 ? rules : DEFAULT_FALLBACK_RULES;
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
  if (payload.imageBlob) {
    formData.append('image', payload.imageBlob, 'webcam_prop.png');
  }

  try {
    const res = await fetch('/api/media/create-prop', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return {
      prop_id: data.prop_id,
      image_url: data.image_url,
      item: data.item as PropRegistryItem,
      simulated: false,
    };
  } catch {
    const slug =
      payload.name
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_|_$/g, '') || `prop_${Date.now().toString().slice(-4)}`;
    const imageUrl = payload.imageBlob
      ? URL.createObjectURL(payload.imageBlob)
      : '/assets/props/star_token.png';
    return {
      prop_id: slug,
      image_url: imageUrl,
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
 * Trigger concurrent rendering for all `needs_rerender` scenes of a branch.
 */
export async function requestBranchRender(
  branchId: string,
): Promise<BranchState | null> {
  const formData = new FormData();
  formData.append('branch_id', branchId);
  try {
    const res = await fetch('/api/media/render-scene', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) return null;
    return (await res.json()) as BranchState;
  } catch {
    return null;
  }
}
