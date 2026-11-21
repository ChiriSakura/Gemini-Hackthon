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
  extendDirectorStory,
  fetchBranches,
  fetchDirectorRules,
  fetchHealth,
  fetchRenderProgress,
  perceiveDirectorInput,
  requestBranchRender,
  requestSceneRender,
  submitDirectorFork,
  type RenderProgressSnapshot,
} from './api/stageClient';
import {
  CameraSelfView,
  type ThrowObjectSubmission,
} from './components/CameraSelfView';
import { ClapperboardQA } from './components/ClapperboardQA';
import { DirectControlBar } from './components/DirectControlBar';
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

interface PendingThrowPrompt {
  branchId: string;
  fromSceneId: number;
  text: string;
  clip?: Blob | null;
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
  const [sculptureFrozen, setSculptureFrozen] = useState(false);
  const [treeExpanded, setTreeExpanded] = useState(false);
  const [showSavedExamples, setShowSavedExamples] = useState(false);
  const [showClapperboard, setShowClapperboard] = useState(false);
  const [throwOpen, setThrowOpen] = useState(false);
  const [throwHint, setThrowHint] = useState<string | null>(null);
  const [suggestedPropName, setSuggestedPropName] = useState<string | null>(null);
  const [pendingThrowPrompt, setPendingThrowPrompt] = useState<PendingThrowPrompt | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const stageRef = useRef<DualCameraStageHandle | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);
  const freezeTimerRef = useRef<number | null>(null);
  const resetDemoRef = useRef<() => void>(() => {});
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
  const [, setTargetSceneId] = useState<number>(2);
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

  // R-33: Alt-guarded global keyboard shortcuts so normal typing never misfires
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName ?? '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target?.isContentEditable) {
        if (e.key === 'Escape') {
          setThrowOpen(false);
          setShortcutsOpen(false);
          setMenuOpen(false);
        }
        return;
      }
      if (e.key === 'Escape') {
        setEvalModalOpen(false);
        setShortcutsOpen(false);
        setMenuOpen(false);
        setThrowOpen(false);
        return;
      }
      if (!e.altKey || e.metaKey || e.ctrlKey) {
        if (e.key === '?') setShortcutsOpen((v) => !v);
        return;
      }
      const key = e.key.toLowerCase();
      if (e.code === 'Space') {
        e.preventDefault();
        handleTogglePlayStory();
      } else if (e.key >= '1' && e.key <= '9') {
        const idx = Number(e.key) - 1;
        if (idx < activeBranch.scenes.length) {
          e.preventDefault();
          setIsPlayingStory(false);
          setActiveSceneIndex(idx);
          setTargetSceneId(activeBranch.scenes[idx]?.scene_id ?? idx + 1);
        }
      } else if (key === 'v') {
        e.preventDefault();
        setViewMode((m) => (m === 'preview' ? 'final' : 'preview'));
      } else if (key === 's') {
        e.preventDefault();
        setSculptureFrozen((v) => !v);
      } else if (key === 't') {
        e.preventDefault();
        setThrowHint('Toss or hold an everyday object (drink, snack, mug, laptop) into frame.');
        setSuggestedPropName(null);
        setThrowOpen((v) => !v);
      } else if (key === 'c') {
        e.preventDefault();
        setCleanCaptureMode((v) => !v);
      } else if (key === 'e') {
        e.preventDefault();
        setEvalModalOpen((v) => !v);
      } else if (key === 'r') {
        e.preventDefault();
        resetDemoRef.current();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [activeBranch.scenes]);

  const scheduleSculptureFreeze = (delayMs = 4400) => {
    if (freezeTimerRef.current !== null) {
      window.clearTimeout(freezeTimerRef.current);
    }
    freezeTimerRef.current = window.setTimeout(() => {
      freezeTimerRef.current = null;
      setSculptureFrozen(true);
    }, delayMs);
  };

  useEffect(() => {
    return () => {
      if (freezeTimerRef.current !== null) {
        window.clearTimeout(freezeTimerRef.current);
      }
    };
  }, []);

  const handleSelectScene = (branchId: string, sceneIndex: number) => {
    const targetBranch = branches.find((b) => b.branch_id === branchId);
    const chosenScene = targetBranch?.scenes[sceneIndex];
    setIsPlayingStory(false);
    setSculptureFrozen(false);
    setActiveBranchId(branchId);
    setActiveSceneIndex(sceneIndex);
    setTargetSceneId(chosenScene?.scene_id ?? sceneIndex + 1);
  };

  // R-26: Non-destructive stage reset back to starter Scene 1 (never deletes live branches)
  const handleResetDemo = () => {
    if (freezeTimerRef.current !== null) {
      window.clearTimeout(freezeTimerRef.current);
      freezeTimerRef.current = null;
    }
    const baseBranch =
      branches.find((b) => b.branch_id === 'branch_v1') ?? branches[0];
    setIsPlayingStory(false);
    setCompareEnabled(false);
    setEvalModalOpen(false);
    setPixelArtMode(false);
    setSculptureFrozen(false);
    setThrowOpen(false);
    setPendingThrowPrompt(null);
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
      setTargetSceneId(baseBranch.scenes[0]?.scene_id ?? 1);
      setViewMode(
        !isMockMode && baseBranch.scenes[0]?.media.cam_a_final_url
          ? 'final'
          : 'preview',
      );
    }
  };
  resetDemoRef.current = handleResetDemo;

  /**
   * Execute Stage-2 unbounded continuation (`POST /api/director/extend`) from
   * `fromSceneId`, jumping immediately to the new Scene N+1 in 2.5D preview
   * and auto-triggering Fast Live Mode rendering (`fast_live_mode=true`) when live.
   */
  const runStoryExtend = async (opts: {
    branchId: string;
    fromSceneId: number;
    text?: string;
    clip?: Blob | null;
  }): Promise<boolean> => {
    setBusy({
      label: `Continuing story into Scene ${opts.fromSceneId + 1}`,
      typical: 'usually 10–20s',
      since: Date.now(),
    });
    const { branch: extended, error: extendError } = await extendDirectorStory({
      branchId: opts.branchId,
      fromSceneId: opts.fromSceneId,
      text: opts.text,
      clip: opts.clip,
      endFreezeMode: 'sculpture',
      detectThrowPeak: true,
    });
    setBusy(null);

    if (!extended) {
      setErrorNote(`Continue failed: ${extendError ?? 'unknown error'}.`);
      setErrorSceneId(opts.fromSceneId);
      void refreshHealth(isMockMode);
      return false;
    }

    // Echo newly perceived props if any arrived via clip
    const parentRegistry =
      branches.find((b) => b.branch_id === opts.branchId)?.props_registry ?? {};
    const sawProps = Object.entries(extended.props_registry)
      .filter(([id, item]) => item.origin === 'camera_physical' && !(id in parentRegistry))
      .map(([, item]) => item.name);
    if (sawProps.length > 0 || extended.rewrite_summary) {
      setHeardNote((prev) => ({
        heard: extended.rewrite_summary ?? prev?.heard ?? null,
        saw: sawProps.length > 0 ? sawProps.join(', ') : prev?.saw ?? null,
      }));
    }

    setBranches((prev) => {
      const exists = prev.some((b) => b.branch_id === extended.branch_id);
      return exists
        ? prev.map((b) => (b.branch_id === extended.branch_id ? extended : b))
        : [...prev, extended];
    });
    setLiveBranchIds((prev) => {
      const next = new Set(prev);
      next.add(extended.branch_id);
      return next;
    });

    const newSceneIdx = Math.max(0, extended.scenes.length - 1);
    const newScene = extended.scenes[newSceneIdx];
    setActiveBranchId(extended.branch_id);
    setActiveSceneIndex(newSceneIdx);
    setTargetSceneId(newScene?.scene_id ?? opts.fromSceneId + 1);
    setViewMode('preview');
    setSculptureFrozen(false);
    playCompletionChime(soundEnabled);

    setStatusNote(
      `Scene ${newScene?.scene_id ?? opts.fromSceneId + 1} staged live — ${
        apiStatus === 'live' ? 'rendering Fast Live clip (10–20s)…' : '2.5D preview ready.'
      }`,
    );

    if (apiStatus === 'live' && newScene) {
      // Trigger fast live render (10–20s) right after 2.5D preview updates
      void handleRenderSceneWithMode(extended.branch_id, newScene, null, true);
    } else if (newScene?.end_freeze_mode !== 'none') {
      scheduleSculptureFreeze(4400);
    }
    return true;
  };

  /**
   * Primary V2 director entry point (W2-V2-04):
   * 1) Unfreezes sculpture and calls `POST /api/director/perceive` (~2s Heard/Saw).
   * 2) If `needs_prop_prompt=true`, opens bottom-right Camera Self-View Throw-In.
   * 3) Otherwise continues immediately via `POST /api/director/extend` (unbounded S1..∞).
   */
  const handleSubmitPrompt = async (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
    parentBranchIdOverride?: string;
    targetSceneIdOverride?: number;
  }): Promise<boolean> => {
    if (submittingRef.current) return false;
    submittingRef.current = true;

    const anchorBranchId = opts.parentBranchIdOverride ?? activeBranch.branch_id;
    const anchorSceneId = opts.targetSceneIdOverride ?? activeScene.scene_id;
    const nextSceneId = anchorSceneId + 1;

    setIsPlayingStory(false);
    setSculptureFrozen(false);
    setSubmitting(true);
    setPendingConflict(null);
    setErrorNote(null);
    setStatusNote(null);
    setHeardNote(null);

    // If explicitly confirming a legacy fork contradiction, run `/api/director/fork`
    if (opts.confirmContradiction) {
      setBusy({
        label: 'Rewriting story',
        typical: 'usually 15–30s',
        since: Date.now(),
      });
      const { branch: newBranch, error: forkError } = await submitDirectorFork({
        parentBranchId: anchorBranchId,
        targetSceneId: anchorSceneId,
        clip: opts.clip,
        text: opts.text,
        confirmContradiction: true,
      });
      submittingRef.current = false;
      setSubmitting(false);
      setBusy(null);
      if (!newBranch) {
        setErrorNote(`Rewrite failed: ${forkError ?? 'unknown error'}.`);
        setErrorSceneId(anchorSceneId);
        return false;
      }
      setBranches((prev) => [...prev, newBranch]);
      setLiveBranchIds((prev) => new Set(prev).add(newBranch.branch_id));
      setActiveBranchId(newBranch.branch_id);
      setActiveSceneIndex(Math.max(0, anchorSceneId - 1));
      setViewMode('preview');
      return true;
    }

    // Stage 1: Fast perception (~2s Heard / Saw + needs_prop_prompt)
    setBusy({
      label: opts.clip ? 'Listening & watching your clip' : 'Understanding your direction',
      typical: '~2s',
      since: Date.now(),
    });
    const { parsed } = await perceiveDirectorInput({
      targetSceneId: nextSceneId,
      clip: opts.clip,
      text: opts.text,
    });

    if (parsed) {
      setHeardNote({
        heard: parsed.instruction,
        saw: parsed.prop_name ?? (parsed.gesture_intent !== 'none' ? parsed.gesture_intent : null),
      });
      if (parsed.needs_prop_prompt) {
        submittingRef.current = false;
        setSubmitting(false);
        setBusy(null);
        setPendingThrowPrompt({
          branchId: anchorBranchId,
          fromSceneId: anchorSceneId,
          text: parsed.instruction || opts.text || '',
          clip: opts.clip,
        });
        setThrowHint(
          parsed.prop_prompt_hint ??
            'Toss an everyday object (drink, snack, mug, laptop) into your camera!',
        );
        setSuggestedPropName(parsed.prop_name ?? null);
        setThrowOpen(true);
        setStatusNote('Story wants an object from you — toss one into the bottom-right camera!');
        return true;
      }
    }

    // Stage 2: Unbounded continuation via `/api/director/extend`
    const ok = await runStoryExtend({
      branchId: anchorBranchId,
      fromSceneId: anchorSceneId,
      text: parsed?.instruction || opts.text,
      clip: opts.clip,
    });
    submittingRef.current = false;
    setSubmitting(false);
    return ok;
  };

  const handleForceRewrite = async () => {
    if (!pendingConflict) return;
    await handleSubmitPrompt({
      clip: pendingConflict.clip,
      text: pendingConflict.text,
      confirmContradiction: true,
      parentBranchIdOverride: pendingConflict.parentBranchId,
      targetSceneIdOverride: pendingConflict.targetSceneId,
    });
  };

  const handleRenderSceneWithMode = async (
    branchId: string,
    scene: Scene,
    preview: PreviewExportResult | null,
    fastLiveMode: boolean,
  ) => {
    if (renderingScene) return;
    setErrorNote(null);
    setRenderingScene(true);
    setStatusNote(null);
    setBusy({
      label: fastLiveMode
        ? `Fast-rendering Scene ${scene.scene_id}`
        : `Rendering Scene ${scene.scene_id} (two cameras + QA)`,
      typical: fastLiveMode ? 'usually 10–20s' : 'usually 40–60s',
      since: Date.now(),
    });

    const invokeOnce = () =>
      requestSceneRender({
        branchId,
        scene,
        camAPreview: preview?.camABlob ?? null,
        camBPreview: preview?.camBBlob ?? null,
        camAStartPng: preview?.camAStartPng ?? null,
        camAEndPng: preview?.camAEndPng ?? null,
        camBStartPng: preview?.camBStartPng ?? null,
        camBEndPng: preview?.camBEndPng ?? null,
        fastLiveMode,
      });

    let { result, error: renderError } = await invokeOnce();

    // W2-V2-04: Explicit content-blocked notice + single automatic retry
    if (
      !result &&
      renderError &&
      /content.?blocked|safety|policy|blocked/i.test(renderError)
    ) {
      setStatusNote('content_blocked, retrying once…');
      ({ result, error: renderError } = await invokeOnce());
    }

    setRenderingScene(false);
    setBusy(null);

    if (!result) {
      const blockedPrefix =
        renderError && /content.?blocked|safety|policy|blocked/i.test(renderError)
          ? 'content_blocked after retry — '
          : '';
      setErrorNote(
        `${blockedPrefix}Render failed: ${renderError ?? 'unknown error'}. Staying on the 2.5D live stage.`,
      );
      setErrorSceneId(scene.scene_id);
      void refreshHealth(isMockMode);
      if (scene.end_freeze_mode !== 'none') scheduleSculptureFreeze(3000);
      return;
    }

    const hasClip = Boolean(
      result.media.cam_a_final_url || result.media.cam_b_final_url,
    );

    setBranches((prev) =>
      prev.map((b) =>
        b.branch_id !== branchId
          ? b
          : {
              ...b,
              scenes: b.scenes.map((s) =>
                s.scene_id === result!.scene_id
                  ? {
                      ...s,
                      media: result!.media,
                      environment_notes:
                        result!.environment_notes ?? s.environment_notes,
                    }
                  : s,
              ),
            },
      ),
    );

    if (result.media.fallback_to_preview) {
      setStatusNote(`Scene ${scene.scene_id} degraded to 2.5D preview.`);
      if (scene.end_freeze_mode !== 'none') scheduleSculptureFreeze(3500);
      return;
    }
    if (!hasClip) {
      setStatusNote(`Scene ${scene.scene_id} returned no clip — staying on 2.5D preview.`);
      if (scene.end_freeze_mode !== 'none') scheduleSculptureFreeze(3500);
      return;
    }

    setRenderedSceneKeys((prev) => {
      const next = new Set(prev);
      next.add(sceneKey(branchId, result!.scene_id));
      return next;
    });
    setViewMode('final');
    playCompletionChime(soundEnabled);
    const judge = summarizeJudge({ ...scene, media: result.media }, true);
    setStatusNote(
      !judge.judged
        ? `Scene ${scene.scene_id} rendered live.`
        : judge.allPassed
          ? `Scene ${scene.scene_id} rendered live — QA passed.`
          : `Scene ${scene.scene_id} rendered live — QA flagged ${judge.total - judge.passedCount}/${judge.total} checks.`,
    );
  };

  const handleRenderScene = async (
    scene: Scene,
    preview: PreviewExportResult,
  ) => {
    await handleRenderSceneWithMode(activeBranch.branch_id, scene, preview, true);
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

  const handleSubmitThrow = async (payload: ThrowObjectSubmission) => {
    const currentSceneId = activeScene.scene_id;
    const targetBranchId = activeBranch.branch_id;
    setThrowOpen(false);
    setErrorNote(null);
    setCreatingProp(true);
    setSculptureFrozen(false);
    setStatusNote(null);
    setBusy({
      label: `Turning "${payload.name}" into a 2D anime stage prop`,
      typical: 'usually 10–20s',
      since: Date.now(),
    });
    const created = await createStageProp({
      name: payload.name,
      description: payload.name,
      branchId: targetBranchId,
      sceneId: currentSceneId,
      placement: payload.placement,
      entryMotion: payload.entryMotion,
      detectThrowPeak: payload.detectThrowPeak,
      imageBlob: payload.mediaBlob,
    });
    setCreatingProp(false);
    setBusy(null);
    if (created.simulated) {
      void refreshHealth(isMockMode);
    }

    const isExplicitToss =
      payload.entryMotion === 'throw_arc' || payload.placement === 'held';
    const onFloor = payload.placement === 'floor';
    const propState: 'held' | 'glowing' = payload.placement === 'held' ? 'held' : 'glowing';
    const propGrid: [number, number] =
      payload.placement === 'table'
        ? [1.08, 1.08]
        : payload.placement === 'floor'
          ? [1.15, 1.48]
          : [0.92, 1.15];

    setBranches((prev) =>
      prev.map((b) => {
        if (b.branch_id !== targetBranchId) return b;
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
            // W2-V2-02/03 (15:06 rule): Only trigger `catch_from_camera` when explicitly
            // handing/tossing an object to Lia; default table/floor props leave Lia in her normal pose.
            const nextActors = isExplicitToss
              ? s.actors.map((a) =>
                  a.id === 'lia'
                    ? {
                        ...a,
                        holding_prop:
                          payload.placement === 'held' ? created.prop_id : a.holding_prop,
                        action:
                          s.scene_id === currentSceneId
                            ? ('catch_from_camera' as const)
                            : a.action,
                      }
                    : a,
                )
              : s.actors;
            const exists = s.stage_props.some((p) => p.id === created.prop_id);
            const resolvedMotion = isExplicitToss ? ('throw_arc' as const) : ('place' as const);
            const nextStageProps =
              payload.placement === 'held'
                ? s.stage_props.filter((p) => p.id !== created.prop_id)
                : exists
                  ? s.stage_props.map((p) =>
                      p.id === created.prop_id
                        ? {
                            ...p,
                            grid: propGrid,
                            on_floor: onFloor,
                            state: propState,
                            entry_motion: resolvedMotion,
                          }
                        : p,
                    )
                  : [
                      ...s.stage_props,
                      {
                        id: created.prop_id,
                        grid: propGrid,
                        on_floor: onFloor,
                        state: propState,
                        entry_motion: resolvedMotion,
                        entry_origin: isExplicitToss ? [1.0, 1.5, 0.8] : null,
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

    // Switch to 2.5D live stage to display the placed or tossed prop immediately
    setViewMode('preview');
    if (isExplicitToss) {
      window.setTimeout(() => {
        stageRef.current?.spawnThrownProp(created.prop_id, [1.0, 1.5, 0.8], propGrid);
        stageRef.current?.triggerCameraGrab();
      }, 60);
    }

    setHeardNote((prev) => ({
      heard: prev?.heard ?? null,
      saw: isExplicitToss
        ? `${created.item.name} (tossed to Lia)`
        : `${created.item.name} (placed on ${payload.placement})`,
    }));
    setStatusNote(
      created.simulated
        ? `Offline cutout fallback — ${isExplicitToss ? 'tossed' : 'placed'} "${payload.name}" (${payload.placement}) on Scene ${currentSceneId}+.`
        : isExplicitToss
          ? `Tossed "${created.item.name}" into the stage — Lia caught it!`
          : `Placed "${created.item.name}" cleanly on the ${payload.placement}.`,
    );

    // If a story prompt was waiting for this object, continue extending automatically
    const queued = pendingThrowPrompt;
    if (queued) {
      setPendingThrowPrompt(null);
      setSubmitting(true);
      submittingRef.current = true;
      await runStoryExtend({
        branchId: queued.branchId,
        fromSceneId: queued.fromSceneId,
        text: isExplicitToss
          ? `${queued.text} (The viewer explicitly tossed "${created.item.name}" to Lia to catch and interact with.)`
          : `${queued.text} (The viewer placed "${created.item.name}" on the ${payload.placement}.)`,
        clip: queued.clip,
      });
      submittingRef.current = false;
      setSubmitting(false);
    } else {
      scheduleSculptureFreeze(4500);
    }
  };

  const handleSkipThrowAndContinue = async () => {
    const queued = pendingThrowPrompt;
    setThrowOpen(false);
    setPendingThrowPrompt(null);
    if (!queued) return;
    setSubmitting(true);
    submittingRef.current = true;
    await runStoryExtend({
      branchId: queued.branchId,
      fromSceneId: queued.fromSceneId,
      text: queued.text,
      clip: queued.clip,
    });
    submittingRef.current = false;
    setSubmitting(false);
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
      label: 'Show saved example runs',
      icon: <IconLayers />,
      checked: showSavedExamples,
      onSelect: () => {
        setShowSavedExamples((v) => {
          const next = !v;
          if (next) setTreeExpanded(true);
          return next;
        });
      },
    },
    {
      label: 'Show QA & prop tray',
      icon: <IconClapper />,
      checked: showClapperboard,
      onSelect: () => setShowClapperboard((v) => !v),
    },
    {
      label: 'Sculpture freeze',
      icon: <IconEye />,
      checked: sculptureFrozen,
      hint: 'Alt+S',
      onSelect: () => setSculptureFrozen((v) => !v),
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
      hint: 'Alt+E',
      onSelect: () => setEvalModalOpen(true),
    },
    {
      label: 'Clean view',
      icon: <IconEye />,
      checked: cleanCaptureMode,
      hint: 'Alt+C',
      onSelect: () => setCleanCaptureMode((v) => !v),
    },
    {
      label: 'Reset stage',
      icon: <IconReset />,
      hint: 'Alt+R',
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

  const isRewound = activeSceneIndex < activeBranch.scenes.length - 1;

  return (
    <div className="app-shell" data-mood={activeScene.lighting}>
      <header className="topbar">
        <span className="brand" title="Live Interactive 2.5D Anime Stage Director">
          StageState
        </span>
        {apiStatus === 'live' && (
          <span className="mode-badge live" role="status" title="Models are reachable: live continuation, throw-in props, and fast renders run live">
            Live
          </span>
        )}
        {apiStatus === 'saved' && (
          <span
            className="mode-badge saved"
            role="status"
            title={
              backendReachable
                ? 'No model key configured: running in MOCK MODE with saved runs.'
                : 'Backend unreachable: running in MOCK MODE with offline fallback.'
            }
          >
            MOCK MODE · Saved runs only
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
              <div className="shortcut-title">Keyboard shortcuts (Alt-guarded)</div>
              <div className="shortcut-row"><kbd>Alt+Space</kbd><span>Play / stop scenes</span></div>
              <div className="shortcut-row"><kbd>Alt+1–9</kbd><span>Jump to scene</span></div>
              <div className="shortcut-row"><kbd>Alt+V</kbd><span>2.5D stage / film clips</span></div>
              <div className="shortcut-row"><kbd>Alt+T</kbd><span>Throw object into stage</span></div>
              <div className="shortcut-row"><kbd>Alt+S</kbd><span>Freeze / unfreeze sculpture</span></div>
              <div className="shortcut-row"><kbd>Alt+E</kbd><span>Eval benchmarks</span></div>
              <div className="shortcut-row"><kbd>Alt+C</kbd><span>Clean view</span></div>
              <div className="shortcut-row"><kbd>Alt+R</kbd><span>Reset stage</span></div>
              <div className="shortcut-row"><kbd>Esc</kbd><span>Close overlays</span></div>
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
        sculptureFrozen={sculptureFrozen}
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
        onToggleSculptureFreeze={setSculptureFrozen}
        onSelectCompareBranch={setCompareBranchId}
        onOpenSavedRun={hasSavedRun ? () => handleOpenSavedRun(activeScene.scene_id) : undefined}
        onTogglePlayStory={handleTogglePlayStory}
        onExportStart={() => setStatusNote('Capturing a 4s dual-camera preview…')}
        onExportComplete={handleExportComplete}
        onRenderScene={handleRenderScene}
        footer={
          showClapperboard ? (
            <ClapperboardQA
              scene={activeScene}
              hasOwnRender={activeOwnsRender}
              rules={rules}
              propsRegistry={activeBranch.props_registry}
              cleanCaptureMode={cleanCaptureMode}
            />
          ) : undefined
        }
      />

      <StoryBranchTree
        branches={branches}
        activeBranchId={activeBranch.branch_id}
        activeSceneIndex={activeSceneIndex}
        renderedSceneKeys={renderedSceneKeys}
        liveBranchIds={liveBranchIds}
        expanded={treeExpanded}
        onToggleExpanded={() => setTreeExpanded((v) => !v)}
        showSavedExamples={showSavedExamples}
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
          currentSceneId={activeScene.scene_id}
          isRewound={isRewound}
          submitting={submitting}
          creatingProp={creatingProp}
          throwOpen={throwOpen}
          contradictionWarning={
            pendingConflict?.warning ?? activeBranch.contradiction_warning
          }
          cleanCaptureMode={cleanCaptureMode}
          onDismissContradiction={() => setPendingConflict(null)}
          onForceRewrite={handleForceRewrite}
          onOpenThrowCamera={() => {
            setThrowHint('Hold or toss an everyday object (drink, snack, mug, laptop) into frame.');
            setSuggestedPropName(null);
            setThrowOpen((v) => !v);
          }}
          onSubmitPrompt={handleSubmitPrompt}
        />
      </div>

      <CameraSelfView
        throwOpen={throwOpen}
        throwHint={throwHint}
        suggestedPropName={suggestedPropName}
        creatingProp={creatingProp}
        promptedByStory={Boolean(pendingThrowPrompt)}
        cleanCaptureMode={cleanCaptureMode}
        onRequestOpenThrow={() => {
          setThrowHint('Hold or toss an everyday object (drink, snack, mug, laptop) into frame.');
          setSuggestedPropName(null);
          setThrowOpen(true);
        }}
        onCancelThrow={() => {
          setThrowOpen(false);
          setPendingThrowPrompt(null);
        }}
        onSkipThrowAndContinue={handleSkipThrowAndContinue}
        onSubmitThrow={handleSubmitThrow}
      />

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
