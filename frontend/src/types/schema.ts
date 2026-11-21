/**
 * Locked TypeScript Data Contract for StageState Frontend (Owned by Coordinator, consumed by P3).
 *
 * Feature & Use Cases:
 * - Strictly mirrors `schema.json` and `backend/schemas.py` so the Three.js
 *   stage, branch tree, and Clapperboard QA strip can develop independently
 *   with zero backend integration friction.
 */

export interface ActorPlacement {
  id: string;
  grid: [number, number];
  action: string;
  holding_prop: string | null;
}

export interface StagePropPlacement {
  id: string;
  grid: [number, number];
  on_floor: boolean;
}

export interface QAResult {
  question: string;
  passed: boolean;
  confidence: number;
}

export interface SceneMedia {
  cam_a_preview_url: string | null;
  cam_b_preview_url: string | null;
  cam_a_final_url: string | null;
  cam_b_final_url: string | null;
  tail_frame_url: string | null;
  qa_results: QAResult[];
  qa_score: number;
  qa_retries: number;
}

export interface SceneState {
  scene_id: number;
  narration: string;
  vocal_emotion: string;
  lighting: string;
  camera_shake: number;
  environment_notes: string;
  was_regenerated: boolean;
  actors: ActorPlacement[];
  stage_props: StagePropPlacement[];
  qa_questions: string[];
  media: SceneMedia;
}

export interface PropAsset {
  prop_id: string;
  name: string;
  image_url: string;
  state: string;
  source_type: 'text' | 'camera';
}

export interface ActorAsset {
  actor_id: string;
  name: string;
  portrait_url: string;
  color_hex: string;
}

export interface BranchState {
  branch_id: string;
  parent_branch_id: string | null;
  forked_from_scene: number;
  contradiction_warning?: string | null;
  props_registry: Record<string, PropAsset>;
  actors_registry: Record<string, ActorAsset>;
  scenes: SceneState[];
}
