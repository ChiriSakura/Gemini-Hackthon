/**
 * Minimal Clapperboard QA Strip + Rule Memory Pill & Stage Prop Tray.
 *
 * Feature & Use Cases:
 * - Displays `judge()` confidence score, selected take (#1 or #2), auto-retry
 *   count, per-question pass/fail status pills, and tail-frame `environment_notes`.
 * - Displays a subtle Rule Memory count chip (`N rules active`) fetched from
 *   `GET /api/director/rules` with hover tooltip details.
 * - Displays a compact stage prop tray showing registered transparent props.
 */
import React from 'react';
import type { PropRegistryItem, Scene } from '../types/schema';

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
        <span style={{ fontWeight: 700, fontSize: '0.84rem', color: '#fde68a' }}>
          🎬 场记板 · Take #{media.selected_candidate_index + 1}
        </span>
        <span className="qa-pill pass">judge() {scorePct}%</span>
        <span className="qa-pill">重拍 {media.qa_retries} 次</span>
        {media.fallback_to_preview && (
          <span className="qa-pill fail">已降级 (2.5D Preview)</span>
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

      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        {propEntries.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              道具栏:
            </span>
            {propEntries.map(([id, item]) => (
              <span
                key={id}
                className="qa-pill"
                style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
                title={`${item.name} (${item.origin})`}
              >
                <img
                  src={item.image_url}
                  alt={item.name}
                  style={{ width: 16, height: 16, objectFit: 'contain' }}
                  onError={(e) => {
                    (e.currentTarget as HTMLImageElement).style.display = 'none';
                  }}
                />
                {item.name}
              </span>
            ))}
          </div>
        )}
        <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>
          环境: <code>{scene.environment_notes || 'clean_stage'}</code>
        </div>
      </div>
    </div>
  );
};
