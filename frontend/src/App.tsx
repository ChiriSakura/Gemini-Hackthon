/**
 * StageState Root Application.
 *
 * Feature & Use Cases:
 * - First view has three zones: the dual-camera stage, the save tree of scene
 *   snapshots, and one input dock pinned at the bottom (type, hold to talk,
 *   show an object). Everything else lives behind a single overflow menu.
 * - Saved runs (branches generated earlier and kept on disk) are one click away
 *   on the tree; a live rewrite via `POST /api/director/fork` grows a new
 *   branch. The header badge says whether live generation is available.
 * - Honesty signals stay visible in the first view: the header badge, the
 *   error line with an "Open a saved run" action, the "Heard / Saw" echo, and
 *   the waiting counter for long backend calls.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  createStageProp,
  fetchBranches,
  fetchDirectorRules,
  fetchHealth,
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
import {
  DualCameraStage,
  type DualCameraStageHandle,
} from './components/DualCameraStage';
import {
  IconChart,
  IconClapper,
  IconCompare,
  IconDownload,
  IconEye,
  IconGrid,
  IconHalo,
  IconHelp,
  IconLayers,
  IconMore,
  IconPlug,
  IconReset,
  IconScissors,
  IconSync,
} from './components/Icons';
import { EvalModal } from './components/EvalModal';
import { StoryBranchTree } from './components/StoryBranchTree';
import { INITIAL_MOCK_BRANCHES } from './data/mockBranches';
import type { PreviewExportResult } from './stage/ThreeStageManager';
import type { BranchState, Scene } from './types/schema';
import { getBranchLabelEn } from './utils/displayLocale';
import {
  findParentScene,
  hasOwnFinalMedia,
  sceneKey,
  summarizeJudge,
} from './utils/sceneMedia';
import { playCompletionChime } from './utils/soundCue';

interface PendingForkInput {
  parentBranchId: string;
  targetSceneId: number;
  clip?: Blob | null;
  text?: string;
  warning: string;
}

// 'saved' = backend offline or without a model key: saved runs only, no live generation
type ApiStatus = 'checking' | 'live' | 'saved';

/**
 * Honest wait indicator: shows how long the current backend call has been
 * running instead of pretending to know which internal step it is on.
 */
function WaitingBanner({
  label,
  typical,
  since,
}: {
  label: string;
  typical: string;
  since: number;
}): React.ReactElement {
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    setElapsedSec(Math.floor((Date.now() - since) / 1000));
    const timer = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - since) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [since]);
  return (
    <div className="notice wait" role="status">
      <span>
        {label} — waiting for backend… {elapsedSec}s ({typical})
      </span>
    </div>
  );
}

export default function App(): React.ReactElement {
  const [branches, setBranches] = useState<BranchState[]>(INITIAL_MOCK_BRANCHES);
  const [isMockMode, setIsMockMode] = useState(true);
  const [apiStatus, setApiStatus] = useState<ApiStatus>('checking');
  const [backendReachable, setBackendReachable] = useState<boolean | null>(null);
  const [errorNote, setErrorNote] = useState<string | null>(null);
  const [errorSceneId, setErrorSceneId] = useState<number | null>(null);
  const [liveBranchIds, setLiveBranchIds] = useState<Set<string>>(() => new Set());
  const [autoCutEnabled, setAutoCutEnabled] = useState(false);
  const [diffHaloEnabled, setDiffHaloEnabled] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const stageRef = useRef<DualCameraStageHandle | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [renderedSceneKeys, setRenderedSceneKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [reconnectingApi, setReconnectingApi] = useState(false);
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [activeBranchId, setActiveBranchId] = useState<string>('branch_v1');
  const [compareBranchId, setCompareBranchId] = useState<string | null>(null);
  const [activeSceneIndex, setActiveSceneIndex] = useState<number>(0);
  const [viewMode, setViewMode] = useState<'preview' | 'final'>('preview');
  const [compareEnabled, setCompareEnabled] = useState<boolean>(false);
  const [pixelArtMode, setPixelArtMode] = useState<boolean>(false);
  const [cleanCaptureMode, setCleanCaptureMode] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return new URLSearchParams(window.location.search).get('clean') === '1';
  });
  const [evalModalOpen, setEvalModalOpen] = useState<boolean>(false);
  const [shortcutsOpen, setShortcutsOpen] = useState<boolean>(false);
  const [isPlayingStory, setIsPlayingStory] = useState<boolean>(false);
  const [targetSceneId, setTargetSceneId] = useState<number>(2);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [renderingScene, setRenderingScene] = useState<boolean>(false);
  const [renderProgress, setRenderProgress] = useState<RenderProgressSnapshot | null>(null);
  const [creatingProp, setCreatingProp] = useState<boolean>(false);
  const [busy, setBusy] = useState<{ label: string; typical: string; since: number } | null>(
    null,
  );
  const [heardNote, setHeardNote] = useState<{ heard: string | null; saw: string | null } | null>(
    null,
  );
  const [rules, setRules] = useState<string[]>([]);
  const [pendingConflict, setPendingConflict] = useState<PendingForkInput | null>(
    null,
  );
  const [statusNote, setStatusNote] = useState<string | null>(null);
  const submittingRef = useRef(false);

  // Re-read /api/health so the header badge reflects whether models are really live
  const refreshHealth = async (branchesFromMock: boolean) => {
    const health = await fetchHealth();
    setBackendReachable(health.reachable);
    setApiStatus(health.reachable && health.live && !branchesFromMock ? 'live' : 'saved');
    return health;
  };

  const loadBackendState = async (showFeedback = false) => {
    setReconnectingApi(true);
    const [{ branches: loaded, fromMock }, health] = await Promise.all([
      fetchBranches(),
      fetchHealth(),
    ]);
    setBranches(loaded);
    setIsMockMode(fromMock);
    setBackendReachable(health.reachable);
    setApiStatus(health.reachable && health.live && !fromMock ? 'live' : 'saved');
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
          ? 'Backend still offline — showing saved runs only.'
          : health.live
            ? `Connected to Live FastAPI (${loaded.length} branches loaded)!`
            : `Backend reachable (${loaded.length} saved runs) but it has no model key — live generation is off.`,
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
        setMenuOpen(false);
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
    setStatusNote(null);
    setErrorNote(null);
    setErrorSceneId(null);
    setHeardNote(null);
    setMenuOpen(false);
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
  }): Promise<boolean> => {
    // Ref guard: a double click cannot start two forks in the same render
    if (submittingRef.current) return false;
    submittingRef.current = true;
    const effectiveParentBranchId =
      opts.parentBranchIdOverride ?? activeBranch.branch_id;
    const effectiveTargetSceneId =
      opts.targetSceneIdOverride ?? targetSceneId;

    setIsPlayingStory(false);
    setSubmitting(true);
    setPendingConflict(null);
    setErrorNote(null);
    setStatusNote(null);
    setHeardNote(null);
    setBusy({
      label: opts.clip ? 'Reading your clip and rewriting the story' : 'Rewriting the story',
      typical: 'usually about 30s',
      since: Date.now(),
    });

    const { branch: newBranch, error: forkError } = await submitDirectorFork({
      parentBranchId: effectiveParentBranchId,
      targetSceneId: effectiveTargetSceneId,
      clip: opts.clip,
      text: opts.text,
      confirmContradiction: opts.confirmContradiction,
    });
    submittingRef.current = false;
    setSubmitting(false);
    setBusy(null);

    if (!newBranch) {
      setStatusNote(null);
      setErrorNote(`Rewrite failed: ${forkError ?? 'unknown error'}. No branch was added.`);
      setErrorSceneId(effectiveTargetSceneId);
      void refreshHealth(isMockMode);
      return false;
    }

    // Echo back what the backend understood from the voice/text and the camera
    const parentRegistry =
      branches.find((b) => b.branch_id === effectiveParentBranchId)?.props_registry ?? {};
    const sawProps = Object.entries(newBranch.props_registry)
      .filter(([id, item]) => item.origin === 'camera_physical' && !(id in parentRegistry))
      .map(([, item]) => item.name);
    setHeardNote({
      heard: newBranch.rewrite_summary,
      saw: sawProps.length > 0 ? sawProps.join(', ') : null,
    });

    if (newBranch.contradiction_warning) {
      setPendingConflict({
        parentBranchId: effectiveParentBranchId,
        targetSceneId: effectiveTargetSceneId,
        clip: opts.clip,
        text: opts.text,
        warning: newBranch.contradiction_warning,
      });
      setStatusNote('Contradiction detected — confirm below to force rewrite.');
      return false;
    }

    setBranches((prev) => [...prev, newBranch]);
    setLiveBranchIds((prev) => {
      const next = new Set(prev);
      next.add(newBranch.branch_id);
      return next;
    });
    setActiveBranchId(newBranch.branch_id);
    setActiveSceneIndex(Math.max(0, effectiveTargetSceneId - 1));
    setTargetSceneId(effectiveTargetSceneId);
    // A fresh fork has no rendered clips of its own yet: show the 2.5D preview
    setViewMode('preview');
    playCompletionChime(soundEnabled);
    const pendingCount = newBranch.scenes.filter(
      (s) =>
        !hasOwnFinalMedia(s, findParentScene(newBranch, branches, s.scene_id)),
    ).length;
    setStatusNote(
      `Forked "${getBranchLabelEn(newBranch)}" — 2.5D preview updated; ${pendingCount} scene${pendingCount === 1 ? '' : 's'} not rendered yet.`,
    );
    return true;
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
    setErrorNote(null);
    setRenderingScene(true);
    setStatusNote(null);
    setBusy({
      label: `Rendering Scene ${scene.scene_id} (two cameras + QA)`,
      typical: 'usually 40–60s',
      since: Date.now(),
    });
    const { result, error: renderError } = await requestSceneRender({
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
    setBusy(null);

    if (!result) {
      setErrorNote(
        `Render failed: ${renderError ?? 'unknown error'}. Staying on the 2.5D preview.`,
      );
      setErrorSceneId(scene.scene_id);
      void refreshHealth(isMockMode);
      return;
    }

    const renderedBranchId = activeBranch.branch_id;
    const hasClip = Boolean(
      result.media.cam_a_final_url || result.media.cam_b_final_url,
    );

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

    if (result.media.fallback_to_preview) {
      setStatusNote(`Scene ${scene.scene_id} degraded to 2.5D preview after QA retries.`);
      return;
    }
    if (!hasClip) {
      setStatusNote(`Scene ${scene.scene_id} render returned no clip — staying on 2.5D preview.`);
      return;
    }

    setRenderedSceneKeys((prev) => {
      const next = new Set(prev);
      next.add(sceneKey(renderedBranchId, result.scene_id));
      return next;
    });
    setViewMode('final');
    playCompletionChime(soundEnabled);
    const judge = summarizeJudge({ ...scene, media: result.media }, true);
    setStatusNote(
      !judge.judged
        ? `Scene ${scene.scene_id} rendered — no QA result returned.`
        : judge.allPassed
          ? `Scene ${scene.scene_id} rendered — QA passed.`
          : `Scene ${scene.scene_id} rendered — QA failed ${judge.total - judge.passedCount} of ${judge.total} checks.`,
    );
  };

  const handleRenderWholeBranch = async () => {
    if (renderingScene) return;
    setErrorNote(null);
    setRenderingScene(true);
    setStatusNote(null);
    setBusy({
      label: `Rendering all pending scenes of "${getBranchLabelEn(activeBranch)}" (two cameras + QA)`,
      typical: 'usually 40–60s',
      since: Date.now(),
    });
    const before = activeBranch;
    const { branch: updated, error: renderError } = await requestBranchRender(
      before.branch_id,
    );
    setRenderingScene(false);
    setBusy(null);
    if (!updated) {
      setErrorNote(`Branch render failed: ${renderError ?? 'unknown error'}.`);
      setErrorSceneId(before.forked_from_scene ?? null);
      void refreshHealth(isMockMode);
      return;
    }
    setBranches((prev) =>
      prev.map((b) => (b.branch_id === updated.branch_id ? updated : b)),
    );

    // Scenes whose final clip changed in this call were really rendered just now
    const newlyRendered = updated.scenes.filter((s) => {
      const old = before.scenes.find((o) => o.scene_id === s.scene_id);
      const hasClip = Boolean(s.media.cam_a_final_url || s.media.cam_b_final_url);
      return (
        hasClip &&
        !s.media.fallback_to_preview &&
        (!old ||
          old.media.cam_a_final_url !== s.media.cam_a_final_url ||
          old.media.cam_b_final_url !== s.media.cam_b_final_url)
      );
    });
    const degraded = updated.scenes.filter((s) => s.media.fallback_to_preview);
    if (newlyRendered.length > 0) {
      setRenderedSceneKeys((prev) => {
        const next = new Set(prev);
        for (const s of newlyRendered) {
          next.add(sceneKey(updated.branch_id, s.scene_id));
        }
        return next;
      });
      setViewMode('final');
      playCompletionChime(soundEnabled);
    }
    const failedQa = newlyRendered.filter((s) => {
      const j = summarizeJudge(s, true);
      return j.judged && !j.allPassed;
    });
    setStatusNote(
      newlyRendered.length === 0
        ? `No new clips were rendered for ${getBranchLabelEn(updated)}${degraded.length > 0 ? ` (${degraded.length} degraded to 2.5D preview)` : ''}.`
        : `Rendered ${newlyRendered.length} scene${newlyRendered.length === 1 ? '' : 's'} for ${getBranchLabelEn(updated)}` +
            (failedQa.length > 0
              ? ` — QA failed on Scene ${failedQa.map((s) => s.scene_id).join(', ')}.`
              : '.') +
            (degraded.length > 0 ? ` ${degraded.length} degraded to 2.5D preview.` : ''),
    );
  };

  // Explicit escape hatch: open the closest saved run (never switched silently).
  // Prefers a saved fork at the same scene, then the first saved fork with clips.
  const handleOpenSavedRun = (preferredSceneId?: number | null) => {
    const hasClips = (b: BranchState) =>
      b.scenes.some((s) => hasOwnFinalMedia(s, findParentScene(b, branches, s.scene_id)));
    const savedForks = branches.filter(
      (b) =>
        !liveBranchIds.has(b.branch_id) &&
        b.branch_id !== activeBranch.branch_id &&
        Boolean(b.parent_branch_id),
    );
    const withClips = savedForks.filter(hasClips);
    const pool = withClips.length > 0 ? withClips : savedForks;
    const saved =
      pool.find((b) => preferredSceneId != null && b.forked_from_scene === preferredSceneId) ??
      pool[0] ??
      branches.find((b) => !liveBranchIds.has(b.branch_id) && b.branch_id !== activeBranch.branch_id);
    if (!saved) {
      setStatusNote('No saved run is available to open.');
      return;
    }
    const sceneIdx = Math.max(
      0,
      Math.min(saved.scenes.length - 1, (saved.forked_from_scene ?? 1) - 1),
    );
    const target = saved.scenes[sceneIdx];
    setIsPlayingStory(false);
    setActiveBranchId(saved.branch_id);
    setActiveSceneIndex(sceneIdx);
    setTargetSceneId(sceneIdx + 1);
    setViewMode(
      target && hasOwnFinalMedia(target, findParentScene(saved, branches, target.scene_id))
        ? 'final'
        : 'preview',
    );
    setErrorNote(null);
    setErrorSceneId(null);
    setStatusNote(`Opened saved run "${getBranchLabelEn(saved)}" (Saved, generated earlier).`);
  };

  const handleCreateProp = async (opts: {
    name: string;
    imageBlob?: Blob | null;
    placement: PropPlacement;
  }) => {
    const currentSceneId = activeScene.scene_id;
    setErrorNote(null);
    setCreatingProp(true);
    setStatusNote(null);
    setBusy({
      label: `Creating stage prop "${opts.name}"`,
      typical: 'usually about 35s',
      since: Date.now(),
    });
    const created = await createStageProp({
      name: opts.name,
      description: opts.name,
      branchId: activeBranch.branch_id,
      sceneId: currentSceneId,
      imageBlob: opts.imageBlob,
    });
    setCreatingProp(false);
    setBusy(null);
    if (created.simulated) {
      void refreshHealth(isMockMode);
    }

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
        ? `Prop generation failed — placed your raw camera snapshot "${opts.name}" (${opts.placement}) on Scenes ${currentSceneId}–4 instead.`
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

  // Close the overflow menu when clicking anywhere outside it
  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (e: PointerEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    window.addEventListener('pointerdown', onPointerDown);
    return () => window.removeEventListener('pointerdown', onPointerDown);
  }, [menuOpen]);

  const activeParentScene = findParentScene(activeBranch, branches, activeScene.scene_id);
  const activeOwnsRender = hasOwnFinalMedia(activeScene, activeParentScene);
  const hasSavedRun = branches.some(
    (b) => !liveBranchIds.has(b.branch_id) && b.branch_id !== activeBranch.branch_id,
  );

  const menuItems: Array<
    | 'sep'
    | {
        label: string;
        icon: React.ReactNode;
        onSelect: () => void;
        checked?: boolean;
        disabled?: boolean;
        keepOpen?: boolean;
        hint?: string;
      }
  > = [
    {
      label: 'Compare before / after',
      icon: <IconCompare />,
      checked: compareEnabled,
      onSelect: () => setCompareEnabled((v) => !v),
    },
    {
      label: 'Auto-cut between cameras',
      icon: <IconScissors />,
      checked: autoCutEnabled,
      onSelect: () => setAutoCutEnabled((v) => !v),
    },
    {
      label: 'Pixel preview',
      icon: <IconGrid />,
      checked: pixelArtMode,
      onSelect: () => setPixelArtMode((v) => !v),
    },
    {
      label: 'Highlight changed actors',
      icon: <IconHalo />,
      checked: diffHaloEnabled,
      onSelect: () => setDiffHaloEnabled((v) => !v),
    },
    'sep',
    {
      label: 'Render this scene',
      icon: <IconClapper />,
      disabled: renderingScene,
      onSelect: () => void stageRef.current?.exportPreview(true),
    },
    {
      label: 'Render whole branch',
      icon: <IconLayers />,
      disabled: renderingScene || isMockMode,
      onSelect: () => void handleRenderWholeBranch(),
    },
    {
      label: 'Sync replay',
      icon: <IconSync />,
      onSelect: () => stageRef.current?.syncReplay(),
    },
    {
      label: 'Export 4s preview',
      icon: <IconDownload />,
      disabled: renderingScene,
      onSelect: () => void stageRef.current?.exportPreview(false),
    },
    'sep',
    {
      label: 'Eval',
      icon: <IconChart />,
      hint: 'E',
      onSelect: () => setEvalModalOpen(true),
    },
    {
      label: 'Clean view',
      icon: <IconEye />,
      checked: cleanCaptureMode,
      hint: 'C',
      onSelect: () => setCleanCaptureMode((v) => !v),
    },
    {
      label: 'Reset demo',
      icon: <IconReset />,
      hint: 'R',
      onSelect: handleResetDemo,
    },
    {
      label: reconnectingApi ? 'Reconnecting' : 'Reconnect to backend',
      icon: <IconPlug />,
      disabled: reconnectingApi,
      onSelect: () => void loadBackendState(true),
    },
    {
      label: 'Help',
      icon: <IconHelp />,
      hint: '?',
      onSelect: () => setShortcutsOpen(true),
    },
  ];

  return (
    <div className="app-shell" data-mood={activeScene.lighting}>
      <header className="topbar">
        <span className="brand" title="One-sentence dual-camera 2.5D stage director">
          StageState
        </span>
        {apiStatus === 'live' && (
          <span className="mode-badge live" role="status" title="Models are reachable: rewrites and renders run live">
            Live
          </span>
        )}
        {apiStatus === 'saved' && (
          <span
            className="mode-badge saved"
            role="status"
            title={
              backendReachable
                ? 'The backend has no model key: saved runs play, new rewrites and renders are unavailable.'
                : 'The backend is unreachable: showing the built-in storyboard of the saved runs (no clips). New rewrites and renders are unavailable.'
            }
          >
            Saved runs only · live generation off
          </span>
        )}
        {apiStatus === 'checking' && <span className="mode-badge">Checking backend</span>}
        <span className="topbar-spacer" />
        <div ref={menuRef}>
          <button
            type="button"
            className={`icon-btn ${menuOpen ? 'on' : ''}`}
            aria-label="More"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title="More"
            onClick={() => {
              setMenuOpen((v) => !v);
              setShortcutsOpen(false);
            }}
          >
            <IconMore size={20} />
          </button>
          {menuOpen && (
            <div className="menu" role="menu">
              {menuItems.map((item, idx) =>
                item === 'sep' ? (
                  <div key={idx} className="menu-sep" role="separator" />
                ) : (
                  <button
                    key={item.label}
                    type="button"
                    className="menu-item"
                    role={item.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
                    aria-checked={item.checked === undefined ? undefined : item.checked}
                    disabled={item.disabled}
                    onClick={() => {
                      item.onSelect();
                      if (item.checked === undefined) setMenuOpen(false);
                    }}
                  >
                    {item.icon}
                    <span>{item.label}</span>
                    <span className="menu-state">
                      {item.checked === undefined ? (item.hint ?? '') : item.checked ? 'On' : 'Off'}
                    </span>
                  </button>
                ),
              )}
            </div>
          )}
          {shortcutsOpen && (
            <div className="help-pop" role="dialog" aria-label="Keyboard shortcuts">
              <div className="shortcut-title">Keyboard shortcuts</div>
              <div className="shortcut-row"><kbd>Space</kbd><span>Play / stop scenes 1 to 4</span></div>
              <div className="shortcut-row"><kbd>1–4</kbd><span>Jump to scene</span></div>
              <div className="shortcut-row"><kbd>Tab</kbd><span>2.5D preview / film clips</span></div>
              <div className="shortcut-row"><kbd>E</kbd><span>Eval</span></div>
              <div className="shortcut-row"><kbd>C</kbd><span>Clean view</span></div>
              <div className="shortcut-row"><kbd>R</kbd><span>Reset demo</span></div>
              <div className="shortcut-row"><kbd>Esc</kbd><span>Close</span></div>
            </div>
          )}
        </div>
      </header>

      <DualCameraStage
        ref={stageRef}
        activeBranch={activeBranch}
        compareBranch={compareBranch}
        allBranches={branches}
        activeSceneIndex={activeSceneIndex}
        viewMode={viewMode}
        compareEnabled={compareEnabled}
        pixelArtMode={pixelArtMode}
        autoCutEnabled={autoCutEnabled}
        diffHaloEnabled={diffHaloEnabled}
        renderingScene={renderingScene}
        renderProgress={renderProgress}
        isPlayingStory={isPlayingStory}
        soundEnabled={soundEnabled}
        modalOpen={evalModalOpen}
        sceneRenderedLive={renderedSceneKeys.has(
          sceneKey(activeBranch.branch_id, activeScene.scene_id),
        )}
        cleanCaptureMode={cleanCaptureMode}
        onSoundEnabledChange={setSoundEnabled}
        onViewModeChange={setViewMode}
        onSelectCompareBranch={setCompareBranchId}
        onOpenSavedRun={hasSavedRun ? () => handleOpenSavedRun(activeScene.scene_id) : undefined}
        onTogglePlayStory={handleTogglePlayStory}
        onExportStart={() => setStatusNote('Capturing a 4s dual-camera preview…')}
        onExportComplete={handleExportComplete}
        onRenderScene={handleRenderScene}
        footer={
          <ClapperboardQA
            scene={activeScene}
            hasOwnRender={activeOwnsRender}
            rules={rules}
            propsRegistry={activeBranch.props_registry}
            cleanCaptureMode={cleanCaptureMode}
          />
        }
      />

      <StoryBranchTree
        branches={branches}
        activeBranchId={activeBranch.branch_id}
        activeSceneIndex={activeSceneIndex}
        renderedSceneKeys={renderedSceneKeys}
        liveBranchIds={liveBranchIds}
        onSelectScene={handleSelectScene}
      />

      <div className="dock">
        {errorNote && (
          <div className="notice error" role="alert">
            <span>
              <strong>{errorNote}</strong>
            </span>
            {hasSavedRun && (
              <button
                type="button"
                className="notice-btn strong"
                onClick={() => handleOpenSavedRun(errorSceneId)}
              >
                Open a saved run
              </button>
            )}
            <button
              type="button"
              className="notice-btn"
              onClick={() => {
                setErrorNote(null);
                setErrorSceneId(null);
              }}
            >
              Dismiss
            </button>
          </div>
        )}

        {busy && <WaitingBanner label={busy.label} typical={busy.typical} since={busy.since} />}

        {heardNote && (heardNote.heard || heardNote.saw) && (
          <div className="notice heard" role="status">
            <span>
              {heardNote.heard && (
                <>
                  <strong>Heard:</strong> &ldquo;{heardNote.heard}&rdquo;
                </>
              )}
              {heardNote.heard && heardNote.saw && ' · '}
              {heardNote.saw && (
                <>
                  <strong>Saw:</strong> {heardNote.saw}
                </>
              )}
            </span>
          </div>
        )}

        {!cleanCaptureMode && statusNote && !busy && (
          <div className="notice" role="status">
            <span>{statusNote}</span>
          </div>
        )}

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
      </div>

      <EvalModal
        open={evalModalOpen}
        onClose={() => setEvalModalOpen(false)}
        isMockMode={isMockMode}
        apiLive={apiStatus === 'checking' ? null : apiStatus === 'live'}
        backendReachable={backendReachable}
        branchCount={branches.length}
      />
    </div>
  );
}
