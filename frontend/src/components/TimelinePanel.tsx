/**
 * Timelines panel: version control for the story.
 *
 * Compare two timelines scene by scene (who stands where, who holds what, where every object is), and graft one
 * timeline's later scenes onto another. Both run on the world state on the backend; no model call is involved.
 */
import React, { useEffect, useMemo, useState } from 'react';
import type { BranchState } from '../types/schema';

interface DiffEntry {
  kind: string;
  subject: string;
  text: string;
}

interface DiffRow {
  scene_id: number;
  a_title: string | null;
  b_title: string | null;
  same_take: boolean;
  same_story?: boolean;
  differences: DiffEntry[];
  only_in: 'A' | 'B' | null;
}

interface DiffReport {
  diverged_at: number | null;
  scenes: DiffRow[];
}

interface GraftReport {
  mode: 'direct' | 'bridge';
  bridge_narration: string | null;
  kept_final_clips: boolean;
  scenes_attached: number;
  new_branch_id: string;
}

interface TimelinePanelProps {
  open: boolean;
  branches: BranchState[];
  activeBranchId: string;
  onClose: () => void;
  /** Called with the new timeline after a graft, so the app can load and open it. */
  onGrafted: (branch: BranchState) => void;
}

const label = (b: BranchState) => `${b.branch_label || b.branch_id} (${b.scenes.length} scenes)`;

export const TimelinePanel: React.FC<TimelinePanelProps> = ({ open, branches, activeBranchId, onClose, onGrafted }) => {
  const [aId, setAId] = useState(activeBranchId);
  const [bId, setBId] = useState('');
  const [report, setReport] = useState<DiffReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keepUntil, setKeepUntil] = useState(1);
  const [continueFrom, setContinueFrom] = useState(2);
  const [grafting, setGrafting] = useState(false);
  const [graftResult, setGraftResult] = useState<{ report: GraftReport; branch: BranchState } | null>(null);

  const branchA = useMemo(() => branches.find((b) => b.branch_id === aId), [branches, aId]);
  const branchB = useMemo(() => branches.find((b) => b.branch_id === bId), [branches, bId]);

  useEffect(() => {
    if (!open) return;
    setAId(activeBranchId);
    setGraftResult(null);
    setBId((curr) => {
      if (curr && curr !== activeBranchId && branches.some((b) => b.branch_id === curr)) return curr;
      return branches.find((b) => b.branch_id !== activeBranchId)?.branch_id ?? '';
    });
  }, [open, activeBranchId, branches]);

  useEffect(() => {
    if (!open || !aId || !bId || aId === bId) {
      setReport(null);
      return;
    }
    let cancelled = false;
    setError(null);
    fetch(`/api/timeline/diff?a=${encodeURIComponent(aId)}&b=${encodeURIComponent(bId)}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: DiffReport) => {
        if (cancelled) return;
        setReport(data);
        const fork = data.diverged_at ?? 2;
        setKeepUntil(Math.max(1, fork - 1));
        setContinueFrom(fork);
      })
      .catch(() => {
        if (!cancelled) setError('The timelines could not be compared (is the backend running?).');
      });
    return () => {
      cancelled = true;
    };
  }, [open, aId, bId]);

  if (!open) return null;

  const runGraft = async () => {
    if (!branchA || !branchB) return;
    setGrafting(true);
    setError(null);
    setGraftResult(null);
    const form = new FormData();
    form.append('base_branch_id', branchA.branch_id);
    form.append('base_scene_id', String(keepUntil));
    form.append('donor_branch_id', branchB.branch_id);
    form.append('donor_from_scene_id', String(continueFrom));
    try {
      const res = await fetch('/api/timeline/graft', { method: 'POST', body: form });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setGraftResult((await res.json()) as { report: GraftReport; branch: BranchState });
    } catch {
      setError('The graft could not be made.');
    } finally {
      setGrafting(false);
    }
  };

  return (
    <div className="models-overlay" role="dialog" aria-modal="true" aria-label="Timelines" onClick={onClose}>
      <div className="models-panel" onClick={(e) => e.stopPropagation()}>
        <div className="models-head">
          <h2>Timelines</h2>
          <button type="button" className="models-close" aria-label="Close" onClick={onClose}>
            ×
          </button>
        </div>
        <p className="models-foot">
          Every scene stores the whole world. So two timelines can be compared like two versions of a file, and one
          timeline can continue with the later scenes of another.
        </p>

        <h3>Compare</h3>
        <div className="timeline-pick">
          <label>
            <span>A</span>
            <select value={aId} onChange={(e) => setAId(e.target.value)}>
              {branches.map((b) => (
                <option key={b.branch_id} value={b.branch_id}>
                  {label(b)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>B</span>
            <select value={bId} onChange={(e) => setBId(e.target.value)}>
              {branches
                .filter((b) => b.branch_id !== aId)
                .map((b) => (
                  <option key={b.branch_id} value={b.branch_id}>
                    {label(b)}
                  </option>
                ))}
            </select>
          </label>
        </div>

        {error && <p className="models-empty">{error}</p>}
        {report && (
          <>
            <p className="models-foot">
              {report.diverged_at
                ? `The two timelines are the same until scene ${report.diverged_at - 1} and differ from scene ${report.diverged_at}.`
                : 'The two timelines hold the same world in every scene.'}
            </p>
            <table className="models-table timeline-table">
              <thead>
                <tr>
                  <th>Scene</th>
                  <th>A</th>
                  <th>B</th>
                  <th>What is different in the world</th>
                </tr>
              </thead>
              <tbody>
                {report.scenes.map((row) => (
                  <tr key={row.scene_id} className={row.same_take ? 'timeline-same' : ''}>
                    <td>{row.scene_id}</td>
                    <td>{row.a_title ?? '—'}</td>
                    <td>{row.b_title ?? '—'}</td>
                    <td>
                      {row.only_in ? (
                        `Only in ${row.only_in}`
                      ) : row.same_take ? (
                        'Same take'
                      ) : row.differences.length === 0 ? (
                        'Same world, told differently'
                      ) : (
                        <ul className="timeline-diffs">
                          {row.differences.map((d, i) => (
                            <li key={`${d.kind}-${d.subject}-${i}`}>{d.text}</li>
                          ))}
                        </ul>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {branchA && branchB && (
          <>
            <h3>Graft</h3>
            <div className="timeline-graft">
              <span>Keep A up to scene</span>
              <select value={keepUntil} onChange={(e) => setKeepUntil(Number(e.target.value))}>
                {branchA.scenes.map((s) => (
                  <option key={s.scene_id} value={s.scene_id}>
                    {s.scene_id}
                  </option>
                ))}
              </select>
              <span>then continue with B from scene</span>
              <select value={continueFrom} onChange={(e) => setContinueFrom(Number(e.target.value))}>
                {branchB.scenes
                  .filter((s) => s.scene_id >= 2)
                  .map((s) => (
                    <option key={s.scene_id} value={s.scene_id}>
                      {s.scene_id}
                    </option>
                  ))}
              </select>
              <button type="button" className="timeline-button" disabled={grafting} onClick={() => void runGraft()}>
                {grafting ? 'Grafting…' : 'Graft'}
              </button>
            </div>
            {graftResult && (
              <div className="timeline-result">
                <p>
                  {graftResult.report.mode === 'direct'
                    ? `The two worlds match at the join, so ${graftResult.report.scenes_attached} scene(s) were attached directly.`
                    : `The two worlds differ at the join, so a bridging scene was added before the ${graftResult.report.scenes_attached - 1} attached scene(s).`}
                </p>
                {graftResult.report.bridge_narration && <p className="timeline-bridge">Bridge: {graftResult.report.bridge_narration}</p>}
                <p>
                  {graftResult.report.kept_final_clips
                    ? 'The attached scenes keep their clips.'
                    : 'The attached scenes start from a different moment, so they show the 3D preview until they are rendered again.'}
                </p>
                <button type="button" className="timeline-button" onClick={() => onGrafted(graftResult.branch)}>
                  Open the new timeline
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
};
