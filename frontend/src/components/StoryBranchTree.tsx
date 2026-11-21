/**
 * 4-Scene Causal Story Branch Tree Component.
 *
 * Feature & Use Cases:
 * - Visualizes all branches (e.g., BE `branch_v1`, HE `branch_v2_cached`,
 *   `branch_v3_starlight_cached`, `branch_v4_umbrella_shield_cached`, and live forks).
 * - Provides 1-click 4-branch preset switcher pills (`BE` / `HE Star` / `Twist V3` / `Shield V4`).
 * - Shows crisp `Reused (0s)` vs `Re-rendered` badges plus a hover/focus
 *   thumbnail tooltip (`120x68px` tail frame + `qa_score`) on rendered scene nodes.
 */
import React, { useState } from 'react';
import type { BranchState, Scene } from '../types/schema';
import { getBranchLabelEn, getSceneTextEn } from '../utils/displayLocale';

interface StoryBranchTreeProps {
  branches: BranchState[];
  activeBranchId: string;
  activeSceneIndex: number;
  onSelectScene: (branchId: string, sceneIndex: number) => void;
}

const PRESET_SLOTS: Array<{ label: string; match: (id: string) => boolean }> = [
  { label: 'BE', match: (id) => id === 'branch_v1' },
  { label: 'HE Star', match: (id) => id === 'branch_v2_cached' || id === 'branch_v2' },
  { label: 'Twist V3', match: (id) => id.startsWith('branch_v3') },
  { label: 'Shield V4', match: (id) => id.startsWith('branch_v4') },
];

export const StoryBranchTree: React.FC<StoryBranchTreeProps> = ({
  branches,
  activeBranchId,
  activeSceneIndex,
  onSelectScene,
}) => {
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);
  const [failedThumbs, setFailedThumbs] = useState<Record<string, boolean>>({});

  const presetPills = PRESET_SLOTS.map((slot) => {
    const found = branches.find((b) => slot.match(b.branch_id));
    return found ? { label: slot.label, branch: found } : null;
  }).filter((item): item is { label: string; branch: BranchState } => Boolean(item));

  const renderThumbTooltip = (sc: Scene, key: string) => {
    const thumbUrl = sc.media.cam_a_tail_frame_url;
    if (hoveredKey !== key || !thumbUrl || failedThumbs[thumbUrl]) return null;
    const scorePct = Math.round((sc.media.qa_score ?? 0) * 100);
    return (
      <div className="scene-thumb-tooltip" role="tooltip">
        <img
          src={thumbUrl}
          alt={`Scene ${sc.scene_id} preview`}
          width={120}
          height={68}
          onError={() =>
            setFailedThumbs((prev) =>
              prev[thumbUrl] ? prev : { ...prev, [thumbUrl]: true },
            )
          }
        />
        <div className="scene-thumb-meta">
          <span>S{sc.scene_id}</span>
          <span>QA {scorePct}%</span>
        </div>
      </div>
    );
  };

  return (
    <section className="branch-tree-panel">
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: '0.8rem',
          gap: 8,
          flexWrap: 'wrap',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <strong>🌿 4-Scene Causal Story Tree</strong>
          {presetPills.length >= 2 && (
            <div className="preset-branch-pills" role="group" aria-label="Quick branch switcher">
              {presetPills.map(({ label, branch }) => (
                <button
                  key={branch.branch_id}
                  type="button"
                  className={`pill-btn subtle ${branch.branch_id === activeBranchId ? 'active' : ''}`}
                  style={{ padding: '2px 8px', fontSize: '0.7rem' }}
                  onClick={() => onSelectScene(branch.branch_id, activeSceneIndex)}
                  title={getBranchLabelEn(branch)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
        <span style={{ color: 'var(--text-muted)', fontSize: '0.73rem' }}>
          Click any branch or scene to jump &amp; compare timelines
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
              title={getBranchLabelEn(branch)}
            >
              <div style={{ fontWeight: 700 }}>{getBranchLabelEn(branch)}</div>
              <div style={{ fontSize: '0.71rem', color: 'var(--text-muted)' }}>
                {branch.forked_from_scene
                  ? `Forked at Scene ${branch.forked_from_scene} · ${savedPct}% rerender saved`
                  : 'Primary Baseline Timeline'}
              </div>
            </button>

            {branch.scenes.map((sc, idx) => {
              const isSelected = isCurrentBranch && idx === activeSceneIndex;
              const isReused =
                Boolean(sc.reused_from_branch) ||
                (Boolean(branch.forked_from_scene) && !sc.needs_rerender);
              const isRerendered = Boolean(sc.needs_rerender);
              const { title } = getSceneTextEn(branch.branch_id, sc);
              const nodeKey = `${branch.branch_id}:${sc.scene_id}`;

              return (
                <div
                  key={sc.scene_id}
                  className="scene-node-wrap"
                  onMouseEnter={() => setHoveredKey(nodeKey)}
                  onMouseLeave={() => setHoveredKey((k) => (k === nodeKey ? null : k))}
                >
                  {renderThumbTooltip(sc, nodeKey)}
                  <button
                    type="button"
                    className={`scene-node-btn ${isSelected ? 'active' : ''} ${
                      isReused ? 'reused' : ''
                    }`}
                    onFocus={() => setHoveredKey(nodeKey)}
                    onBlur={() => setHoveredKey((k) => (k === nodeKey ? null : k))}
                    onClick={() => onSelectScene(branch.branch_id, idx)}
                  >
                    <div
                      style={{
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        fontSize: '0.71rem',
                        fontWeight: 700,
                        gap: 4,
                      }}
                    >
                      <span>Scene {sc.scene_id}</span>
                      <span
                        style={{
                          padding: '1px 6px',
                          borderRadius: 999,
                          fontSize: '0.67rem',
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
                        fontSize: '0.76rem',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {title}
                    </div>
                  </button>
                </div>
              );
            })}
          </div>
        );
      })}
    </section>
  );
};
