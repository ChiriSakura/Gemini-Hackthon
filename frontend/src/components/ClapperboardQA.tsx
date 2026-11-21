/**
 * Minimal Clapperboard QA Strip + Rule Memory Pill & Stage Prop Tray.
 *
 * Feature & Use Cases:
 * - Displays `judge()` confidence score, selected take (#1 or #2), auto-retry
 *   count, per-question pass/fail status pills, and active continuity rules.
 * - Displays a compact stage prop tray showing registered transparent props.
 */
import React from 'react';
import type { PropRegistryItem, Scene } from '../types/schema';
import { getPropNameEn } from '../utils/displayLocale';

interface ClapperboardQAProps {
  scene: Scene;
  rules: string[];
  propsRegistry: Record<string, PropRegistryItem>;
}

export const ClapperboardQA: React.FC<ClapperboardQAProps> = ({
  scene,
  rules,
  propsRegistry,
}) => {
  const media = scene.media;
  const qaItems = media.qa_result?.items;
  const scorePct = Math.round((media.qa_score ?? 0) * 100);
  const propEntries = Object.entries(propsRegistry);

  return (
    <div className="clapperboard-strip">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: '0.82rem', color: '#fde68a' }}>
          🎬 Clapperboard · Take #{media.selected_candidate_index + 1}
        </span>
        <span className="qa-pill pass">judge() {scorePct}%</span>
        <span className="qa-pill">Retries: {media.qa_retries}</span>
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
          ? qaItems.map((item, idx) => (
              <span
                key={idx}
                className={`qa-pill ${item.passed ? 'pass' : 'fail'}`}
                title={item.reason ?? item.question}
              >
                {item.passed ? '✓' : '✗'} Q{idx + 1} (
                {Math.round(item.confidence * 100)}%)
              </span>
            ))
          : scene.qa_questions.map((q, idx) => (
              <span key={idx} className="qa-pill pass" title={q}>
                ✓ Q{idx + 1}
              </span>
            ))}
      </div>

      {propEntries.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
            Props:
          </span>
          {propEntries.map(([id, item]) => {
            const label = getPropNameEn(id, item);
            return (
              <span
                key={id}
                className="qa-pill"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                title={`${label} (${item.origin})`}
              >
                <img
                  src={item.image_url}
                  alt={label}
                  style={{ width: 15, height: 15, objectFit: 'contain' }}
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = 'none';
                  }}
                />
                {label}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};
