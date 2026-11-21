/**
 * Minimal Clapperboard QA Strip + Rule Memory Pill & Interactive Stage Prop Tray.
 *
 * Feature & Use Cases:
 * - Displays `judge()` confidence score, selected take (#1 or #2), auto-retry
 *   count, per-question pass/fail status pills, and active continuity rules.
 * - When the scene has no judge result of its own, every question is shown in a
 *   neutral "not judged" state and the score reads `judge() —`.
 * - Displays an interactive Stage Prop Tray with crisp thumbnails (automatically
 *   switching to `/assets/props/crystal_vase_broken.png` when broken) and live
 *   per-scene state badges (`intact` / `broken` / `held` / `glowing`).
 */
import React, { useState } from 'react';
import type { PropRegistryItem, Scene } from '../types/schema';
import { getPropNameEn } from '../utils/displayLocale';
import { summarizeJudge } from '../utils/sceneMedia';

function getPropFallbackEmoji(propId: string): string {
  if (propId.includes('umbrella')) return '☂️';
  if (propId.includes('vase')) return '🏺';
  if (propId.includes('star')) return '⭐';
  return '✨';
}

function parseCameraPrefix(question: string): { cam: 'CAM A' | 'CAM B' | null; text: string } {
  const match = question.match(/^\s*\[(CAM\s*[AB])\]\s*(.*)$/i);
  if (!match) return { cam: null, text: question };
  const normalized = match[1].toUpperCase().replace(/\s+/, ' ') as 'CAM A' | 'CAM B';
  return { cam: normalized, text: match[2] || question };
}

interface ClapperboardQAProps {
  scene: Scene;
  rules: string[];
  propsRegistry: Record<string, PropRegistryItem>;
  cleanCaptureMode?: boolean;
  /** False when the scene only carries media/QA copied from its parent branch. */
  hasOwnRender?: boolean;
}

export const ClapperboardQA: React.FC<ClapperboardQAProps> = ({
  scene,
  rules,
  propsRegistry,
  cleanCaptureMode = false,
  hasOwnRender = true,
}) => {
  const [failedThumbUrls, setFailedThumbUrls] = useState<Record<string, boolean>>({});
  const media = scene.media;
  const judge = summarizeJudge(scene, hasOwnRender);
  const qaItems = judge.judged ? media.qa_result?.items : undefined;
  const propEntries = Object.entries(propsRegistry);

  const resolveLivePropState = (propId: string, fallbackState: string): string => {
    const heldByActor = scene.actors.some((a) => a.holding_prop === propId);
    if (heldByActor) return 'held';
    const stageProp = scene.stage_props.find((p) => p.id === propId);
    return stageProp?.state ?? fallbackState;
  };

  return (
    <div className="clapperboard-strip">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: '0.82rem', color: '#fde68a' }}>
          🎬 Clapperboard ·{' '}
          {judge.judged ? `Take #${media.selected_candidate_index + 1}` : 'no take judged yet'}
        </span>
        {judge.judged ? (
          <span
            className={`qa-pill ${judge.allPassed ? 'pass' : 'fail'}`}
            title={`${judge.passedCount}/${judge.total} checks passed`}
          >
            judge() {judge.scorePct}%
          </span>
        ) : (
          <span className="qa-pill" title="No judge result for this scene yet">
            judge() —
          </span>
        )}
        {judge.judged && <span className="qa-pill">Retries: {media.qa_retries}</span>}
        {media.fallback_to_preview && (
          <span className="qa-pill fail">Degraded (2.5D Preview)</span>
        )}
        <span
          className="qa-pill"
          style={{ borderColor: 'rgba(167, 139, 250, 0.45)', color: '#ddd6fe' }}
          title={rules.map((r, i) => `${i + 1}. ${r}`).join('\n')}
        >
          🧠 {rules.length} rules active
        </span>
      </div>

      <div className="qa-pills">
        {qaItems && qaItems.length > 0
          ? qaItems.map((item, idx) => {
              const parsed = parseCameraPrefix(item.question);
              return (
                <span
                  key={idx}
                  className={`qa-pill ${item.passed ? 'pass' : 'fail'}`}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  title={item.reason ? `${parsed.text} — ${item.reason}` : parsed.text}
                >
                  {!cleanCaptureMode && parsed.cam && (
                    <span className={`cam-mini-badge ${parsed.cam === 'CAM A' ? 'cam-a' : 'cam-b'}`}>
                      {parsed.cam}
                    </span>
                  )}
                  <span>
                    {item.passed ? '✓' : '✗'} Q{idx + 1} (
                    {Math.round(item.confidence * 100)}%)
                  </span>
                </span>
              );
            })
          : scene.qa_questions.map((q, idx) => {
              const parsed = parseCameraPrefix(q);
              return (
                <span
                  key={idx}
                  className="qa-pill"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 4,
                    color: '#94a3b8',
                  }}
                  title={`${parsed.text} — not judged yet`}
                >
                  {!cleanCaptureMode && parsed.cam && (
                    <span className={`cam-mini-badge ${parsed.cam === 'CAM A' ? 'cam-a' : 'cam-b'}`}>
                      {parsed.cam}
                    </span>
                  )}
                  <span>Q{idx + 1} · not judged</span>
                </span>
              );
            })}
      </div>

      {propEntries.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            Props:
          </span>
          {propEntries.map(([id, item]) => {
            const label = getPropNameEn(id, item);
            const liveState = resolveLivePropState(id, item.state);
            const preferredThumbUrl =
              id === 'crystal_vase' && liveState === 'broken'
                ? '/assets/props/crystal_vase_broken.png'
                : item.image_url;
            const resolvedThumbUrl = failedThumbUrls[preferredThumbUrl]
              ? item.image_url
              : preferredThumbUrl;
            const showThumbImg =
              Boolean(resolvedThumbUrl) && !failedThumbUrls[resolvedThumbUrl];
            const stateClass =
              liveState === 'broken'
                ? 'fail'
                : liveState === 'glowing' || liveState === 'intact'
                  ? 'pass'
                  : '';

            return (
              <span
                key={id}
                className={`qa-pill ${stateClass}`}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
                title={`${label} · state=${liveState} · origin=${item.origin}`}
              >
                {showThumbImg ? (
                  <img
                    src={resolvedThumbUrl}
                    alt={label}
                    style={{ width: 18, height: 18, objectFit: 'contain' }}
                    onError={() => {
                      setFailedThumbUrls((prev) =>
                        prev[resolvedThumbUrl]
                          ? prev
                          : { ...prev, [resolvedThumbUrl]: true },
                      );
                    }}
                  />
                ) : (
                  <span aria-hidden="true">{getPropFallbackEmoji(id)}</span>
                )}
                <span>{label}</span>
                <span className="prop-state-tag">{liveState}</span>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};
