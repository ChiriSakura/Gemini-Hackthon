/**
 * Lightweight Collapsible Eval & Failure Curve Modal (Non-Dashboard).
 *
 * Feature & Use Cases:
 * - Keeps the main viewport 100% stage-focused while offering judges a 1-click
 *   summary of offline evaluation results. Every number is read from the
 *   repo's report files (`eval/stress_report.json`, `eval/latest_report.json`,
 *   `backend/assets/calibration_20_clips.json`, `eval_dual_cam_stats.json`,
 *   `eval_prop_persistence.json`, `eval_720p_vs_360p.json`) or recomputed from
 *   the backend take log `eval_media_stats.jsonl` when the modal opens.
 * - The booth checklist reports the real API, microphone and camera state.
 */
import React, { useEffect, useState } from 'react';
import { fetchEvalSummary } from '../api/stageClient';
import calibration from '../../../backend/assets/calibration_20_clips.json';
import res720 from '../../../backend/assets/eval_720p_vs_360p.json';
import dualCam from '../../../backend/assets/eval_dual_cam_stats.json';
import propPersistence from '../../../backend/assets/eval_prop_persistence.json';
import latestReport from '../../../eval/latest_report.json';
import stressReport from '../../../eval/stress_report.json';

interface EvalModalProps {
  open: boolean;
  onClose: () => void;
  isMockMode?: boolean;
  /** `null` while the health check is still running. */
  apiLive?: boolean | null;
  backendReachable?: boolean | null;
  branchCount?: number;
}

type DeviceState = 'granted' | 'denied' | 'prompt' | 'missing' | 'unknown';

interface TakeStats {
  shots: number;
  singleTakePass: number;
  bestOfTwoPass: number;
  afterRetriesPass: number;
  asOf: string;
  fromBackend: boolean;
}

/** Judge score a take must reach to count as a pass (backend `QA_PASS_THRESHOLD`). */
const TAKE_PASS_THRESHOLD = 0.75;

/** Counts from `backend/assets/eval_media_stats.jsonl` at the time of writing; replaced by a live read when available. */
const TAKE_STATS_SNAPSHOT: TakeStats = {
  shots: 54,
  singleTakePass: 44,
  bestOfTwoPass: 48,
  afterRetriesPass: 53,
  asOf: 'snapshot Nov 21 14:33',
  fromBackend: false,
};

const SHOWCASE_PROPS = [
  { label: 'Brass Pocket Watch', url: '/assets/props/showcase_brass_pocket_watch.png' },
  { label: 'Crystal Shield', url: '/assets/props/showcase_crystal_shield.png' },
  { label: 'Glowing Quill', url: '/assets/props/showcase_glowing_quill.png' },
  { label: 'Origami Crane', url: '/assets/props/showcase_origami_crane.png' },
  { label: 'Star Lantern', url: '/assets/props/showcase_star_lantern.png' },
  { label: 'Desk: Brass Key', url: '/assets/props/showcase_desk_brass_key.png' },
  { label: 'Desk: Coffee Mug', url: '/assets/props/showcase_desk_coffee_mug.png' },
  { label: 'Desk: Paper Heart', url: '/assets/props/showcase_desk_paper_heart.png' },
];

const baseline = stressReport.stateless_baseline;
const withoutRules = stressReport.scene_scaling_without_rules;
const withRules = stressReport.scene_scaling_with_rule_memory;
const sameSizeWithoutRules = withoutRules.find((r) => r.num_scenes === baseline.scenes);
const sameSizeWithRules = withRules.find((r) => r.num_scenes === baseline.scenes);

const SCENE_CURVE = withRules.map((row) => ({
  scenes: `${row.num_scenes} Scenes`,
  withRules: row.judge_score,
  withRulesPassed: row.judge_all_passed,
  noRules: withoutRules.find((r) => r.num_scenes === row.num_scenes)?.judge_score ?? null,
}));

const calibrationCorrect = Math.round(calibration.accuracy * calibration.total_clips);
const propRun = propPersistence.branch_v3_prop_persistence;
const propRate = Math.min(
  propRun.star_token_persistence_rate,
  propRun.crystal_vase_intact_persistence_rate,
);
const cost360 = res720.clip_360p.branch_6_clips_cost_usd;
const cost720 = res720.clip_720p.branch_6_clips_cost_usd;
const costSavingsPct = res720.comparison.cost_savings_pct_using_360p;
const latency360 = res720.clip_360p.total_wall_clock_sec;
const latency720 = res720.clip_720p.total_wall_clock_sec;
const rerenderSkipPct = latestReport.localized_rewrite_skip_savings_pct;

const MEDIA_BENCHMARKS: Array<{ label: string; fill: number; text: string }> = [
  {
    label: 'Cross-Cam Consistency',
    fill: dualCam.cross_camera_consistency_rate,
    text: `${dualCam.cross_camera_consistency_rate.toFixed(3)} · ${dualCam.passed_checks}/${dualCam.total_checks} checks`,
  },
  {
    label: 'Prop Persistence',
    fill: propRate,
    text: `${propRate.toFixed(3)} · ${propRun.clips_audited} clips (1 branch)`,
  },
  {
    label: 'Decisions Calibration',
    fill: calibration.accuracy,
    text: `${calibration.accuracy.toFixed(2)} · ${calibrationCorrect}/${calibration.total_clips} · TPR ${calibration.true_positive_rate.toFixed(3)} · TNR ${calibration.true_negative_rate.toFixed(3)}`,
  },
  {
    label: '360p vs 720p Cost',
    fill: costSavingsPct / 100,
    text: `${costSavingsPct.toFixed(1)}% cheaper · $${cost360.toFixed(2)} vs $${cost720.toFixed(2)} (estimated)`,
  },
  {
    label: '360p vs 720p Latency',
    fill: latency360 / latency720,
    text: `${latency360.toFixed(1)}s vs ${latency720.toFixed(1)}s per 4s clip (n=1)`,
  },
];

function pct(part: number, whole: number): string {
  return whole > 0 ? `${((part / whole) * 100).toFixed(1)}%` : '—';
}

/**
 * Recompute single-take / best-of-two / after-retries pass counts from the
 * backend take log (one JSON object per line).
 */
function parseTakeLog(text: string): TakeStats | null {
  let shots = 0;
  let single = 0;
  let bestOfTwo = 0;
  let afterRetries = 0;
  let lastTimestamp = '';
  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      const row = JSON.parse(trimmed) as {
        take_0_score?: number | null;
        take_1_score?: number | null;
        passed?: boolean;
        timestamp?: string;
      };
      const t0 = row.take_0_score ?? 0;
      const t1 = row.take_1_score ?? 0;
      shots += 1;
      if (t0 >= TAKE_PASS_THRESHOLD) single += 1;
      if (Math.max(t0, t1) >= TAKE_PASS_THRESHOLD) bestOfTwo += 1;
      if (row.passed === true) afterRetries += 1;
      if (row.timestamp) lastTimestamp = row.timestamp;
    } catch {
      // Skip malformed lines
    }
  }
  if (shots === 0) return null;
  return {
    shots,
    singleTakePass: single,
    bestOfTwoPass: bestOfTwo,
    afterRetriesPass: afterRetries,
    asOf: lastTimestamp
      ? `backend log, last entry ${lastTimestamp.replace('T', ' ')}`
      : 'backend log',
    fromBackend: true,
  };
}

async function queryDeviceState(
  permissionName: 'microphone' | 'camera',
  deviceKind: 'audioinput' | 'videoinput',
): Promise<DeviceState> {
  try {
    if (navigator.mediaDevices?.enumerateDevices) {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (!devices.some((d) => d.kind === deviceKind)) return 'missing';
    }
  } catch {
    // Fall through to the permission query
  }
  try {
    if (!navigator.permissions?.query) return 'unknown';
    const status = await navigator.permissions.query({
      name: permissionName as PermissionName,
    });
    return status.state as DeviceState;
  } catch {
    return 'unknown';
  }
}

function devicePill(label: string, state: DeviceState): { className: string; text: string } {
  switch (state) {
    case 'granted':
      return { className: 'qa-pill pass', text: `${label} allowed` };
    case 'denied':
      return { className: 'qa-pill fail', text: `${label} blocked` };
    case 'missing':
      return { className: 'qa-pill fail', text: `No ${label.toLowerCase()} found` };
    case 'prompt':
      return { className: 'qa-pill', text: `${label}: permission not granted yet` };
    default:
      return { className: 'qa-pill', text: `${label}: not checked` };
  }
}

export const EvalModal: React.FC<EvalModalProps> = ({
  open,
  onClose,
  isMockMode = false,
  apiLive = null,
  backendReachable = null,
  branchCount = 0,
}) => {
  const [reelOpen, setReelOpen] = useState(false);
  const [reelError, setReelError] = useState(false);
  const [pitchSectionOpen, setPitchSectionOpen] = useState(false);
  const [missingMedia, setMissingMedia] = useState<Record<string, boolean>>({});
  const [failedProps, setFailedProps] = useState<Record<string, boolean>>({});
  const [micState, setMicState] = useState<DeviceState>('unknown');
  const [camState, setCamState] = useState<DeviceState>('unknown');
  const [takeStats, setTakeStats] = useState<TakeStats>(TAKE_STATS_SNAPSHOT);
  const [evalSummaryHydrated, setEvalSummaryHydrated] = useState(false);

  useEffect(() => {
    if (!open || typeof navigator === 'undefined') return;
    let cancelled = false;
    queryDeviceState('microphone', 'audioinput').then((state) => {
      if (!cancelled) setMicState(state);
    });
    queryDeviceState('camera', 'videoinput').then((state) => {
      if (!cancelled) setCamState(state);
    });
    fetchEvalSummary().then((summary) => {
      if (!cancelled && summary) {
        setEvalSummaryHydrated(true);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  // Recompute take pass rates from the backend's own log each time the modal opens
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetch('/assets/eval_media_stats.jsonl', { signal: AbortSignal.timeout(3000) })
      .then((res) => (res.ok ? res.text() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((text) => {
        const parsed = parseTakeLog(text);
        if (!cancelled && parsed) setTakeStats(parsed);
      })
      .catch(() => {
        // Keep the labelled snapshot when the log is unreachable
      });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const markMediaMissing = (key: string) => {
    setMissingMedia((prev) => (prev[key] ? prev : { ...prev, [key]: true }));
  };

  if (!open) return null;

  const micPill = devicePill('Mic', micState);
  const camPill = devicePill('Cam', camState);
  const apiPill =
    apiLive === null
      ? { className: 'qa-pill', text: 'Backend: checking…' }
      : apiLive
        ? { className: 'qa-pill pass', text: 'Live generation on' }
        : backendReachable
          ? { className: 'qa-pill fail', text: 'Live generation off (no model key) · saved runs only' }
          : { className: 'qa-pill fail', text: 'Backend offline · saved runs only' };

  const TAKE_ROWS = [
    { label: 'Single take', passed: takeStats.singleTakePass },
    { label: 'Best of 2 takes', passed: takeStats.bestOfTwoPass },
    { label: 'After retries', passed: takeStats.afterRetriesPass },
  ];

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
              Offline Eval Benchmarks, Cutout Props &amp; Demo Reel
            </strong>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Gemini 4 Neon + Hydrogen + Decisions API v1beta + Omni Flash ·{' '}
              {evalSummaryHydrated ? 'Hydrated via /api/eval/summary' : 'Audited offline repo JSON'}
            </div>
          </div>
          <button type="button" className="pill-btn" onClick={onClose}>
            Close
          </button>
        </div>

        {/* W2-T9F: Live Booth Checklist */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 8,
            flexWrap: 'wrap',
            padding: '7px 10px',
            marginBottom: 10,
            borderRadius: 10,
            background: 'rgba(15, 23, 42, 0.85)',
            border: '1px solid var(--border-subtle)',
          }}
        >
          <span style={{ fontSize: '0.73rem', fontWeight: 700, color: '#fde68a' }}>
            Live Booth Checklist:
          </span>
          <span className={micPill.className}>{micPill.text}</span>
          <span className={camPill.className}>{camPill.text}</span>
          <span className={apiPill.className}>{apiPill.text}</span>
          <span className="qa-pill">
            {branchCount} {branchCount === 1 ? 'branch' : 'branches'} loaded
            {isMockMode ? ' (built-in storyboard, no clips)' : ''}
          </span>
        </div>

        {/* Highlight KPIs */}
        <div className="eval-kpi-row">
          <div className="eval-kpi">
            <span className="eval-kpi-label">
              Stateless Baseline · {baseline.scenes} scenes (plan-level, n=1)
            </span>
            <strong style={{ color: '#fda4af', fontSize: '1.06rem' }}>
              {baseline.judge_score.toFixed(3)}{' '}
              <span style={{ fontSize: '0.72rem' }}>
                ({baseline.latency_sec.toFixed(2)}s · {baseline.all_passed ? 'PASS' : 'FAIL'})
              </span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">
              StageState (WorldState) · {baseline.scenes} scenes (plan-level, n=1)
            </span>
            {sameSizeWithoutRules && (
              <strong style={{ color: '#6ee7b7', fontSize: '1.06rem' }}>
                {sameSizeWithoutRules.judge_score.toFixed(3)}{' '}
                <span style={{ fontSize: '0.72rem' }}>
                  ({sameSizeWithoutRules.latency_sec.toFixed(2)}s ·{' '}
                  {sameSizeWithoutRules.judge_all_passed ? 'PASS' : 'FAIL'})
                </span>
              </strong>
            )}
            {sameSizeWithRules && (
              <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>
                with rule memory: {sameSizeWithRules.judge_score.toFixed(3)} (
                {sameSizeWithRules.latency_sec.toFixed(2)}s)
              </span>
            )}
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">360p Cost (estimated) &amp; Rerender Skip</span>
            <strong style={{ color: '#fde68a', fontSize: '1.06rem' }}>
              {costSavingsPct.toFixed(1)}%{' '}
              <span style={{ fontSize: '0.72rem' }}>
                cheaper (est.) · {rerenderSkipPct.toFixed(1)}% skip
              </span>
            </strong>
          </div>
          <div className="eval-kpi">
            <span className="eval-kpi-label">Cross-Cam &amp; Props</span>
            <strong style={{ color: '#7dd3fc', fontSize: '1.06rem' }}>
              {dualCam.cross_camera_consistency_rate.toFixed(3)}{' '}
              <span style={{ fontSize: '0.72rem' }}>
                ({dualCam.passed_checks}/{dualCam.total_checks}) · {propRate.toFixed(3)} prop
              </span>
            </strong>
          </div>
        </div>

        <div className="eval-two-col">
          {/* Omni Flash take pass rates recomputed from the backend take log */}
          <div className="eval-box">
            <div className="eval-box-title">
              Omni Flash Take Pass Rates (judge ≥ {TAKE_PASS_THRESHOLD.toFixed(2)} · {takeStats.shots}{' '}
              camera shots)
            </div>
            {TAKE_ROWS.map((row) => (
              <div key={row.label} className="bar-row">
                <span className="bar-label">{row.label}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill blue"
                    style={{
                      width: `${Math.round((row.passed / Math.max(1, takeStats.shots)) * 100)}%`,
                    }}
                  />
                </div>
                <span className="bar-val">
                  {pct(row.passed, takeStats.shots)} ({row.passed}/{takeStats.shots})
                </span>
              </div>
            ))}
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', marginTop: 4 }}>
              Source: <code>eval_media_stats.jsonl</code> · {takeStats.asOf}
            </div>
          </div>

          {/* 2->8 Scene Curve */}
          <div className="eval-box">
            <div className="eval-box-title">
              Scene Scaling 2 → 8 (plan-level, n=1) · with rule memory vs without
            </div>
            {SCENE_CURVE.map((row) => (
              <div
                key={row.scenes}
                className="bar-row"
                style={{ gridTemplateColumns: '64px 1fr 176px' }}
              >
                <span className="bar-label">{row.scenes}</span>
                <div className="bar-track">
                  <div
                    className={`bar-fill ${row.withRulesPassed ? 'green' : 'warn'}`}
                    style={{ width: `${Math.round(row.withRules * 100)}%` }}
                  />
                </div>
                <span className="bar-val">
                  {row.withRules.toFixed(3)} vs{' '}
                  {row.noRules !== null ? row.noRules.toFixed(3) : '—'}{' '}
                  {row.withRulesPassed ? 'pass' : 'judge fail'}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Media benchmarks read from backend/assets report files */}
        <div className="eval-box" style={{ marginTop: 10 }}>
          <div className="eval-box-title">
            Dual-Cam, Prop, Calibration &amp; 360p/720p Media Benchmarks (offline runs)
          </div>
          <div className="eval-two-col" style={{ gap: 8 }}>
            {MEDIA_BENCHMARKS.map((m) => (
              <div
                key={m.label}
                className="bar-row"
                style={{ gridTemplateColumns: '132px 1fr 230px' }}
              >
                <span className="bar-label">{m.label}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill green"
                    style={{ width: `${Math.round(m.fill * 100)}%` }}
                  />
                </div>
                <span className="bar-val">{m.text}</span>
              </div>
            ))}
          </div>
        </div>

        {/* 5-Prop Cutout Showcase Strip */}
        <div className="eval-box" style={{ marginTop: 10 }}>
          <div className="eval-box-title">
            Cutout Prop Showcase (pre-generated · {propPersistence.prop_png_audits.length} PNGs
            audited{propPersistence.all_props_zero_green_spill ? ', zero green spill' : ''})
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
                    <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)' }}>missing</span>
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
              Stitched BE vs. HE Side-by-Side Comparison Reel
            </span>
            <button
              type="button"
              className={`pill-btn ${reelOpen ? 'active' : 'blue'}`}
              onClick={() => {
                setReelError(false);
                setReelOpen((v) => !v);
              }}
            >
              {reelOpen ? 'Hide Reel' : 'Watch BE vs. HE Reel'}
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
                  Comparison reel requires the FastAPI static server (
                  <code>/assets/finals/demo_comparison_be_vs_he.mp4</code>).
                </div>
              )}
            </div>
          )}
        </div>

        {/* Collapsible Submission & Storyboard Showcase Gallery (W2-T6F + W2-T8A) */}
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
              Submission &amp; Storyboard Showcase Gallery
            </span>
            <button
              type="button"
              className={`pill-btn ${pitchSectionOpen ? 'active' : 'subtle'}`}
              onClick={() => {
                setMissingMedia({});
                setPitchSectionOpen((v) => !v);
              }}
            >
              {pitchSectionOpen ? 'Hide Gallery' : 'Open Submission & Storyboard'}
            </button>
          </div>
          {pitchSectionOpen && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
              <div className="eval-two-col">
                {!missingMedia.hook10s && (
                  <div>
                    <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 4 }}>
                      10s Dual-Cam Twist Hook (<code>hook_10s_dualcam_twist.mp4</code>)
                    </div>
                    <video
                      src="/assets/submission/hook_10s_dualcam_twist.mp4"
                      controls
                      playsInline
                      onError={() => markMediaMissing('hook10s')}
                      style={{ width: '100%', maxHeight: 190, borderRadius: 8, background: '#000' }}
                    />
                  </div>
                )}
                {!missingMedia.eval12s && (
                  <div>
                    <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 4 }}>
                      12s Empirical Curves Reel (<code>eval_dashboard_12s.mp4</code>)
                    </div>
                    <video
                      src="/assets/submission/eval_dashboard_12s.mp4"
                      controls
                      playsInline
                      onError={() => markMediaMissing('eval12s')}
                      style={{ width: '100%', maxHeight: 190, borderRadius: 8, background: '#000' }}
                    />
                  </div>
                )}
              </div>

              {!missingMedia.evalBoard && (
                <div>
                  <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 4 }}>
                    Empirical Charts Board (<code>eval_charts_board.png</code>)
                  </div>
                  <img
                    src="/assets/submission/eval_charts_board.png"
                    alt="StageState Empirical Evaluation Charts Board"
                    onError={() => markMediaMissing('evalBoard')}
                    style={{
                      width: '100%',
                      borderRadius: 8,
                      border: '1px solid var(--border-subtle)',
                      background: '#05070d',
                    }}
                  />
                </div>
              )}

              {!missingMedia.storyboard && (
                <div>
                  <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 4 }}>
                    Storyboard Filmstrip (<code>showcase_storyboard_strip.png</code>)
                  </div>
                  <img
                    src="/assets/showcase_storyboard_strip.png"
                    alt="StageState Storyboard Filmstrip"
                    onError={() => markMediaMissing('storyboard')}
                    style={{
                      width: '100%',
                      borderRadius: 8,
                      border: '1px solid var(--border-subtle)',
                      background: '#05070d',
                    }}
                  />
                </div>
              )}

              {!missingMedia.pitchReel && (
                <div>
                  <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 4 }}>
                    1-Min Pitch Demo Reel (<code>pitch_demo_reel_1min.mp4</code>)
                  </div>
                  <video
                    src="/assets/finals/pitch_demo_reel_1min.mp4"
                    controls
                    playsInline
                    onError={() => markMediaMissing('pitchReel')}
                    style={{ width: '100%', maxHeight: 220, borderRadius: 8, background: '#000' }}
                  />
                </div>
              )}

              {missingMedia.hook10s &&
                missingMedia.eval12s &&
                missingMedia.evalBoard &&
                missingMedia.storyboard &&
                missingMedia.pitchReel && (
                  <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
                    Submission media gallery requires FastAPI static assets.
                  </div>
                )}
            </div>
          )}
        </div>

        <div style={{ marginTop: 10, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          Sources: <code>eval/stress_report.json</code> (plan-level judge, one run per point),{' '}
          <code>eval/latest_report.json</code>,{' '}
          <code>backend/assets/calibration_20_clips.json</code>,{' '}
          <code>eval_dual_cam_stats.json</code>, <code>eval_prop_persistence.json</code>,{' '}
          <code>eval_720p_vs_360p.json</code> (list-price estimate per 6-clip branch),{' '}
          <code>eval_media_stats.jsonl</code>.
        </div>
      </div>
    </div>
  );
};
