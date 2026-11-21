/**
 * StageState Root Theater Application.
 *
 * Feature & Use Cases:
 * - Coordinates story branches (`branch_v1` BE vs. forked HE branches), active
 *   scene selection, 2.5D Preview vs. AI Render toggle, Pixel-Art upscaled
 *   preview toggle, and side-by-side branch comparison.
 * - Connects the 3s "Hold to Direct (Cam+Mic)" & text fallback bar to
 *   `POST /api/director/fork` with inline contradiction confirmation.
 * - Connects 2.5D `.webm` / PNG preview export directly to
 *   `POST /api/media/render-scene` for end-to-end Omni Flash rendering.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  fetchBranches,
  requestBranchRender,
  requestSceneRender,
  submitDirectorFork,
} from './api/stageClient';
import { ClapperboardQA } from './components/ClapperboardQA';
import { DirectControlBar } from './components/DirectControlBar';
import { DualCameraStage } from './components/DualCameraStage';
import { StoryBranchTree } from './components/StoryBranchTree';
import { INITIAL_MOCK_BRANCHES } from './data/mockBranches';
import type { PreviewExportResult } from './stage/ThreeStageManager';
import type { BranchState, Scene } from './types/schema';

interface PendingForkInput {
  parentBranchId: string;
  targetSceneId: number;
  clip?: Blob | null;
  text?: string;
  warning: string;
}

export default function App(): React.ReactElement {
  const [branches, setBranches] = useState<BranchState[]>(INITIAL_MOCK_BRANCHES);
  const [isMockMode, setIsMockMode] = useState(true);
  const [activeBranchId, setActiveBranchId] = useState<string>('branch_v1');
  const [activeSceneIndex, setActiveSceneIndex] = useState<number>(0);
  const [viewMode, setViewMode] = useState<'preview' | 'final'>('preview');
  const [compareEnabled, setCompareEnabled] = useState<boolean>(true);
  const [pixelArtMode, setPixelArtMode] = useState<boolean>(false);
  const [cleanCaptureMode, setCleanCaptureMode] = useState<boolean>(false);
  const [targetSceneId, setTargetSceneId] = useState<number>(2);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [renderingScene, setRenderingScene] = useState<boolean>(false);
  const [pendingConflict, setPendingConflict] = useState<PendingForkInput | null>(
    null,
  );
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
    setPendingConflict(null);
    setStatusNote('正在调用 Hydrogen + Neon 推演新分支...');

    const { branch: newBranch, simulated } = await submitDirectorFork({
      parentBranchId: activeBranch.branch_id,
      targetSceneId,
      clip: opts.clip,
      text: opts.text,
      confirmContradiction: opts.confirmContradiction,
    });
    setSubmitting(false);

    if (newBranch.contradiction_warning) {
      setPendingConflict({
        parentBranchId: activeBranch.branch_id,
        targetSceneId,
        clip: opts.clip,
        text: opts.text,
        warning: newBranch.contradiction_warning,
      });
      setStatusNote('检测到逻辑矛盾，请确认是否仍要强制改写。');
      return;
    }

    setBranches((prev) => [...prev, newBranch]);
    setActiveBranchId(newBranch.branch_id);
    setActiveSceneIndex(Math.max(0, targetSceneId - 1));
    setCompareEnabled(true);
    setStatusNote(
      simulated
        ? '后端未启动，已用锁定 Mock 契约即时演示新分支分叉！'
        : `已生成新分支 ${newBranch.branch_label}（2.5D 舞台已即时同步）`,
    );
  };

  const handleForceRewrite = async () => {
    if (!pendingConflict) return;
    await handleSubmitFork({
      clip: pendingConflict.clip,
      text: pendingConflict.text,
      confirmContradiction: true,
    });
  };

  const handleRenderScene = async (
    scene: Scene,
    preview: PreviewExportResult,
  ) => {
    setRenderingScene(true);
    setStatusNote(`正在上传第 ${scene.scene_id} 幕 2.5D 预览并调用 Omni Flash 双拍渲染...`);
    const result = await requestSceneRender({
      branchId: activeBranch.branch_id,
      scene,
      camAPreview: preview.camABlob,
      camBPreview: preview.camBBlob,
      camAStartPng: preview.camAStartPng,
      camAEndPng: preview.camAEndPng,
      camBStartPng: preview.camBStartPng,
      camBEndPng: preview.camBEndPng,
    });
    setRenderingScene(false);

    if (!result) {
      setStatusNote('后端渲染接口暂不可用，已保持 2.5D 实时舞台预览。');
      return;
    }

    setBranches((prev) =>
      prev.map((b) =>
        b.branch_id !== activeBranch.branch_id
          ? b
          : {
              ...b,
              scenes: b.scenes.map((s) =>
                s.scene_id === result.scene_id
                  ? {
                      ...s,
                      media: result.media,
                      environment_notes:
                        result.environment_notes ?? s.environment_notes,
                    }
                  : s,
              ),
            },
      ),
    );
    setViewMode('final');
    setStatusNote(
      result.media.fallback_to_preview
        ? `第 ${scene.scene_id} 幕质检未通过已降级为 2.5D 预览`
        : `✅ 第 ${scene.scene_id} 幕 Omni Flash 成片渲染完成！`,
    );
  };

  const handleRenderWholeBranch = async () => {
    if (renderingScene) return;
    setRenderingScene(true);
    setStatusNote(`正在并发渲染 ${activeBranch.branch_label} 受影响幕...`);
    const updated = await requestBranchRender(activeBranch.branch_id);
    setRenderingScene(false);
    if (!updated) {
      setStatusNote('批量渲染接口暂不可用，请检查 FastAPI 服务。');
      return;
    }
    setBranches((prev) =>
      prev.map((b) => (b.branch_id === updated.branch_id ? updated : b)),
    );
    setViewMode('final');
    setStatusNote(`✅ ${updated.branch_label} 全部受影响幕渲染完成！`);
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
          {!cleanCaptureMode && (
            <span className="cam-tag" style={{ fontSize: '0.72rem' }}>
              {isMockMode ? 'Mock 契约模式' : 'Live FastAPI 已连接'}
            </span>
          )}
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
            className={`pill-btn ${pixelArtMode ? 'active' : ''}`}
            onClick={() => setPixelArtMode((v) => !v)}
          >
            👾 像素风预览
          </button>
          <button
            type="button"
            className={`pill-btn ${compareEnabled ? 'active' : ''}`}
            onClick={() => setCompareEnabled((v) => !v)}
          >
            ⚖️ 新旧对比
          </button>
          {!isMockMode && (
            <button
              type="button"
              className="pill-btn"
              onClick={handleRenderWholeBranch}
              disabled={renderingScene}
            >
              {renderingScene ? '⏳ 并发渲染中...' : '🚀 并发渲染全分支'}
            </button>
          )}
          <button
            type="button"
            className={`pill-btn ${cleanCaptureMode ? 'active' : ''}`}
            onClick={() => setCleanCaptureMode((v) => !v)}
          >
            🎥 纯净录屏
          </button>
        </div>
      </header>

      {!cleanCaptureMode && statusNote && (
        <div
          style={{
            fontSize: '0.78rem',
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
        pixelArtMode={pixelArtMode}
        renderingScene={renderingScene}
        onExportComplete={handleExportComplete}
        onRenderScene={handleRenderScene}
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
        contradictionWarning={
          pendingConflict?.warning ?? activeBranch.contradiction_warning
        }
        cleanCaptureMode={cleanCaptureMode}
        onDismissContradiction={() => setPendingConflict(null)}
        onForceRewrite={handleForceRewrite}
        onSubmitFork={handleSubmitFork}
      />
    </div>
  );
}
