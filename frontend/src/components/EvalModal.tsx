/**
 * Lightweight Collapsible Eval & Failure Curve Modal (Non-Dashboard).
 *
 * Feature & Use Cases:
 * - Keeps the main viewport 100% stage-focused while offering judges a 1-click
 *   empirical summary of StageState vs. Stateless Baseline, actor/scene failure
 *   curves, localized rerender savings, and P1/P2 multimodal media benchmarks.
 */
import React from 'react';

interface EvalModalProps {
  open: boolean;
  onClose: () => void;
}

const ACTOR_CURVE = [
  { actors: '1 Actor (Lia)', score: 0.885, latency: '11.65s' },
  { actors: '2 Actors (Lia + Nyx)', score: 0.855, latency: '11.04s' },
  { actors: '3 Actors (+ Owl Keeper)', score: 0.82, latency: '10.08s' },
  { actors: '4 Actors (+ Clockmaker)', score: 0.82, latency: '10.67s' },
];

const SCENE_CURVE = [
  { scenes: '2 Scenes', noRules: 0.951, withRules: 0.947 },
  { scenes: '4 Scenes', noRules: 0.952, withRules: 0.901 },
  { scenes: '6 Scenes', noRules: 0.937, withRules: 0.699 },
  { scenes: '8 Scenes', noRules: 0.95, withRules: 0.946 },
];

const MEDIA_BENCHMARKS = [
  { label: 'Cross-Cam Consistency', score: 0.893, detail: '25/28 pass' },
  { label: 'Decisions Calibration', score: 1.0, detail: '20/20 (TPR=1.0, TNR=1.0)' },
  { label: '3-Prop Stress Test', score: 0.815, detail: '39.33s total' },
  { label: 'Omni Flash Dual-Take', score: 1.0, detail: '14/14 pass (18.64s gen)' },
];

export const EvalModal: React.FC<EvalModalProps> = ({ open, onClose }) => {
  if (!open) return null;

  return (
    <div className="modal-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 12,
          }}
        >
          <div>
            <strong style={{ fontSize: '1.02rem' }}>
              📊 Empirical Benchmark &amp; Failure Curves
            </strong>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Gemini 4 Neon + Hydrogen + Decisions API v1beta + Omni Flash (live verified)
            </div>
          </div>
          <button type="button" className="pill-btn" onClick={onClose}>
            ✕ Close
          </button>
        </div>

        {/* Highlight KPIs */}
        <div className="eval-kpi-row">
          <div className="eval-kpi">
            <span className="eval-kpi-label">Stateless Baseline</span>
            <strong style={{ color: '#fda4af', fontSize: '1.1rem' }}>
              0.564 <span style={{ fontSize: '0.73rem' }}>(29.16s · FAIL)</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">StageState (WorldState)</span>
            <strong style={{ color: '#6ee7b7', fontSize: '1.1rem' }}>
              0.951 <span style={{ fontSize: '0.73rem' }}>(6.90s–9.65s · PASS)</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">Localized Rewrite Savings</span>
            <strong style={{ color: '#fde68a', fontSize: '1.1rem' }}>
              66.7% <span style={{ fontSize: '0.73rem' }}>scenes skipped</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">Decisions Calibration</span>
            <strong style={{ color: '#7dd3fc', fontSize: '1.1rem' }}>
              1.000 <span style={{ fontSize: '0.73rem' }}>(20/20 · 0.93–1.54s)</span>
            </strong>
          </div>
        </div>

        <div className="eval-two-col">
          {/* 1->4 Actor Curve */}
          <div className="eval-box">
            <div className="eval-box-title">Actor Scaling Curve (1 → 4 Actors)</div>
            {ACTOR_CURVE.map((row) => (
              <div key={row.actors} className="bar-row">
                <span className="bar-label">{row.actors}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill blue"
                    style={{ width: `${Math.round(row.score * 100)}%` }}
                  />
                </div>
                <span className="bar-val">
                  {row.score.toFixed(3)} ({row.latency})
                </span>
              </div>
            ))}
          </div>

          {/* 2->8 Scene Curve */}
          <div className="eval-box">
            <div className="eval-box-title">
              Scene Scaling Curve (2 → 8 Scenes · Honest Regressions)
            </div>
            {SCENE_CURVE.map((row) => (
              <div key={row.scenes} className="bar-row">
                <span className="bar-label">{row.scenes}</span>
                <div className="bar-track">
                  <div
                    className={`bar-fill ${row.withRules < 0.75 ? 'warn' : 'green'}`}
                    style={{ width: `${Math.round(row.withRules * 100)}%` }}
                  />
                </div>
                <span className="bar-val">
                  {row.withRules.toFixed(3)}{' '}
                  {row.withRules < 0.75 ? '⚠ caught' : '✓'}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* P2 Verified Multimodal & Media Benchmarks */}
        <div className="eval-box" style={{ marginTop: 12 }}>
          <div className="eval-box-title">
            Verified Multimodal &amp; Dual-Camera Media Benchmarks
          </div>
          <div className="eval-two-col" style={{ gap: 8 }}>
            {MEDIA_BENCHMARKS.map((m) => (
              <div key={m.label} className="bar-row" style={{ gridTemplateColumns: '140px 1fr 125px' }}>
                <span className="bar-label">{m.label}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill green"
                    style={{ width: `${Math.round(m.score * 100)}%` }}
                  />
                </div>
                <span className="bar-val">
                  {m.score.toFixed(3)} · {m.detail}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ marginTop: 10, fontSize: '0.73rem', color: 'var(--text-muted)' }}>
          Sources: <code>eval/run_eval.py</code>, <code>eval/stress_report.json</code>, and{' '}
          <code>backend/assets/eval_media_stats.jsonl</code>. Decisions API v1beta:{' '}
          <code>0.93s–1.54s</code>/call · Omni Flash 360p/4s: <code>18.64s</code> gen.
        </div>
      </div>
    </div>
  );
};
