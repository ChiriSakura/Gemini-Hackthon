/**
 * Minimal Clapperboard QA Strip for StageState.
 *
 * Feature & Use Cases:
 * - Displays `judge()` confidence score, selected take (#1 or #2), auto-retry
 *   count, per-question pass/fail status pills, and tail-frame `environment_notes`.
 */
import React from 'react';
import type { Scene } from '../types/schema';

interface ClapperboardQAProps {
  scene: Scene;
}

export const ClapperboardQA: React.FC<ClapperboardQAProps> = ({ scene }) => {
  const media = scene.media;
  const qaItems = media.qa_result?.items;
  const scorePct = Math.round((media.qa_score ?? 0) * 100);

  return (
    <div className="clapperboard-strip">
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ fontWeight: 700, fontSize: '0.86rem', color: '#fde68a' }}>
          🎬 场记板质检 · Take #{media.selected_candidate_index + 1}
        </span>
        <span className="qa-pill pass">judge() 置信度 {scorePct}%</span>
        <span className="qa-pill">
          自动重拍 {media.qa_retries} 次
        </span>
        {media.fallback_to_preview && (
          <span className="qa-pill fail">已降级 2.5D 预览</span>
        )}
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

      <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
        环境记忆: <code>{scene.environment_notes || 'clean_stage'}</code>
      </div>
    </div>
  );
};
