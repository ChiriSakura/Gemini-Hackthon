/**
 * Save Tree of Scene Snapshots.
 *
 * Feature & Use Cases:
 * - Every saved run is a row of photo snapshots on the tree, and a live rewrite
 *   grows a new branch on it. Branches are glowing golden vines growing left to
 *   right from a single trunk at Scene 1; scenes shared with the parent are
 *   drawn once and the vine splits where a branch forks.
 * - Each scene is a small framed photo snapshot (its Camera A tail frame when
 *   the scene has media of its own) hanging from the vine with a slight tilt.
 *   The active snapshot is larger and brighter, unrendered scenes are faint
 *   dashed ghost frames, and ending snapshots carry a small BE/HE ribbon.
 * - Click (or focus + Enter) jumps to that branch and scene. Status, narration
 *   and the judge score only appear in the hover/focus card, never at a glance.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import type { BranchState, Scene } from '../types/schema';
import { getBranchLabelEn, getSceneTextEn } from '../utils/displayLocale';
import {
  findParentScene,
  hasOwnFinalMedia,
  sceneKey,
  summarizeJudge,
} from '../utils/sceneMedia';

interface StoryBranchTreeProps {
  branches: BranchState[];
  activeBranchId: string;
  activeSceneIndex: number;
  /** `branchId:sceneId` keys of scenes rendered during this browser session. */
  renderedSceneKeys?: Set<string>;
  /** Branches created by a live rewrite/continuation during this browser session. */
  liveBranchIds?: Set<string>;
  /** Controls whether the full multi-branch save tree is expanded or collapsed into the compact timeline rail. */
  expanded?: boolean;
  onToggleExpanded?: () => void;
  /** When false, hides pre-saved example branches from the expanded tree to keep the canvas clutter-free. */
  showSavedExamples?: boolean;
  onSelectScene: (branchId: string, sceneIndex: number) => void;
}

type SceneBadge = 'Reused' | 'Saved' | 'Live' | 'Not rendered' | 'Degraded';

interface TreeNode {
  key: string;
  branch: BranchState;
  scene: Scene;
  sceneIndex: number;
  row: number;
  col: number;
  ownMedia: boolean;
  badge: SceneBadge;
  title: string;
  narration: string;
  tilt: number;
  onPath: boolean;
  endTag: 'BE' | 'HE' | null;
  branchTag: 'Saved' | 'Live' | null;
}

interface TreeSegment {
  key: string;
  d: string;
  width: number;
  pending: boolean;
  lit: boolean;
}

// SVG user units: one column and one row are each 100 units; the canvas is
// stretched to the grid, so strokes use `vector-effect: non-scaling-stroke`.
const COL_UNITS = 100;
const ROW_UNITS = 100;
const HANG_X = 30;
const HANG_Y = 12;
const SEGMENT_WIDTHS = [3.6, 3, 2.4, 1.9];

function hashString(value: string): number {
  let h = 0;
  for (let i = 0; i < value.length; i++) {
    h = (h * 31 + value.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

/** Deterministic tilt between roughly -3 and +3 degrees, never flat. */
function tiltFor(key: string): number {
  const raw = (hashString(key) % 600) / 100 - 3;
  if (Math.abs(raw) < 1.2) return raw < 0 ? -1.8 : 1.8;
  return Number(raw.toFixed(1));
}

export const StoryBranchTree: React.FC<StoryBranchTreeProps> = ({
  branches,
  activeBranchId,
  activeSceneIndex,
  renderedSceneKeys,
  liveBranchIds,
  showSavedExamples = false,
  onSelectScene,
}) => {
  const [tip, setTip] = useState<{ key: string; x: number; y: number } | null>(null);
  const [failedThumbs, setFailedThumbs] = useState<Record<string, boolean>>({});
  const nodeRefs = useRef(new Map<string, HTMLButtonElement>());

  const visibleBranches = useMemo(() => {
    if (showSavedExamples) return branches;
    const filtered = branches.filter(
      (b) =>
        b.branch_id === activeBranchId ||
        b.branch_id === 'branch_v1' ||
        liveBranchIds?.has(b.branch_id),
    );
    return filtered.length > 0 ? filtered : branches.slice(0, 1);
  }, [branches, activeBranchId, liveBranchIds, showSavedExamples]);

  const layout = useMemo(() => {
    const byId = new Map(visibleBranches.map((b) => [b.branch_id, b]));
    const rowOf = new Map(visibleBranches.map((b, idx) => [b.branch_id, idx]));
    const cols = Math.max(1, ...visibleBranches.map((b) => b.scenes.length));

    // First scene number a branch draws itself; earlier scenes are the parent's
    const firstOwnScene = (b: BranchState): number => {
      if (!b.parent_branch_id || !byId.has(b.parent_branch_id)) return 1;
      return Math.max(1, b.forked_from_scene ?? 1);
    };
    // Which branch actually draws scene `sceneId` on the way to branch `b`
    const ownerOf = (b: BranchState, sceneId: number, depth = 0): BranchState => {
      const parent = b.parent_branch_id ? byId.get(b.parent_branch_id) : undefined;
      if (parent && depth < 12 && sceneId < firstOwnScene(b)) {
        return ownerOf(parent, sceneId, depth + 1);
      }
      return b;
    };
    const hang = (col: number, row: number) => ({
      x: (col - 1) * COL_UNITS + HANG_X,
      y: row * ROW_UNITS + HANG_Y,
    });
    const curve = (
      from: { x: number; y: number },
      to: { x: number; y: number },
      seed: string,
    ): string => {
      const dx = to.x - from.x;
      if (from.y !== to.y) {
        const dy = to.y - from.y;
        return `C ${from.x + dx * 0.3} ${from.y + dy * 0.62}, ${to.x - dx * 0.5} ${to.y}, ${to.x} ${to.y}`;
      }
      const wobble = hashString(seed) % 2 === 0 ? 7 : -7;
      return `C ${from.x + dx * 0.5} ${from.y + wobble}, ${to.x - dx * 0.5} ${to.y - wobble}, ${to.x} ${to.y}`;
    };
    const startPoint = (b: BranchState, sceneId: number) => {
      if (sceneId <= 1) {
        const hasParent = Boolean(b.parent_branch_id && byId.has(b.parent_branch_id));
        const trunkRow = hasParent ? 0 : (rowOf.get(b.branch_id) ?? 0);
        return { x: 0, y: trunkRow * ROW_UNITS + HANG_Y + 6 };
      }
      const owner = ownerOf(b, sceneId - 1);
      return hang(sceneId - 1, rowOf.get(owner.branch_id) ?? 0);
    };

    const active = byId.get(activeBranchId) ?? visibleBranches[0];
    const litKeys = new Set<string>();
    let sparklePath = '';
    if (active) {
      for (let sceneId = 1; sceneId <= active.scenes.length; sceneId++) {
        const owner = ownerOf(active, sceneId);
        litKeys.add(sceneKey(owner.branch_id, sceneId));
        const from = startPoint(owner, sceneId);
        const to = hang(sceneId, rowOf.get(owner.branch_id) ?? 0);
        if (sceneId === 1) sparklePath = `M ${from.x} ${from.y} `;
        sparklePath += `${curve(from, to, sceneKey(owner.branch_id, sceneId))} `;
      }
    }

    const nodes: TreeNode[] = [];
    const segments: TreeSegment[] = [];
    visibleBranches.forEach((branch, row) => {
      const first = firstOwnScene(branch);
      let taggedBranch = false;
      branch.scenes.forEach((scene, sceneIndex) => {
        if (scene.scene_id < first) return;
        const key = sceneKey(branch.branch_id, scene.scene_id);
        const ownMedia = hasOwnFinalMedia(
          scene,
          findParentScene(branch, branches, scene.scene_id),
        );
        const badge: SceneBadge = scene.media.fallback_to_preview
          ? 'Degraded'
          : !ownMedia
            ? 'Not rendered'
            : scene.reused_from_branch
              ? 'Reused'
              : renderedSceneKeys?.has(key)
                ? 'Live'
                : 'Saved';
        const text = getSceneTextEn(branch.branch_id, scene);
        const col = sceneIndex + 1;
        const branchTag = taggedBranch
          ? null
          : liveBranchIds?.has(branch.branch_id)
            ? 'Live'
            : 'Saved';
        taggedBranch = true;
        nodes.push({
          key,
          branch,
          scene,
          sceneIndex,
          row,
          col,
          ownMedia,
          badge,
          title: text.title,
          narration: text.narration,
          tilt: tiltFor(key),
          onPath: litKeys.has(key),
          // W2-V2-01: Remove prominent BE/HE ribbons from main canvas
          endTag: null,
          branchTag,
        });
        const from = startPoint(branch, scene.scene_id);
        const to = hang(col, row);
        segments.push({
          key,
          d: `M ${from.x} ${from.y} ${curve(from, to, key)}`,
          width: SEGMENT_WIDTHS[Math.min(col - 1, SEGMENT_WIDTHS.length - 1)],
          pending: !ownMedia,
          lit: litKeys.has(key),
        });
      });
    });

    return { cols, rows: visibleBranches.length, nodes, segments, sparklePath };
  }, [visibleBranches, branches, activeBranchId, renderedSceneKeys, liveBranchIds]);

  const activeKey = (() => {
    const active = branches.find((b) => b.branch_id === activeBranchId) ?? branches[0];
    const scene = active?.scenes[activeSceneIndex];
    return active && scene ? sceneKey(active.branch_id, scene.scene_id) : null;
  })();

  // Keep the active branch's own snapshots in view when the tree scrolls
  useEffect(() => {
    const own = layout.nodes.find((n) => n.branch.branch_id === activeBranchId);
    const el = own ? nodeRefs.current.get(own.key) : null;
    el?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' });
  }, [activeBranchId, layout.nodes]);

  const showTip = (node: TreeNode, el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    const x = Math.min(Math.max(rect.left + rect.width / 2, 160), window.innerWidth - 160);
    setTip({ key: node.key, x, y: rect.top - 10 });
  };

  const tipNode = tip ? layout.nodes.find((n) => n.key === tip.key) : null;
  const tipJudge = tipNode ? summarizeJudge(tipNode.scene, tipNode.ownMedia) : null;

  const style = {
    '--cols': layout.cols,
    '--rows': Math.max(1, layout.rows),
  } as React.CSSProperties;

  return (
    <nav className="tree" aria-label="Story tree" style={style}>
      <div className="tree-inner">
        <div className="tree-head" aria-hidden="true">
          {Array.from({ length: layout.cols }, (_, i) => (
            <span key={i}>Scene {i + 1}</span>
          ))}
        </div>
        <div className="tree-canvas">
          <svg
            className="tree-links"
            viewBox={`0 0 ${layout.cols * COL_UNITS} ${Math.max(1, layout.rows) * ROW_UNITS}`}
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <defs>
              <linearGradient
                id="vine-gold"
                gradientUnits="userSpaceOnUse"
                x1="0"
                y1="0"
                x2={layout.cols * COL_UNITS}
                y2="0"
              >
                <stop offset="0" stopColor="#f5c451" />
                <stop offset="0.6" stopColor="#ffe6a3" />
                <stop offset="1" stopColor="#fff6dc" />
              </linearGradient>
            </defs>
            {layout.segments.map((seg) => (
              <path
                key={seg.key}
                d={seg.d}
                stroke="url(#vine-gold)"
                strokeWidth={seg.lit ? seg.width + 0.8 : seg.width}
                className={`${seg.lit ? 'lit' : ''} ${seg.pending ? 'pending' : ''}`}
              />
            ))}
            {layout.sparklePath &&
              [0, 1, 2].map((i) => (
                <path key={i} className="tree-sparkle" d="M0 0h0.01">
                  <animateMotion
                    dur="9s"
                    begin={`${i * 3}s`}
                    repeatCount="indefinite"
                    path={layout.sparklePath}
                  />
                  <animate
                    attributeName="opacity"
                    values="0;1;0.4;1;0"
                    dur="9s"
                    begin={`${i * 3}s`}
                    repeatCount="indefinite"
                  />
                </path>
              ))}
          </svg>

          <div className="tree-grid">
            {layout.nodes.map((node) => {
              const isActive = node.key === activeKey;
              const onActiveBranch = node.branch.branch_id === activeBranchId;
              const thumbUrl = node.ownMedia ? node.scene.media.cam_a_tail_frame_url : null;
              const showImg = thumbUrl !== null && !failedThumbs[thumbUrl];
              return (
                <button
                  key={node.key}
                  type="button"
                  ref={(el) => {
                    if (el) nodeRefs.current.set(node.key, el);
                    else nodeRefs.current.delete(node.key);
                  }}
                  className={`snap ${isActive ? 'active' : ''} ${
                    onActiveBranch ? 'on-branch' : ''
                  } ${node.onPath ? 'on-path' : ''} ${node.ownMedia ? '' : 'ghost'} ${node.badge === 'Degraded' ? 'degraded' : ''}`}
                  style={
                    {
                      gridColumn: node.col,
                      gridRow: node.row + 1,
                      '--tilt': `${node.tilt}deg`,
                    } as React.CSSProperties
                  }
                  aria-current={isActive ? 'true' : undefined}
                  aria-label={`${getBranchLabelEn(node.branch)} — Scene ${node.scene.scene_id}: ${node.title} (${node.badge})`}
                  onClick={() => onSelectScene(node.branch.branch_id, node.sceneIndex)}
                  onMouseEnter={(e) => showTip(node, e.currentTarget)}
                  onMouseLeave={() => setTip((t) => (t?.key === node.key ? null : t))}
                  onFocus={(e) => showTip(node, e.currentTarget)}
                  onBlur={() => setTip((t) => (t?.key === node.key ? null : t))}
                >
                  <span className="snap-photo">
                    {showImg && thumbUrl ? (
                      <img
                        src={thumbUrl}
                        alt=""
                        loading="lazy"
                        onError={() =>
                          setFailedThumbs((prev) =>
                            prev[thumbUrl] ? prev : { ...prev, [thumbUrl]: true },
                          )
                        }
                      />
                    ) : (
                      <span className={`snap-blank mood-${node.scene.lighting}`}>
                        {node.scene.scene_id}
                      </span>
                    )}
                    {node.endTag && (
                      <span className={`snap-ribbon ${node.endTag === 'BE' ? 'be' : 'he'}`}>
                        {node.endTag}
                      </span>
                    )}
                    {node.branchTag && (
                      <span className={`snap-tag ${node.branchTag === 'Live' ? 'live' : ''}`}>
                        {node.branchTag}
                      </span>
                    )}
                  </span>
                  <span className="snap-title">{node.title}</span>
                </button>
              );
            })}
          </div>
        </div>
      </div>

      {tip && tipNode && tipJudge && (
        <div className="tree-tip" role="tooltip" style={{ left: tip.x, top: tip.y }}>
          <strong>
            Scene {tipNode.scene.scene_id} · {tipNode.title}
          </strong>
          <div>{tipNode.narration}</div>
          <div className="tree-tip-meta">
            {getBranchLabelEn(tipNode.branch)} ·{' '}
            {liveBranchIds?.has(tipNode.branch.branch_id) ? 'Live branch' : 'Saved run'} ·{' '}
            {tipNode.badge}
            {tipNode.scene.reused_from_branch
              ? ` from ${tipNode.scene.reused_from_branch}`
              : ''}{' '}
            · {tipJudge.judged ? `QA ${tipJudge.scorePct}%` : 'QA not judged'}
          </div>
        </div>
      )}
    </nav>
  );
};
