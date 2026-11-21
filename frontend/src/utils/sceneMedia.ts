/**
 * Scene Media Ownership & QA Summary Helpers.
 *
 * Feature & Use Cases:
 * - Decides whether a scene carries final media of its own (rendered for this
 *   branch, or explicitly reused from its parent) versus stale media copied
 *   from the parent branch before any render happened.
 * - Summarizes `qa_result` so the UI only shows pass/fail styling when a
 *   judge result actually exists for the scene.
 */
import type { BranchState, Scene } from '../types/schema';

/**
 * Find the same-numbered scene on the parent branch, if the branch has one.
 */
export function findParentScene(
  branch: BranchState,
  allBranches: BranchState[],
  sceneId: number,
): Scene | null {
  if (!branch.parent_branch_id) return null;
  const parent = allBranches.find((b) => b.branch_id === branch.parent_branch_id);
  return parent?.scenes.find((s) => s.scene_id === sceneId) ?? null;
}

/**
 * True when the scene has a final clip that belongs to it: either rendered for
 * this branch or explicitly reused from the parent. A scene that still needs a
 * rerender and only carries the parent's clip URLs has no media of its own.
 */
export function hasOwnFinalMedia(scene: Scene, parentScene: Scene | null): boolean {
  const media = scene.media;
  if (!media.cam_a_final_url && !media.cam_b_final_url) return false;
  if (scene.reused_from_branch) return true;
  if (scene.needs_rerender && parentScene) {
    const sameA = media.cam_a_final_url === parentScene.media.cam_a_final_url;
    const sameB = media.cam_b_final_url === parentScene.media.cam_b_final_url;
    if (sameA && sameB) return false;
  }
  return true;
}

export interface JudgeSummary {
  judged: boolean;
  allPassed: boolean;
  passedCount: number;
  total: number;
  scorePct: number;
}

/**
 * Summarize the scene's judge result. `judged` is false when there are no
 * per-question items or the media is not the scene's own.
 */
export function summarizeJudge(scene: Scene, ownMedia: boolean): JudgeSummary {
  const qa = scene.media.qa_result;
  const items = qa?.items ?? [];
  const judged = ownMedia && items.length > 0;
  const passedCount = items.filter((item) => item.passed).length;
  const allPassed =
    judged &&
    (typeof qa?.all_passed === 'boolean' ? qa.all_passed : passedCount === items.length);
  return {
    judged,
    allPassed,
    passedCount: judged ? passedCount : 0,
    total: judged ? items.length : 0,
    scorePct: judged ? Math.round((scene.media.qa_score ?? 0) * 100) : 0,
  };
}

/**
 * Stable key for tracking scenes rendered during this browser session.
 */
export function sceneKey(branchId: string, sceneId: number): string {
  return `${branchId}:${sceneId}`;
}
