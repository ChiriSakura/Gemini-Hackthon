/**
 * 4-Scene Story Branch Tree Component.
 *
 * Feature & Use Cases:
 * - Visualizes all branches (e.g., BE `branch_v1` and rewritten HE `branch_v2`).
 * - Clearly distinguishes cached prefix/unaffected scenes (`Reused (0s)`)
 *   from regenerated scenes (`Re-rendered`) and displays savings percentage.
 */
import React from 'react';
import type { BranchState } from '../types/schema';

interface StoryBranchTreeProps {
  branches: BranchState[];
  activeBranchId: string;
  activeSceneIndex: number;
  onSelectScene: (branchId: string, sceneIndex: number) => void;
}

export const StoryBranchTree: React.FC<StoryBranchTreeProps> = ({
  branches,
  activeBranchId,
  activeSceneIndex,
  onSelectScene,
}) => {
  return (
    <section className="branch-tree-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: '0.82rem',
        }}
      >
        <strong>🌿 4 幕因果分叉剧情树</strong>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.74rem' }}>
          点击任意分支与幕次即可切换舞台与对比过去/未来结局
        </span>
      </div>

      {branches.map((branch) => {
        const isCurrentBranch = branch.branch_id === activeBranchId;
        const reusedCount = branch.scenes.filter(
          (s) => Boolean(s.reused_from_branch) || !s.needs_rerender,
        ).length;
        const savedPct = Math.round(
          (reusedCount / Math.max(1, branch.scenes.length)) * 100,
        );

        return (
          <div key={branch.branch_id} className="branch-row">
            <button
              type="button"
              className={`branch-label-btn ${isCurrentBranch ? 'active' : ''}`}
              onClick={() => onSelectScene(branch.branch_id, activeSceneIndex)}
            >
              <div style={{ fontWeight: 700 }}>{branch.branch_label}</div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                {branch.forked_from_scene
                  ? `第 ${branch.forked_from_scene} 幕分叉 · 省下 ${savedPct}% 重画`
                  : '主线基准分支'}
              </div>
            </button>

            {branch.scenes.map((sc, idx) => {
              const isSelected = isCurrentBranch && idx === activeSceneIndex;
              const isReused =
                Boolean(sc.reused_from_branch) ||
                (Boolean(branch.forked_from_scene) && !sc.needs_rerender);
              const isRerendered = Boolean(sc.needs_rerender);

              return (
                <button
                  key={sc.scene_id}
                  type="button"
                  className={`scene-node-btn ${isSelected ? 'active' : ''} ${
                    isReused ? 'reused' : ''
                  }`}
                  onClick={() => onSelectScene(branch.branch_id, idx)}
                >
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      fontSize: '0.72rem',
                      fontWeight: 700,
                      gap: 4,
                    }}
                  >
                    <span>第 {sc.scene_id} 幕</span>
                    <span
                      style={{
                        padding: '1px 6px',
                        borderRadius: 999,
                        fontSize: '0.68rem',
                        background: isReused
                          ? 'rgba(52, 211, 153, 0.14)'
                          : isRerendered
                            ? 'rgba(251, 191, 36, 0.16)'
                            : 'rgba(56, 189, 248, 0.14)',
                        color: isReused
                          ? '#6ee7b7'
                          : isRerendered
                            ? '#fde68a'
                            : '#7dd3fc',
                      }}
                    >
                      {isReused
                        ? 'Reused (0s)'
                        : isRerendered
                          ? 'Re-rendered'
                          : 'Cached'}
                    </span>
                  </div>
                  <div
                    style={{
                      fontSize: '0.78rem',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}
                  >
                    {sc.title}
                  </div>
                </button>
              );
            })}
          </div>
        );
      })}
    </section>
  );
};
