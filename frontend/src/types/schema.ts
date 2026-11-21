/**
 * Locked TypeScript Data Contract for StageState Frontend (mirrors `shared/schema.json`).
 *
 * Feature & Use Cases:
 * - Strictly matches `shared/schema.json` and `backend/schemas.py` so the
 *   Three.js 2.5D stage (Lia & Nyx), Clapperboard QA strip, and Branch Tree
 *   work seamlessly against `shared/mock_branch_1.json` and live FastAPI routes.
 */

export type LightingPreset = 'warm_dim' | 'cold_blue' | 'warm_transition' | 'warm_gold_snow';
export type VocalEmotion = 'neutral' | 'gentle_whisper' | 'urgent_emotional' | 'angry_shout' | 'joyful' | 'sad_soft';
export type GestureIntent =
  | 'none'
  | 'point_left'
  | 'point_right'
  | 'point_center'
  | 'stop_rewind'
  | 'beckon_soothe'
  | 'show_physical_prop';

export interface DirectorInputParsed {
  target_scene_id: number;
  instruction: string;
  vocal_emotion: VocalEmotion;
  gesture_intent: GestureIntent;
  direction_hint: 'none' | 'left' | 'center' | 'right' | 'front' | 'back';
  has_physical_prop: boolean;
  prop_name?: string | null;
  prop_description?: string | null;
  prop_frame_timestamp_sec?: number | null;
  mode?: 'extend' | 'fork';
  needs_prop_prompt?: boolean;
  prop_prompt_hint?: string | null;
}

export interface JudgeItem {
  question: string;
  passed: boolean;
  confidence: number;
  reason?: string;
}

export interface JudgeResult {
  overall_score: number;
  all_passed: boolean;
  items: JudgeItem[];
}

export type ActorAction =
  | 'stand'
  | 'walk'
  | 'kneel'
  | 'reach'
  | 'sit'
  | 'jump'
  | 'crouch'
  | 'flee'
  | 'nuzzle'
  | 'shared_umbrella'
  | 'catch_from_camera'
  | 'drink'
  | 'eat'
  | 'hold_up';

export interface ActorState {
  id: 'lia' | 'nyx';
  name: string;
  asset_url: string;
  grid: [number, number];
  facing: 'left' | 'right' | 'front' | 'center';
  action: ActorAction;
  holding_prop: string | null;
  height_m?: number | null;
}

export type PropEntryMotion = 'place' | 'throw_arc' | 'held';

export interface StagePropState {
  id: string;
  grid: [number, number];
  on_floor: boolean;
  state: 'intact' | 'broken' | 'held' | 'glowing' | 'closed' | 'open';
  entry_motion?: PropEntryMotion;
  entry_origin?: number[] | null;
}

export interface SceneMedia {
  cam_a_preview_url: string | null;
  cam_b_preview_url: string | null;
  cam_a_final_url: string | null;
  cam_b_final_url: string | null;
  cam_a_tail_frame_url: string | null;
  cam_b_tail_frame_url: string | null;
  qa_result?: JudgeResult;
  qa_score: number;
  qa_retries: number;
  selected_candidate_index: 0 | 1;
  fallback_to_preview: boolean;
}

export interface Scene {
  scene_id: number;
  title: string;
  duration_sec: number;
  narration: string;
  vocal_emotion: VocalEmotion;
  gesture_intent: GestureIntent;
  direction_hint: string;
  lighting: LightingPreset;
  camera_shake: boolean;
  audio_mood: string;
  environment_notes: string;
  needs_rerender: boolean;
  reused_from_branch: string | null;
  end_freeze_mode?: 'sculpture' | 'none';
  parent_scene_id?: number | null;
  actors: ActorState[];
  stage_props: StagePropState[];
  qa_questions: string[];
  media: SceneMedia;
}

export interface PropRegistryItem {
  name: string;
  image_url: string;
  state: string;
  origin: 'preset' | 'text_generated' | 'camera_physical';
  height_m?: number | null;
  appearance_summary?: string;
}

export interface BranchState {
  branch_id: string;
  branch_label: string;
  parent_branch_id: string | null;
  forked_from_scene: number | null;
  rewrite_summary: string | null;
  contradiction_warning: string | null;
  props_registry: Record<string, PropRegistryItem>;
  scenes: Scene[];
}
