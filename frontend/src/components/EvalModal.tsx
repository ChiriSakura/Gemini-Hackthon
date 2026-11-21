/**
 * Lightweight Collapsible Eval & Failure Curve Modal (Non-Dashboard).
 *
 * Feature & Use Cases:
 * - Keeps the main viewport 100% stage-focused while offering judges a 1-click
 *   empirical summary of StageState vs. Stateless Baseline, actor/scene failure
 *   curves, localized rerender savings, 360p vs 720p cost savings, 5-prop
 *   transparent cutout showcase, and embedded BE vs. HE comparison reel.
 */
import React, { useState } from 'react';

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
  { label: 'Cross-Cam Consistency', score: 0.929, detail: '26/28 pass' },
  { label: 'Prop Persistence', score: 1.0, detail: '1.000 across S2-S4' },
  { label: 'Decisions Calibration', score: 1.0, detail: '20/20 (TPR=1, TNR=1)' },
  { label: '360p vs 720p Savings', score: 0.714, detail: '$0.48 vs $1.68 (71.4%)' },
  { label: '3-Prop Stress Test', score: 0.815, detail: '39.33s total' },
  { label: 'Omni Flash Dual-Take', score: 1.0, detail: '17.1s 360p / 25.8s 720p' },
];

const SHOWCASE_PROPS = [
  { label: 'Brass Pocket Watch', url: '/assets/props/showcase_brass_pocket_watch.png' },
  { label: 'Crystal Shield', url: '/assets/props/showcase_crystal_shield.png' },
  { label: 'Glowing Quill', url: '/assets/props/showcase_glowing_quill.png' },
  { label: 'Origami Crane', url: '/assets/props/showcase_origami_crane.png' },
  { label: 'Star Lantern', url: '/assets/props/showcase_star_lantern.png' },
];

export const EvalModal: React.FC<EvalModalProps> = ({ open, onClose }) => {
  const [reelOpen, setReelOpen] = useState(false);
  const [reelError, setReelError] = useState(false);
  const [failedProps, setFailedProps] = useState<Record<string, boolean>>({});

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
            gap: 10,
          }}
        >
          <div>
            <strong style={{ fontSize: '1.02rem' }}>
              📊 Empirical Benchmark, Cutout Props &amp; BE/HE Reel
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
            <strong style={{ color: '#fda4af', fontSize: '1.06rem' }}>
              0.564 <span style={{ fontSize: '0.72rem' }}>(29.16s · FAIL)</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">StageState (WorldState)</span>
            <strong style={{ color: '#6ee7b7', fontSize: '1.06rem' }}>
              0.951 <span style={{ fontSize: '0.72rem' }}>(6.90s–9.65s · PASS)</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">360p Cost &amp; Rerender</span>
            <strong style={{ color: '#fde68a', fontSize: '1.06rem' }}>
              71.4% <span style={{ fontSize: '0.72rem' }}>cheaper · 66.7% skip</span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">Cross-Cam &amp; Props</span>
            <strong style={{ color: '#7dd3fc', fontSize: '1.06rem' }}>
              0.929 <span style={{ fontSize: '0.72rem' }}>(26/28) · 1.000 prop</span>
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
        <div className="eval-box" style={{ marginTop: 10 }}>
          <div className="eval-box-title">
            Verified Multimodal, Dual-Cam &amp; 360p/720p Media Benchmarks
          </div>
          <div className="eval-two-col" style={{ gap: 8 }}>
            {MEDIA_BENCHMARKS.map((m) => (
              <div
                key={m.label}
                className="bar-row"
                style={{ gridTemplateColumns: '132px 1fr 136px' }}
              >
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

        {/* 5-Prop Cutout Showcase Strip */}
        <div className="eval-box" style={{ marginTop: 10 }}>
          <div className="eval-box-title">
            Live Cutout Prop Showcase (Zero Green Halo · Persistent Registry)
          </div>
          <div className="showcase-prop-strip">
            {SHOWCASE_PROPS.map((prop) => (
              <div key={prop.label} className="showcase-prop-card">
                <div className="checkerboard-thumb">
                  {!failedProps[prop.url] ? (
                    <img
                      src={prop.url}
                      alt={prop.label}
                      onError={() =>
                        setFailedProps((prev) => ({ ...prev, [prop.url]: true }))
                      }
                    />
                  ) : (
                    <span style={{ fontSize: '1.1rem' }}>✨</span>
                  )}
                </div>
                <span className="showcase-prop-label">{prop.label}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Collapsible Side-by-Side Comparison Reel */}
        <div className="eval-box" style={{ marginTop: 10 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
            }}
          >
            <span className="eval-box-title" style={{ margin: 0 }}>
              🎬 Stitched BE vs. HE Side-by-Side Comparison Reel
            </span>
            <button
              type="button"
              className={`pill-btn ${reelOpen ? 'active' : 'blue'}`}
              onClick={() => {
                setReelError(false);
                setReelOpen((v) => !v);
              }}
            >
              {reelOpen ? 'Hide Reel' : '▶ Watch BE vs. HE Reel'}
            </button>
          </div>
          {reelOpen && (
            <div style={{ marginTop: 8 }}>
              {!reelError ? (
                <video
                  src="/assets/finals/demo_comparison_be_vs_he.mp4"
                  controls
                  autoPlay
                  playsInline
                  onError={() => setReelError(true)}
                  style={{
                    width: '100%',
                    maxHeight: 260,
                    borderRadius: 10,
                    background: '#000',
                  }}
                />
              ) : (
                <div className="warning-banner">
                  Comparison reel requires the FastAPI static server on port 8000 (
                  <code>/assets/finals/demo_comparison_be_vs_he.mp4</code>).
                </div>
              )}
            </div>
          )}
        </div>

        <div style={{ marginTop: 10, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          Sources: <code>eval/stress_report.json</code>,{' '}
          <code>backend/assets/eval_720p_vs_360p.json</code> ($0.48 vs $1.68 per 6-clip branch),{' '}
          <code>eval_dual_cam_stats.json</code>, <code>eval_prop_persistence.json</code>.
        </div>
      </div>
    </div>
  );
};
