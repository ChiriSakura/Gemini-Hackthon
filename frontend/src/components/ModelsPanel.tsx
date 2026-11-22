/**
 * Models & Evaluation panel.
 *
 * Shows which Gemini model does which job in the pipeline (names read live from the backend),
 * the timings measured on this build, and the evaluation summary served by `/api/eval/summary`.
 */
import React, { useEffect, useState } from 'react';

interface HealthModels {
  director?: string;
  judge?: string;
  decision?: string;
  video?: string;
  image?: string;
}

interface StressRow {
  num_scenes: number;
  judge_score: number;
  latency_sec: number;
}

interface SceneScore {
  scene_id: number;
  title: string;
  score: number | null;
  passed: number;
  total: number;
}

interface VideoConsistencyCategory {
  passed: number;
  total: number;
  pass_rate: number;
  avg_confidence?: number;
}

interface VideoConsistencyReport {
  timestamp?: string;
  method?: string;
  branch_id?: string;
  scenes_evaluated?: number;
  overall?: { passed: number; total: number; pass_rate: number };
  category_summary?: Record<string, VideoConsistencyCategory>;
  human_audit_sample_count?: number;
}

interface EvalSummary {
  calibration_20_clips?: { total_clips?: number; accuracy?: number; avg_latency_sec?: number };
  cache_savings?: { naive_total_clips?: number; clips_skipped?: number };
  video_consistency?: VideoConsistencyReport | null;
  stress_report?: {
    timestamp?: string;
    stateless_baseline?: { judge_score?: number; scenes?: number };
    scene_scaling_with_rule_memory?: StressRow[];
    scene_scaling_without_rules?: StressRow[];
  };
}

const VIDEO_CONSISTENCY_LABELS: Array<{ key: string; label: string }> = [
  { key: 'count', label: 'Entity count (1 girl, 1 cat, 1 vase)' },
  { key: 'holding', label: 'Holding state (umbrella / props)' },
  { key: 'position', label: 'Stage position (left/right, floor/table)' },
  { key: 'permanence', label: 'Object permanence across scenes' },
  { key: 'clean', label: 'Clean frame (no cameras / text / numbers)' },
  { key: 'cross_camera', label: 'Cross-camera sync (start / mid / end)' },
  { key: 'inter_scene', label: 'Scene-to-scene continuity' },
];

const STAGES: Array<{ step: string; key: keyof HealthModels; fallback: string; job: string }> = [
  { step: 'Hear & see', key: 'judge', fallback: 'Gemini 4 Hydrogen', job: 'Turns voice, camera and text into one direction; spots a real object held up to the camera.' },
  { step: 'Direct', key: 'director', fallback: 'Gemini 4 Neon', job: 'Updates the world state: who stands where, what they hold, what happens next.' },
  { step: 'Make props', key: 'image', fallback: 'Gemini image model', job: 'Turns the object in your hand into a stage prop; draws the 45° opening view.' },
  { step: 'Film', key: 'video', fallback: 'Omni Flash', job: 'Films both cameras in one generation, so the same action happens at the same instant in A and B.' },
  { step: 'Judge', key: 'decision', fallback: 'Decisions API', job: 'Answers yes/no checks on every clip and scores it; a failed check triggers a repair note.' },
];

const MEASURED: Array<{ label: string; value: string; note: string }> = [
  { label: 'One scene, both cameras', value: '26–38 s', note: 'single split-screen generation, 5 runs today' },
  { label: 'Direction to plan', value: '16–18 s', note: 'voice or text to a new world state' },
  { label: 'One judge question', value: '0.4–0.6 s', note: 'Decisions API, measured today' },
  { label: 'Cameras per generation', value: '2', note: 'A front, B fixed 45°, cut from one clip' },
];

export const ModelsPanel: React.FC<{ open: boolean; onClose: () => void }> = ({ open, onClose }) => {
  const [models, setModels] = useState<HealthModels>({});
  const [summary, setSummary] = useState<EvalSummary | null>(null);
  const [sceneScores, setSceneScores] = useState<SceneScore[]>([]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    fetch('/api/health')
      .then((r) => r.json())
      .then((d) => !cancelled && setModels(d?.models ?? {}))
      .catch(() => undefined);
    fetch('/api/eval/summary')
      .then((r) => r.json())
      .then((d) => !cancelled && setSummary(d))
      .catch(() => undefined);
    fetch('/api/branches')
      .then((r) => r.json())
      .then((d) => {
        const list = Array.isArray(d) ? d : (d?.branches ?? []);
        const base = list.find((b: { branch_id: string }) => b.branch_id === 'branch_v1') ?? list[0];
        if (cancelled || !base) return;
        setSceneScores(
          (base.scenes ?? []).map((sc: any) => {
            const items = sc.media?.qa_result?.items ?? [];
            return {
              scene_id: sc.scene_id,
              title: sc.title,
              score: sc.media?.qa_result?.overall_score ?? null,
              passed: items.filter((i: any) => i.passed).length,
              total: items.length,
            };
          }),
        );
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) return null;
  const stress = summary?.stress_report;
  const scaling = stress?.scene_scaling_with_rule_memory ?? stress?.scene_scaling_without_rules ?? [];
  const baseline = stress?.stateless_baseline?.judge_score;
  const videoConsistency = summary?.video_consistency ?? null;
  const videoCategoryRows = videoConsistency?.category_summary
    ? VIDEO_CONSISTENCY_LABELS.filter((item) => Boolean(videoConsistency.category_summary?.[item.key])).map(
        (item) => ({
          ...item,
          stats: videoConsistency.category_summary![item.key],
        }),
      )
    : [];

  return (
    <div className="models-overlay" role="dialog" aria-modal="true" aria-label="Models and evaluation">
      <div className="models-panel">
        <header className="models-head">
          <h2>Models &amp; Evaluation</h2>
          <button type="button" className="models-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <section>
          <h3>Architecture</h3>
          <div className="arch">
            <div className="arch-node in">Voice · Camera · Text</div>
            <span className="arch-arrow">→</span>
            <div className="arch-node">Hear &amp; See<small>Gemini 4 Hydrogen</small></div>
            <span className="arch-arrow">→</span>
            <div className="arch-node">Director<small>Gemini 4 Neon</small></div>
            <span className="arch-arrow">→</span>
            <div className="arch-node state">World state<small>story tree · 3D stage</small></div>
            <span className="arch-arrow">→</span>
            <div className="arch-node">Film A + B together<small>Omni Flash</small></div>
            <span className="arch-arrow">→</span>
            <div className="arch-node">Judge each clip<small>Decisions API</small></div>
            <span className="arch-arrow">→</span>
            <div className="arch-node out">Saved scene</div>
          </div>
          <p className="models-foot">
            Side paths: the image model turns a real object into a prop and draws the 45° opening view. A failed check
            sends a repair note back to the director. Every finished scene is saved, so any earlier scene can be rewritten.
          </p>
        </section>

        <section>
          <h3>Who does what</h3>
          <ol className="models-flow">
            {STAGES.map((s) => (
              <li key={s.step}>
                <span className="models-step">{s.step}</span>
                <span className="models-name">{(models[s.key] ?? s.fallback).replace('models/', '')}</span>
                <span className="models-job">{s.job}</span>
              </li>
            ))}
          </ol>
        </section>

        <section>
          <h3>Measured on this build</h3>
          <div className="models-tiles">
            {MEASURED.map((m) => (
              <div className="models-tile" key={m.label}>
                <span className="models-tile-value">{m.value}</span>
                <span className="models-tile-label">{m.label}</span>
                <span className="models-tile-note">{m.note}</span>
              </div>
            ))}
          </div>
        </section>

        <section>
          <h3>Every generation yields two clips, and each is scored</h3>
          {sceneScores.length > 0 ? (
            <table className="models-table">
              <thead>
                <tr>
                  <th>Scene</th>
                  <th>Clips</th>
                  <th>Yes/no checks passed</th>
                  <th>Judge score</th>
                </tr>
              </thead>
              <tbody>
                {sceneScores.map((row) => (
                  <tr key={row.scene_id}>
                    <td>
                      {row.scene_id}. {row.title}
                    </td>
                    <td>Camera A + Camera B</td>
                    <td>{row.total > 0 ? `${row.passed} / ${row.total}` : '—'}</td>
                    <td>{row.score != null ? row.score.toFixed(2) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="models-empty">No scored scenes yet.</p>
          )}
          {summary?.calibration_20_clips?.accuracy != null && (
            <p className="models-foot">
              Judge calibration: the Decisions API agreed with human labels on{' '}
              {Math.round((summary.calibration_20_clips.accuracy ?? 0) * 100)}% of{' '}
              {summary.calibration_20_clips.total_clips} labelled clips.
              {summary.cache_savings?.clips_skipped != null &&
                ` Rewriting one scene re-renders only what follows it: ${summary.cache_savings.clips_skipped} of ${summary.cache_savings.naive_total_clips} clips were reused across saved branches.`}
            </p>
          )}
        </section>

        <section>
          <h3>Clip-level consistency (6-scene benchmark)</h3>
          {videoCategoryRows.length > 0 ? (
            <>
              <table className="models-table">
                <thead>
                  <tr>
                    <th>Check category</th>
                    <th>Passed</th>
                    <th>Pass rate</th>
                  </tr>
                </thead>
                <tbody>
                  {videoCategoryRows.map((row) => (
                    <tr key={row.key}>
                      <td>{row.label}</td>
                      <td>
                        {row.stats.passed} / {row.stats.total}
                      </td>
                      <td>{Math.round(row.stats.pass_rate * 100)}%</td>
                    </tr>
                  ))}
                  {videoConsistency?.overall && (
                    <tr>
                      <td>
                        <strong>Overall ({videoConsistency.scenes_evaluated ?? 6} scenes)</strong>
                      </td>
                      <td>
                        <strong>
                          {videoConsistency.overall.passed} / {videoConsistency.overall.total}
                        </strong>
                      </td>
                      <td>
                        <strong>{Math.round(videoConsistency.overall.pass_rate * 100)}%</strong>
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p className="models-foot">
                Evaluated on rendered clips from both cameras ({videoConsistency?.method ?? 'baseline'}
                {videoConsistency?.branch_id ? ` · ${videoConsistency.branch_id}` : ''}).
                {videoConsistency?.human_audit_sample_count
                  ? ` Saved ${videoConsistency.human_audit_sample_count} sample frames for human calibration.`
                  : ''}
              </p>
            </>
          ) : (
            <p className="models-empty">Clip-level consistency benchmark has not been run yet.</p>
          )}
        </section>

        <section>
          <h3>Story consistency as the story grows</h3>
          {scaling.length > 0 ? (
            <table className="models-table">
              <thead>
                <tr>
                  <th>Scenes</th>
                  <th>Judge score, with world state</th>
                  <th>Planning time</th>
                </tr>
              </thead>
              <tbody>
                {scaling.map((row) => (
                  <tr key={row.num_scenes}>
                    <td>{row.num_scenes}</td>
                    <td>{row.judge_score.toFixed(2)}</td>
                    <td>{row.latency_sec.toFixed(1)} s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="models-empty">Evaluation summary is not available from the backend right now.</p>
          )}
          {typeof baseline === 'number' && (
            <p className="models-foot">
              Same story without world state (text chained scene to scene): judge score {baseline.toFixed(2)}.
              {stress?.timestamp ? ` Run ${stress.timestamp}.` : ''} Scores are plan-level, one run each.
            </p>
          )}
        </section>
      </div>
    </div>
  );
};
