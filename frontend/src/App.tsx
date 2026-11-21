/**
 * StageState Root Theater Application (English-First UI).
 *
 * Feature & Use Cases:
 * - Coordinates story branches (`branch_v1` BE vs. forked HE branches), active
 *   scene selection, `▶ Play 1→4` auto-sequence playback, 2.5D Preview vs.
 *   Omni Flash AI Render toggle, Pixel-Art preview, and side-by-side comparison.
 * - Connects 3s Cam+Mic / text director input to `POST /api/director/fork` with
 *   inline contradiction confirmation and physical prop creation to `/api/media/create-prop`.
 * - Hosts a lightweight collapsible `📊 Eval` modal showing real empirical curves.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  createStageProp,
  fetchBranches,
  fetchDirectorRules,
  fetchRenderProgress,
  requestBranchRender,
  requestSceneRender,
  submitDirectorFork,
  type RenderProgressSnapshot,
} from './api/stageClient';
import { ClapperboardQA } from './components/ClapperboardQA';
import {
  DirectControlBar,
  type PropPlacement,
} from './components/DirectControlBar';
import { DualCameraStage } from './components/DualCameraStage';
import { EvalModal } from './components/EvalModal';
import { StoryBranchTree } from './components/StoryBranchTree';
import { INITIAL_MOCK_BRANCHES } from './data/mockBranches';
import type { PreviewExportResult } from './stage/ThreeStageManager';
import type { BranchState, Scene } from './types/schema';
import { getBranchLabelEn } from './utils/displayLocale';
import { playCompletionChime } from './utils/soundCue';

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
  const [reconnectingApi, setReconnectingApi] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [activeBranchId, setActiveBranchId] = useState<string>('branch_v1');
  const [compareBranchId, setCompareBranchId] = useState<string | null>(null);
  const [activeSceneIndex, setActiveSceneIndex] = useState<number>(0);
  const [viewMode, setViewMode] = useState<'preview' | 'final'>('preview');
  const [compareEnabled, setCompareEnabled] = useState<boolean>(true);
  const [pixelArtMode, setPixelArtMode] = useState<boolean>(false);
  const [cleanCaptureMode, setCleanCaptureMode] = useState<boolean>(false);
  const [evalModalOpen, setEvalModalOpen] = useState<boolean>(false);
  const [shortcutsOpen, setShortcutsOpen] = useState<boolean>(false);
  const [isPlayingStory, setIsPlayingStory] = useState<boolean>(false);
  const [targetSceneId, setTargetSceneId] = useState<number>(2);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [renderingScene, setRenderingScene] = useState<boolean>(false);
  const [renderProgress, setRenderProgress] = useState<RenderProgressSnapshot | null>(null);
  const [creatingProp, setCreatingProp] = useState<boolean>(false);
  const [pipelineStage, setPipelineStage] = useState<number>(0); // 0=idle, 1..4=active step
  const [rules, setRules] = useState<string[]>([]);
  const [pendingConflict, setPendingConflict] = useState<PendingForkInput | null>(
    null,
  );
  const [statusNote, setStatusNote] = useState<string | null>(null);

  const loadBackendState = async (showFeedback = false) => {
    setReconnectingApi(true);
    const { branches: loaded, fromMock } = await fetchBranches();
    setBranches(loaded);
    setIsMockMode(fromMock);
    if (loaded.length > 0) {
      setActiveBranchId((curr) =>
        loaded.some((b) => b.branch_id === curr) ? curr : loaded[0].branch_id,
      );
      if (!fromMock && loaded[0].scenes[0]?.media.cam_a_final_url) {
        setViewMode('final');
      }
    }
    const fetchedRules = await fetchDirectorRules();
    setRules(fetchedRules);
    setReconnectingApi(false);
    if (showFeedback) {
      setStatusNote(
        fromMock
          ? 'Backend still offline — staying in Mock Fallback Mode.'
          : `Connected to Live FastAPI (${loaded.length} branches loaded)!`,
      );
    }
  };

  useEffect(() => {
    loadBackendState(false);
  }, []);

  const activeBranch = useMemo(
    () => branches.find((b) => b.branch_id === activeBranchId) ?? branches[0],
    [branches, activeBranchId],
  );

  const compareBranch = useMemo(() => {
    if (branches.length < 2) return null;
    if (compareBranchId && compareBranchId !== activeBranch.branch_id) {
      const explicit = branches.find((b) => b.branch_id === compareBranchId);
      if (explicit) return explicit;
    }
    if (activeBranch.parent_branch_id) {
      return (
        branches.find((b) => b.branch_id === activeBranch.parent_branch_id) ??
        branches[0]
      );
    }
    return branches.find((b) => b.branch_id !== activeBranch.branch_id) ?? null;
  }, [branches, activeBranch, compareBranchId]);

  // W2-T5A: Poll GET /api/media/progress/{branch_id} every 1.2s while rendering
  useEffect(() => {
    if (!renderingScene || isMockMode) {
      setRenderProgress(null);
      return;
    }
    let cancelled = false;
    const poll = async () => {
      const snap = await fetchRenderProgress(activeBranch.branch_id);
      if (!cancelled && snap) {
        setRenderProgress(snap);
      }
    };
    poll();
    const timer = window.setInterval(poll, 1200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [renderingScene, isMockMode, activeBranch.branch_id]);

  const activeScene =
    activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];

  // Auto-advance scenes 1 -> 4 every 4 seconds when Play 1->4 is active
  useEffect(() => {
    if (!isPlayingStory) return;
    const totalScenes = activeBranch.scenes.length;
    if (totalScenes <= 1) {
      setIsPlayingStory(false);
      return;
    }
    const timer = window.setInterval(() => {
      setActiveSceneIndex((prev) => {
        if (prev + 1 >= totalScenes) {
          setIsPlayingStory(false);
          return prev;
        }
        const next = prev + 1;
        setTargetSceneId(next + 1);
        return next;
      });
    }, 4000);
    return () => window.clearInterval(timer);
  }, [isPlayingStory, activeBranch.scenes.length]);

  const handleTogglePlayStory = () => {
    setIsPlayingStory((playing) => {
      if (!playing) {
        setActiveSceneIndex(0);
        setTargetSceneId(1);
        return true;
      }
      return false;
    });
  };

  // W2-T4A: Safe global keyboard shortcuts for smooth demo & 16:00 video capture
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) {
        return;
      }
      if (e.code === 'Space') {
        e.preventDefault();
        handleTogglePlayStory();
      } else if (e.key >= '1' && e.key <= '4') {
        const idx = Number(e.key) - 1;
        if (idx < activeBranch.scenes.length) {
          setIsPlayingStory(false);
          setActiveSceneIndex(idx);
          setTargetSceneId(idx + 1);
        }
      } else if (e.key === 'Tab') {
        e.preventDefault();
        setViewMode((m) => (m === 'preview' ? 'final' : 'preview'));
      } else if (e.key.toLowerCase() === 'c' && !e.metaKey && !e.ctrlKey) {
        setCleanCaptureMode((v) => !v);
      } else if (e.key.toLowerCase() === 'e' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        setEvalModalOpen((v) => !v);
      } else if (e.key.toLowerCase() === 'r' && !e.metaKey && !e.ctrlKey && !e.altKey) {
        handleResetDemo();
      } else if (e.key === '?' && !e.metaKey && !e.ctrlKey) {
        setShortcutsOpen((v) => !v);
      } else if (e.key === 'Escape') {
        setEvalModalOpen(false);
        setShortcutsOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeBranch.scenes.length]);

  const handleSelectScene = (branchId: string, sceneIndex: number) => {
    setIsPlayingStory(false);
    setActiveBranchId(branchId);
    setActiveSceneIndex(sceneIndex);
    setTargetSceneId(sceneIndex + 1);
  };

  // W2-T6B: One-click booth reset back to branch_v1 Scene 1 without page reload
  const handleResetDemo = () => {
    const baseBranch =
      branches.find((b) => b.branch_id === 'branch_v1') ?? branches[0];
    setIsPlayingStory(false);
    setCompareEnabled(false);
    setEvalModalOpen(false);
    setPixelArtMode(false);
    setPendingConflict(null);
    setRenderProgress(null);
    setPipelineStage(0);
    setStatusNote(null);
    setCompareBranchId(null);
    if (baseBranch) {
      setActiveBranchId(baseBranch.branch_id);
      setActiveSceneIndex(0);
      setTargetSceneId(2);
      setViewMode(
        !isMockMode && baseBranch.scenes[0]?.media.cam_a_final_url
          ? 'final'
          : 'preview',
      );
    }
  };

  const handleSubmitFork = async (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
    parentBranchIdOverride?: string;
    targetSceneIdOverride?: number;
  }) => {
    const effectiveParentBranchId =
      opts.parentBranchIdOverride ?? activeBranch.branch_id;
    const effectiveTargetSceneId =
      opts.targetSceneIdOverride ?? targetSceneId;

    setIsPlayingStory(false);
    setSubmitting(true);
    setPipelineStage(1);
    setPendingConflict(null);
    setStatusNote('Step 1/4: Hydrogen parsing multimodal input…');
    const stepTimer = window.setTimeout(() => {
      setPipelineStage(2);
      setStatusNote('Step 2/4: Gemini 4 Neon simulating causal world state…');
    }, 1800);

    const { branch: newBranch, simulated } = await submitDirectorFork({
      parentBranchId: effectiveParentBranchId,
      targetSceneId: effectiveTargetSceneId,
      clip: opts.clip,
      text: opts.text,
      confirmContradiction: opts.confirmContradiction,
    });
    window.clearTimeout(stepTimer);
    setSubmitting(false);
    setPipelineStage(0);

    if (newBranch.contradiction_warning) {
      setPendingConflict({
        parentBranchId: effectiveParentBranchId,
        targetSceneId: effectiveTargetSceneId,
        clip: opts.clip,
        text: opts.text,
        warning: newBranch.contradiction_warning,
      });
      setStatusNote('Contradiction detected — confirm below to force rewrite.');
      return;
    }

    setBranches((prev) => [...prev, newBranch]);
    setActiveBranchId(newBranch.branch_id);
    setActiveSceneIndex(Math.max(0, effectiveTargetSceneId - 1));
    setTargetSceneId(effectiveTargetSceneId);
    setCompareEnabled(true);
    playCompletionChime(soundEnabled);
    setStatusNote(
      simulated
        ? 'Offline mock simulation: forked new timeline on 2.5D stage.'
        : `Forked "${getBranchLabelEn(newBranch)}" — 2.5D stage synced (<1s).`,
    );
  };

  const handleForceRewrite = async () => {
    if (!pendingConflict) return;
    await handleSubmitFork({
      clip: pendingConflict.clip,
      text: pendingConflict.text,
      confirmContradiction: true,
      parentBranchIdOverride: pendingConflict.parentBranchId,
      targetSceneIdOverride: pendingConflict.targetSceneId,
    });
  };

  const handleRenderScene = async (
    scene: Scene,
    preview: PreviewExportResult,
  ) => {
    if (renderingScene) return;
    setRenderingScene(true);
    setPipelineStage(3);
    setStatusNote(`Step 3/4: Uploading Scene ${scene.scene_id} 2.5D preview…`);
    const renderStepTimer = window.setTimeout(() => {
      setPipelineStage(4);
      setStatusNote(`Step 4/4: Omni Flash dual-take rendering + Decision-1 QA…`);
    }, 1200);
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
    window.clearTimeout(renderStepTimer);
    setRenderingScene(false);
    setPipelineStage(0);

    if (!result) {
      setStatusNote('Render API unreachable — staying on live 2.5D preview.');
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
    playCompletionChime(soundEnabled);
    setStatusNote(
      result.media.fallback_to_preview
        ? `Scene ${scene.scene_id} degraded to 2.5D preview after QA retries.`
        : `Scene ${scene.scene_id} Omni Flash render passed QA!`,
    );
  };

  const handleRenderWholeBranch = async () => {
    if (renderingScene) return;
    setRenderingScene(true);
    setPipelineStage(4);
    setStatusNote(`Step 4/4: Concurrent Omni Flash dual-take + Decision-1 QA for ${getBranchLabelEn(activeBranch)}…`);
    const updated = await requestBranchRender(activeBranch.branch_id);
    setRenderingScene(false);
    setPipelineStage(0);
    if (!updated) {
      setStatusNote('Batch render failed — verify FastAPI backend is running.');
      return;
    }
    setBranches((prev) =>
      prev.map((b) => (b.branch_id === updated.branch_id ? updated : b)),
    );
    setViewMode('final');
    playCompletionChime(soundEnabled);
    setStatusNote(`All affected scenes rendered for ${getBranchLabelEn(updated)}!`);
  };

  // W2-T7C: Non-blocking demo safety escape to cached Starlight branch if live render > 35s
  const handleSwitchToCachedSafeBranch = () => {
    const safeBranch =
      branches.find((b) => b.branch_id === 'branch_v3_starlight_cached') ??
      branches.find((b) => b.branch_id === 'branch_v2_cached') ??
      branches.find((b) => b.branch_id !== 'branch_v1') ??
      branches[0];
    if (!safeBranch) return;
    setActiveBranchId(safeBranch.branch_id);
    setActiveSceneIndex(1);
    setTargetSceneId(2);
    setCompareEnabled(true);
    setViewMode(safeBranch.scenes[1]?.media.cam_a_final_url ? 'final' : 'preview');
    setStatusNote(`Switched seamlessly to pre-cached "${getBranchLabelEn(safeBranch)}".`);
  };

  const handleCreateProp = async (opts: {
    name: string;
    imageBlob?: Blob | null;
    placement: PropPlacement;
  }) => {
    const currentSceneId = activeScene.scene_id;
    setCreatingProp(true);
    setStatusNote(`Generating transparent stage prop "${opts.name}" (${opts.placement})…`);
    const created = await createStageProp({
      name: opts.name,
      description: opts.name,
      branchId: activeBranch.branch_id,
      sceneId: currentSceneId,
      imageBlob: opts.imageBlob,
    });
    setCreatingProp(false);

    const onFloor = opts.placement === 'floor';
    const propState: 'held' | 'glowing' = opts.placement === 'held' ? 'held' : 'glowing';
    const propGrid: [number, number] =
      opts.placement === 'table'
        ? [1.08, 1.08]
        : opts.placement === 'floor'
          ? [1.15, 1.48]
          : [0.92, 1.15];

    setBranches((prev) =>
      prev.map((b) => {
        if (b.branch_id !== activeBranch.branch_id) return b;
        return {
          ...b,
          props_registry: {
            ...b.props_registry,
            [created.prop_id]: {
              ...created.item,
              state: propState,
            },
          },
          scenes: b.scenes.map((s) => {
            if (s.scene_id < currentSceneId) return s;
            const nextActors =
              opts.placement === 'held'
                ? s.actors.map((a) =>
                    a.id === 'lia' ? { ...a, holding_prop: created.prop_id } : a,
                  )
                : s.actors;
            const exists = s.stage_props.some((p) => p.id === created.prop_id);
            const nextStageProps =
              opts.placement === 'held'
                ? s.stage_props.filter((p) => p.id !== created.prop_id)
                : exists
                  ? s.stage_props.map((p) =>
                      p.id === created.prop_id
                        ? { ...p, grid: propGrid, on_floor: onFloor, state: propState }
                        : p,
                    )
                  : [
                      ...s.stage_props,
                      {
                        id: created.prop_id,
                        grid: propGrid,
                        on_floor: onFloor,
                        state: propState,
                      },
                    ];
            return {
              ...s,
              actors: nextActors,
              stage_props: nextStageProps,
            };
          }),
        };
      }),
    );

    setStatusNote(
      created.simulated
        ? `Placed simulated prop "${opts.name}" (${opts.placement}) across Scenes ${currentSceneId}–4.`
        : `Prop "${opts.name}" (${opts.placement}) generated & synced across Scenes ${currentSceneId}–4!`,
    );
  };

  const handleExportComplete = (result: PreviewExportResult) => {
    setStatusNote(
      result.mode === 'webm'
        ? `Dual-camera .webm export ready (${result.elapsedMs}ms)`
        : `Start+end PNG keyframe fallback ready (${result.elapsedMs}ms)`,
    );
  };

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="brand-group">
          <span className="brand-title">StageState</span>
          <span className="brand-subtitle">
            One-Sentence Dual-Camera 2.5D Stage Director
          </span>
          {!cleanCaptureMode &&
            (isMockMode ? (
              <button
                type="button"
                className="pill-btn subtle"
                style={{ fontSize: '0.7rem', padding: '3px 9px' }}
                onClick={() => loadBackendState(true)}
                disabled={reconnectingApi}
                title="Re-ping FastAPI backend without reloading the page"
              >
                {reconnectingApi ? '⏳ Reconnecting…' : 'Mock Fallback Mode · ↻ Retry API'}
              </button>
            ) : (
              <span className="cam-tag ai-ready" style={{ fontSize: '0.7rem' }}>
                Live FastAPI
              </span>
            ))}
        </div>

        <div className="toolbar-actions">
          <button
            type="button"
            className={`pill-btn blue ${viewMode === 'preview' ? 'active' : ''}`}
            onClick={() => setViewMode('preview')}
          >
            🎭 2.5D Stage
          </button>
          <button
            type="button"
            className={`pill-btn blue ${viewMode === 'final' ? 'active' : ''}`}
            onClick={() => setViewMode('final')}
          >
            ✨ AI Render
          </button>
          <button
            type="button"
            className={`pill-btn ${compareEnabled ? 'active' : ''}`}
            onClick={() => setCompareEnabled((v) => !v)}
          >
            ⚖️ Compare
          </button>
          <button
            type="button"
            className={`pill-btn subtle ${pixelArtMode ? 'active' : ''}`}
            onClick={() => setPixelArtMode((v) => !v)}
          >
            👾 Pixel
          </button>
          <button
            type="button"
            className="pill-btn subtle"
            onClick={() => setEvalModalOpen(true)}
          >
            📊 Eval
          </button>
          {!cleanCaptureMode && (
            <div style={{ position: 'relative' }}>
              <button
                type="button"
                className={`pill-btn subtle ${shortcutsOpen ? 'active' : ''}`}
                onClick={() => setShortcutsOpen((v) => !v)}
                title="Keyboard shortcuts cheat-sheet (?)"
                aria-label="Keyboard shortcuts"
              >
                ?
              </button>
              {shortcutsOpen && (
                <div className="shortcut-popover" role="dialog" aria-label="Keyboard shortcuts">
                  <div className="shortcut-title">Stage Presenter Shortcuts</div>
                  <div className="shortcut-row"><kbd>Space</kbd><span>Play / Stop 1→4</span></div>
                  <div className="shortcut-row"><kbd>1–4</kbd><span>Jump to Scene</span></div>
                  <div className="shortcut-row"><kbd>Tab</kbd><span>Toggle 2.5D / AI</span></div>
                  <div className="shortcut-row"><kbd>E</kbd><span>Toggle Eval Modal</span></div>
                  <div className="shortcut-row"><kbd>C</kbd><span>Toggle Clean View</span></div>
                  <div className="shortcut-row"><kbd>R</kbd><span>Reset Booth Demo</span></div>
                </div>
              )}
            </div>
          )}
          {!isMockMode && !cleanCaptureMode && (
            <button
              type="button"
              className="pill-btn subtle"
              onClick={handleRenderWholeBranch}
              disabled={renderingScene}
            >
              {renderingScene ? '⏳ Rendering…' : '🚀 Render Branch'}
            </button>
          )}
          <button
            type="button"
            className={`pill-btn subtle ${cleanCaptureMode ? 'active' : ''}`}
            onClick={() => setCleanCaptureMode((v) => !v)}
          >
            🎥 Clean View
          </button>
          <button
            type="button"
            className="pill-btn subtle"
            onClick={handleResetDemo}
            title="Reset stage cleanly to branch_v1 Scene 1 for the next judge"
          >
            ↺ Reset Demo
          </button>
        </div>
      </header>

      {pipelineStage > 0 && (
        <div className="pipeline-stepper" role="status" aria-live="polite">
          {[
            '1. Hydrogen Parse',
            '2. Neon Causal Sim',
            '3. 2.5D Preview Upload',
            '4. Omni Flash Dual-Take + Decision-1 QA',
          ].map((label, idx) => {
            const stepNum = idx + 1;
            const stateClass =
              stepNum < pipelineStage
                ? 'done'
                : stepNum === pipelineStage
                  ? 'active'
                  : '';
            return (
              <span key={label} className={`pipeline-step ${stateClass}`}>
                {stepNum < pipelineStage ? '✓ ' : stepNum === pipelineStage ? '⏳ ' : ''}
                {label}
              </span>
            );
          })}
        </div>
      )}

      {!cleanCaptureMode && statusNote && pipelineStage === 0 && (
        <div className="status-banner gold" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
          <span>{statusNote}</span>
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>
            Keys: Space (Play 1→4) · 1–4 (Scene) · Tab (2.5D/AI) · C (Clean View)
          </span>
        </div>
      )}

      <DualCameraStage
        activeBranch={activeBranch}
        compareBranch={compareBranch}
        allBranches={branches}
        activeSceneIndex={activeSceneIndex}
        viewMode={viewMode}
        compareEnabled={compareEnabled}
        pixelArtMode={pixelArtMode}
        renderingScene={renderingScene}
        renderProgress={renderProgress}
        isPlayingStory={isPlayingStory}
        soundEnabled={soundEnabled}
        modalOpen={evalModalOpen}
        onSoundEnabledChange={setSoundEnabled}
        cleanCaptureMode={cleanCaptureMode}
        onSelectCompareBranch={setCompareBranchId}
        onSwitchToCachedSafeBranch={handleSwitchToCachedSafeBranch}
        onTogglePlayStory={handleTogglePlayStory}
        onExportComplete={handleExportComplete}
        onRenderScene={handleRenderScene}
      />

      <ClapperboardQA
        scene={activeScene}
        rules={rules}
        propsRegistry={activeBranch.props_registry}
        cleanCaptureMode={cleanCaptureMode}
      />

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
        creatingProp={creatingProp}
        contradictionWarning={
          pendingConflict?.warning ?? activeBranch.contradiction_warning
        }
        cleanCaptureMode={cleanCaptureMode}
        onDismissContradiction={() => setPendingConflict(null)}
        onForceRewrite={handleForceRewrite}
        onCreateProp={handleCreateProp}
        onSubmitFork={handleSubmitFork}
      />

      <EvalModal
        open={evalModalOpen}
        onClose={() => setEvalModalOpen(false)}
        isMockMode={isMockMode}
        branchCount={branches.length}
      />
    </div>
  );
}
