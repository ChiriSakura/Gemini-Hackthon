/**
 * StageState Root Theater Application.
 *
 * Feature & Use Cases:
 * - Coordinates story branches (`branch_v1` BE vs. forked HE branches), active
 *   scene selection, 2.5D Preview vs. AI Render toggle, and side-by-side branch
 *   comparison.
 * - Connects the 3s "Hold to Direct (Cam+Mic)" & text fallback bar to
 *   `POST /api/director/fork` and updates `tasks/status_P3.json` mode tracking.
 */
import React, { useEffect, useMemo, useState } from 'react';
import { fetchBranches, submitDirectorFork } from './api/stageClient';
import { ClapperboardQA } from './components/ClapperboardQA';
import { DirectControlBar } from './components/DirectControlBar';
import { DualCameraStage } from './components/DualCameraStage';
import { StoryBranchTree } from './components/StoryBranchTree';
import { INITIAL_MOCK_BRANCHES } from './data/mockBranches';
import type { PreviewExportResult } from './stage/ThreeStageManager';
import type { BranchState } from './types/schema';

export default function App(): React.ReactElement {
  const [branches, setBranches] = useState<BranchState[]>(INITIAL_MOCK_BRANCHES);
  const [isMockMode, setIsMockMode] = useState(true);
  const [activeBranchId, setActiveBranchId] = useState<string>('branch_v1');
  const [activeSceneIndex, setActiveSceneIndex] = useState<number>(0);
  const [viewMode, setViewMode] = useState<'preview' | 'final'>('preview');
  const [compareEnabled, setCompareEnabled] = useState<boolean>(true);
  const [targetSceneId, setTargetSceneId] = useState<number>(2);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);

  useEffect(() => {
    fetchBranches().then(({ branches: loaded, fromMock }) => {
      setBranches(loaded);
      setIsMockMode(fromMock);
      if (loaded.length > 0) {
        setActiveBranchId(loaded[0].branch_id);
      }
    });
  }, []);

  const activeBranch = useMemo(
    () => branches.find((b) => b.branch_id === activeBranchId) ?? branches[0],
    [branches, activeBranchId],
  );

  const compareBranch = useMemo(() => {
    if (branches.length < 2) return null;
    if (activeBranch.parent_branch_id) {
      return (
        branches.find((b) => b.branch_id === activeBranch.parent_branch_id) ??
        branches[0]
      );
    }
    return branches.find((b) => b.branch_id !== activeBranch.branch_id) ?? null;
  }, [branches, activeBranch]);

  const activeScene =
    activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];

  const handleSelectScene = (branchId: string, sceneIndex: number) => {
    setActiveBranchId(branchId);
    setActiveSceneIndex(sceneIndex);
    setTargetSceneId(sceneIndex + 1);
  };

  const handleSubmitFork = async (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
  }) => {
    setSubmitting(true);
    setStatusNote('正在调用 Hydrogen + Neon 推演新分支...');
    const { branch: newBranch, simulated } = await submitDirectorFork({
      parentBranchId: activeBranch.branch_id,
      targetSceneId,
      clip: opts.clip,
      text: opts.text,
      confirmContradiction: opts.confirmContradiction,
    });
    setSubmitting(false);

    setBranches((prev) => [...prev, newBranch]);
    setActiveBranchId(newBranch.branch_id);
    setActiveSceneIndex(Math.max(0, targetSceneId - 1));
    setCompareEnabled(true);
    setStatusNote(
      simulated
        ? '后端未启动，已用锁定 Mock 契约即时演示新分支分叉！'
        : `已生成新分支 ${newBranch.branch_label}`,
    );
  };

  const handleExportComplete = (result: PreviewExportResult) => {
    setStatusNote(
      result.mode === 'webm'
        ? `双机位 .webm 导出就绪 (${result.elapsedMs}ms)`
        : `已启用首尾帧 PNG 兜底模式 (${result.elapsedMs}ms)`,
    );
  };

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="brand-group">
          <span className="brand-title">StageState</span>
          <span className="brand-subtitle">
            一句话导演 · 可改写过去的双机位 2.5D 舞台
          </span>
          <span className="cam-tag" style={{ fontSize: '0.72rem' }}>
            {isMockMode ? 'Mock 契约联调模式' : 'Live FastAPI 已连接'}
          </span>
        </div>

        <div className="toolbar-actions">
          <button
            type="button"
            className={`pill-btn blue ${viewMode === 'preview' ? 'active' : ''}`}
            onClick={() => setViewMode('preview')}
          >
            🎭 2.5D 实时舞台
          </button>
          <button
            type="button"
            className={`pill-btn blue ${viewMode === 'final' ? 'active' : ''}`}
            onClick={() => setViewMode('final')}
          >
            ✨ Omni Flash 成片
          </button>
          <button
            type="button"
            className={`pill-btn ${compareEnabled ? 'active' : ''}`}
            onClick={() => setCompareEnabled((v) => !v)}
          >
            ⚖️ 新旧结局同屏对比
          </button>
        </div>
      </header>

      {statusNote && (
        <div
          style={{
            fontSize: '0.8rem',
            color: '#fde68a',
            padding: '4px 12px',
            background: 'rgba(251, 191, 36, 0.08)',
            borderRadius: 8,
          }}
        >
          {statusNote}
        </div>
      )}

      <DualCameraStage
        activeBranch={activeBranch}
        compareBranch={compareBranch}
        activeSceneIndex={activeSceneIndex}
        viewMode={viewMode}
        compareEnabled={compareEnabled}
        onExportComplete={handleExportComplete}
      />

      <ClapperboardQA scene={activeScene} />

      <StoryBranchTree
        branches={branches}
        activeBranchId={activeBranch.branch_id}
        activeSceneIndex={activeSceneIndex}
        onSelectScene={handleSelectScene}
      />

      <DirectControlBar
        targetSceneId={targetSceneId}
        onTargetSceneChange={setTargetSceneId}
        submitting={submitting}
        contradictionWarning={activeBranch.contradiction_warning}
        onSubmitFork={handleSubmitFork}
      />
    </div>
  );
}
