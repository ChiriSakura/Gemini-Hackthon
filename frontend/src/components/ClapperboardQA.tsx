/**
 * Slim Clapperboard QA Line with Expandable Detail.
 *
 * Feature & Use Cases:
 * - At a glance shows only one small dot per judge question (green pass, red
 *   fail, grey not judged). No numbers are shown until the line is opened.
 * - Clicking the line expands the per-question detail (camera, question,
 *   confidence, reason), the overall `judge()` score, selected take, retries,
 *   the scene cues (lighting, voice, gesture, environment), active continuity
 *   rules and the stage prop tray with live per-scene states.
 * - When the scene has no judge result of its own, every question is shown in a
 *   neutral "not judged" state and the score reads `judge() —`.
 */
import React, { useEffect, useState } from 'react';
import type { PropRegistryItem, Scene } from '../types/schema';
import { getPropNameEn } from '../utils/displayLocale';
import { summarizeJudge } from '../utils/sceneMedia';
import { IconChevronDown, IconChevronUp, IconClapper } from './Icons';

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
  const [open, setOpen] = useState(false);
  const [failedThumbUrls, setFailedThumbUrls] = useState<Record<string, boolean>>({});
  const media = scene.media;
  const judge = summarizeJudge(scene, hasOwnRender);
  const qaItems = judge.judged ? (media.qa_result?.items ?? []) : [];
  const propEntries = Object.entries(propsRegistry);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const resolveLivePropState = (propId: string, fallbackState: string): string => {
    const heldByActor = scene.actors.some((a) => a.holding_prop === propId);
    if (heldByActor) return 'held';
    const stageProp = scene.stage_props.find((p) => p.id === propId);
    return stageProp?.state ?? fallbackState;
  };

  const summaryTitle = judge.judged
    ? `judge() ${judge.scorePct}% · ${judge.passedCount}/${judge.total} checks passed — click for details`
    : 'Not judged yet — click for details';

  if (cleanCaptureMode) return null;

  return (
    <div className="qa-wrap">
      {open && (
        <div className="qa-detail" role="region" aria-label="Judge details for this scene">
          <div>
            <h4>
              {judge.judged
                ? `Omni Flash render checks · judge() ${judge.scorePct}% · take ${media.selected_candidate_index + 1} · retries ${media.qa_retries}`
                : 'Checks · judge() — · not judged yet'}
            </h4>
            {judge.judged
              ? qaItems.map((item, idx) => {
                  const parsed = parseCameraPrefix(item.question);
                  return (
                    <div key={idx} className="qa-row">
                      <span className={`dot ${item.passed ? 'pass' : 'fail'}`} />
                      <span className="qa-cam">{parsed.cam ?? ''}</span>
                      <span>
                        {parsed.text}
                        {item.reason && <span className="qa-reason">{item.reason}</span>}
                      </span>
                      <span className="qa-conf">{Math.round(item.confidence * 100)}%</span>
                    </div>
                  );
                })
              : scene.qa_questions.map((q, idx) => {
                  const parsed = parseCameraPrefix(q);
                  return (
                    <div key={idx} className="qa-row">
                      <span className="dot" />
                      <span className="qa-cam">{parsed.cam ?? ''}</span>
                      <span>{parsed.text}</span>
                      <span className="qa-conf">—</span>
                    </div>
                  );
                })}
          </div>

          <div>
            <h4>Scene cues</h4>
            <dl className="qa-kv">
              <dt>Lighting</dt>
              <dd>{scene.lighting}</dd>
              <dt>Voice</dt>
              <dd>{scene.vocal_emotion}</dd>
              <dt>Gesture</dt>
              <dd>{scene.gesture_intent}</dd>
              <dt>Environment</dt>
              <dd>{scene.environment_notes || 'clean_stage'}</dd>
            </dl>

            <h4>{rules.length} continuity rules</h4>
            <ol className="qa-list">
              {rules.map((rule, idx) => (
                <li key={idx}>{rule}</li>
              ))}
            </ol>

            {propEntries.length > 0 && (
              <>
                <h4>Props</h4>
                <div className="qa-props">
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
                    return (
                      <span
                        key={id}
                        className="qa-prop"
                        title={`${label} · state=${liveState} · origin=${item.origin}`}
                      >
                        {showThumbImg && (
                          <img
                            src={resolvedThumbUrl}
                            alt=""
                            onError={() => {
                              setFailedThumbUrls((prev) =>
                                prev[resolvedThumbUrl]
                                  ? prev
                                  : { ...prev, [resolvedThumbUrl]: true },
                              );
                            }}
                          />
                        )}
                        <span>{label}</span>
                        <span className="prop-state-tag">{liveState}</span>
                      </span>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      <button
        type="button"
        className="qa-line"
        aria-expanded={open}
        aria-label={summaryTitle}
        title={summaryTitle}
        onClick={() => setOpen((v) => !v)}
      >
        <IconClapper size={15} />
        <span className="qa-dots">
          {judge.judged
            ? qaItems.map((item, idx) => (
                <span key={idx} className={`dot ${item.passed ? 'pass' : 'fail'}`} />
              ))
            : scene.qa_questions.map((_, idx) => <span key={idx} className="dot" />)}
        </span>
        {!judge.judged && <span>Not judged yet</span>}
        {media.fallback_to_preview && <span className="qa-score fail">Degraded to 2.5D preview</span>}
        <span className="qa-grow" />
        {open ? <IconChevronDown size={15} /> : <IconChevronUp size={15} />}
      </button>
    </div>
  );
};
