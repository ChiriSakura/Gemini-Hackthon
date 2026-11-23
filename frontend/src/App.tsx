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
  exportBranchMovie,
  extendDirectorStory,
  fetchBranches,
  fetchHealth,
  perceiveDirectorInput,
  requestSceneRender,
  submitDirectorFork,
  subscribeRenderProgress,
  type InjectedPropPayload,
  type RenderProgressSnapshot,
} from './api/stageClient';
import {
  CameraSelfView,
  type ThrowObjectSubmission,
} from './components/CameraSelfView';
import { DirectControlBar } from './components/DirectControlBar';
import {
  DualCameraStage,
  type DualCameraStageHandle,
} from './components/DualCameraStage';
import { EvalModal } from './components/EvalModal';
import { ModelsPanel } from './components/ModelsPanel';
import { TimelinePanel } from './components/TimelinePanel';
import { StoryBranchTree } from './components/StoryBranchTree';
import { INITIAL_MOCK_BRANCHES } from './data/mockBranches';
import type { PreviewExportResult } from './stage/ThreeStageManager';
import type { BranchState, DirectorInputParsed, Scene } from './types/schema';
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
const LIVE_STAGE_LABELS: Record<string, string> = {
  queued: 'queued',
  rendering: 'rendering',
  rendering_split_screen: 'Dual-Cam split-screen',
  rendering_cam_a: 'Cam A',
  deriving_cam_b_keyframes: 'Cam B keyframes (+45°)',
  rendering_cam_b: 'Cam B',
  cross_cam_qa: 'Cross-Cam QA',
  content_blocked_retrying: 'safety filter retry',
};

function WaitingBanner({
  label,
  typical,
  since,
  liveStage,
}: {
  label: string;
  typical: string;
  since: number;
  liveStage?: string | null;
}): React.ReactElement {
  const [elapsedSec, setElapsedSec] = useState(0);
  useEffect(() => {
    setElapsedSec(Math.floor((Date.now() - since) / 1000));
    const timer = window.setInterval(() => {
      setElapsedSec(Math.floor((Date.now() - since) / 1000));
    }, 500);
    return () => window.clearInterval(timer);
  }, [since]);
  const niceStage = liveStage ? (LIVE_STAGE_LABELS[liveStage] ?? liveStage) : null;
  return (
    <div className="notice wait" role="status">
      <span>
        {label}
        {niceStage ? ` · ${niceStage}` : ''} — waiting for backend… {elapsedSec}s ({typical})
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
  const [canRetryRender, setCanRetryRender] = useState<boolean>(false);
  const [canRetryExtend, setCanRetryExtend] = useState<boolean>(false);
  const lastExtendOptsRef = useRef<{
    branchId: string;
    fromSceneId: number;
    text?: string;
    clip?: Blob | null;
    parsedDirective?: DirectorInputParsed | null;
    injectedProp?: InjectedPropPayload | null;
  } | null>(null);
  const [liveBranchIds, setLiveBranchIds] = useState<Set<string>>(() => new Set());
  const [sculptureFrozen, setSculptureFrozen] = useState(false);
  const [throwOpen, setThrowOpen] = useState(false);
  const [throwHint, setThrowHint] = useState<string | null>(null);
  const [suggestedPropName, setSuggestedPropName] = useState<string | null>(null);
  const [pendingThrowPrompt, setPendingThrowPrompt] = useState<PendingThrowPrompt | null>(null);
  const stageRef = useRef<DualCameraStageHandle | null>(null);
  const freezeTimerRef = useRef<number | null>(null);
  const optimisticUrlsRef = useRef<Set<string>>(new Set());
  const resetDemoRef = useRef<() => void>(() => {});
  const handleExportBranchRef = useRef<() => Promise<void>>(async () => {});
  const [renderedSceneKeys, setRenderedSceneKeys] = useState<Set<string>>(
    () => new Set(),
  );
  const [soundEnabled, setSoundEnabled] = useState(false);
  // `?branch=<id>` opens a saved branch directly (and `&scene=<n>` one of its scenes); the default is the baseline.
  const [activeBranchId, setActiveBranchId] = useState<string>(() => {
    if (typeof window === 'undefined') return 'branch_v1';
    return new URLSearchParams(window.location.search).get('branch') || 'branch_v1';
  });
  const [compareBranchId, setCompareBranchId] = useState<string | null>(null);
  const [activeSceneIndex, setActiveSceneIndex] = useState<number>(() => {
    if (typeof window === 'undefined') return 0;
    const wanted = Number(new URLSearchParams(window.location.search).get('scene'));
    return Number.isInteger(wanted) && wanted >= 1 ? wanted - 1 : 0;
  });
  const [viewMode, setViewMode] = useState<'preview' | 'final'>('preview');
  const [cleanCaptureMode, setCleanCaptureMode] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false;
    return new URLSearchParams(window.location.search).get('clean') === '1';
  });
  const [evalModalOpen, setEvalModalOpen] = useState<boolean>(false);
  const [modelsOpen, setModelsOpen] = useState<boolean>(false);
  const [timelinesOpen, setTimelinesOpen] = useState<boolean>(false);
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

  const loadBackendState = async () => {
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
  };

  useEffect(() => {
    void loadBackendState();
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
    return subscribeRenderProgress(activeBranch.branch_id, (snap) => {
      setRenderProgress(snap);
    });
  }, [renderingScene, isMockMode, activeBranch.branch_id]);

  const activeScene =
    activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];

  // Advance to the next scene smoothly when a clip finishes during continuous Play Story mode
  const handleAdvanceStoryScene = () => {
    setActiveSceneIndex((prev) => {
      const totalScenes = activeBranch.scenes.length;
      if (prev + 1 >= totalScenes) {
        setIsPlayingStory(false);
        return prev;
      }
      const next = prev + 1;
      setTargetSceneId(activeBranch.scenes[next]?.scene_id ?? next + 1);
      return next;
    });
  };

  // Fallback timer ONLY if the current scene has no rendered final video clip during Play Story
  useEffect(() => {
    if (!isPlayingStory) return;
    const current = activeBranch.scenes[activeSceneIndex];
    const hasClip = Boolean(current?.media?.cam_a_final_url);
    if (hasClip) return;
    const totalScenes = activeBranch.scenes.length;
    if (totalScenes <= 1) {
      setIsPlayingStory(false);
      return;
    }
    const timer = window.setTimeout(() => {
      handleAdvanceStoryScene();
    }, 4000);
    return () => window.clearTimeout(timer);
  }, [isPlayingStory, activeSceneIndex, activeBranch.branch_id, activeBranch.scenes]);

  const handleTogglePlayStory = () => {
    setIsPlayingStory((playing) => {
      if (!playing) {
        setViewMode('final');
        setActiveSceneIndex(0);
        setTargetSceneId(1);
        window.requestAnimationFrame(() => stageRef.current?.syncReplay());
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
        }
        return;
      }
      if (e.key === 'Escape') {
        setEvalModalOpen(false);
        setThrowOpen(false);
        return;
      }
      if (!e.altKey || e.metaKey || e.ctrlKey) {
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
      } else if (key === 'x') {
        e.preventDefault();
        void handleExportBranchRef.current();
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
    const urls = optimisticUrlsRef.current;
    return () => {
      if (freezeTimerRef.current !== null) {
        window.clearTimeout(freezeTimerRef.current);
      }
      for (const u of urls) {
        URL.revokeObjectURL(u);
      }
      urls.clear();
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
    setEvalModalOpen(false);
    setSculptureFrozen(false);
    setThrowOpen(false);
    setPendingThrowPrompt(null);
    setPendingConflict(null);
    setRenderProgress(null);
    setStatusNote(null);
    setErrorNote(null);
    setErrorSceneId(null);
    setCanRetryRender(false);
    setCanRetryExtend(false);
    lastExtendOptsRef.current = null;
    setHeardNote(null);
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

  handleExportBranchRef.current = async () => {
    if (busy || isMockMode) {
      setStatusNote(
        isMockMode
          ? 'Branch film export requires the backend server.'
          : 'Wait for the active task to finish before exporting.',
      );
      return;
    }
    setBusy({
      label: `Exporting stitched film for ${getBranchLabelEn(activeBranch)}`,
      typical: 'usually 2–6s',
      since: Date.now(),
    });
    const { result, error } = await exportBranchMovie(activeBranch.branch_id, 'dual_cam');
    setBusy(null);
    if (!result) {
      setErrorNote(`Export failed: ${error ?? 'unknown error'}`);
      return;
    }
    setStatusNote(
      `Exported ${result.scene_count} scenes (${result.duration_est_sec}s H.264) — download started.`,
    );
    const link = document.createElement('a');
    link.href = result.video_url;
    link.download = `${activeBranch.branch_id}_dual_cam.mp4`;
    document.body.appendChild(link);
    link.click();
    link.remove();
  };

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
    parsedDirective?: DirectorInputParsed | null;
    injectedProp?: InjectedPropPayload | null;
  }): Promise<boolean> => {
    lastExtendOptsRef.current = opts;
    setErrorNote(null);
    setCanRetryExtend(false);
    setCanRetryRender(false);
    setBusy({
      label: `Continuing story into Scene ${opts.fromSceneId + 1}`,
      typical: 'usually 10–20s',
      since: Date.now(),
    });
    // Skip re-uploading video clip when /perceive already confirmed there is no physical prop to cut out.
    const clipToForward =
      opts.parsedDirective && !opts.parsedDirective.has_physical_prop ? null : opts.clip;
    const { branch: extended, error: extendError } = await extendDirectorStory({
      branchId: opts.branchId,
      fromSceneId: opts.fromSceneId,
      text: opts.text,
      clip: clipToForward,
      parsedDirective: opts.parsedDirective,
      injectedProp: opts.injectedProp,
      endFreezeMode: 'sculpture',
      detectThrowPeak: true,
    });
    setBusy(null);

    if (!extended) {
      setErrorNote(`Director failed: ${extendError ?? 'unknown error'}`);
      setErrorSceneId(opts.fromSceneId);
      setCanRetryRender(false);
      setCanRetryExtend(true);
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

    const existingCustomProps = Object.entries(activeBranch.props_registry).filter(
      ([id, item]) =>
        item.origin === 'camera_physical' ||
        item.origin === 'text_generated' ||
        !['red_umbrella', 'crystal_vase', 'wooden_chair', 'star_token'].includes(id),
    );
    const rawPromptText = (parsed?.instruction || opts.text || '').trim();
    const referencesExistingProp =
      existingCustomProps.length > 0 &&
      (/\b(we gave|i gave|gave her|the drink|the beverage|beverge|drink it|eat it|use it|hold it|pick up|that drink|that snack|that phone)\b/i.test(
        rawPromptText,
      ) ||
        existingCustomProps.some(([id, item]) => {
          const words = `${id.replace(/_/g, ' ')} ${item.name} ${item.appearance_summary ?? ''}`
            .toLowerCase()
            .split(/\s+/)
            .filter((w) => w.length >= 4);
          return words.some((w) => rawPromptText.toLowerCase().includes(w));
        }));

    if (parsed) {
      setHeardNote({
        heard: parsed.instruction,
        saw: parsed.prop_name ?? (parsed.gesture_intent !== 'none' ? parsed.gesture_intent : null),
      });
      if (parsed.needs_prop_prompt && !referencesExistingProp) {
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
            'Hold or toss an everyday object (drink, snack, mug, laptop) into your camera!',
        );
        setSuggestedPropName(parsed.prop_name ?? null);
        setThrowOpen(true);
        setStatusNote('Story wants an object from you — capture one in the bottom-right camera!');
        return true;
      }
    }

    // Stage 2: Unbounded continuation via `/api/director/extend` (reuse Stage-1 parsedDirective to save 1 LLM round-trip)
    let enrichedText = rawPromptText;
    if (referencesExistingProp && existingCustomProps.length > 0) {
      const [pid, pItem] = existingCustomProps[existingCustomProps.length - 1];
      const pDesc = pItem.appearance_summary || pItem.name;
      enrichedText = `${rawPromptText} (ACTION REQUIREMENT: Use the already-present stage prop "${pItem.name}" [${pDesc}, id=${pid}]. Lia MUST put aside her umbrella, pick up "${pItem.name}", and visibly perform the requested action!)`;
    }
    const effectiveParsed = parsed
      ? {
          ...parsed,
          instruction: enrichedText || parsed.instruction,
          needs_prop_prompt: referencesExistingProp ? false : parsed.needs_prop_prompt,
        }
      : null;
    const ok = await runStoryExtend({
      branchId: anchorBranchId,
      fromSceneId: anchorSceneId,
      text: enrichedText || undefined,
      clip: opts.clip,
      parsedDirective: effectiveParsed,
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
    setCanRetryRender(false);
    setRenderingScene(true);
    setStatusNote(null);
    setBusy({
      label: fastLiveMode
        ? `Fast-rendering Scene ${scene.scene_id}`
        : `Rendering Scene ${scene.scene_id} (two cameras + QA)`,
      typical: fastLiveMode ? 'usually 10–20s' : 'usually 40–60s',
      since: Date.now(),
    });

    const activeBranchObj = branches.find((b) => b.branch_id === branchId);
    const prevAnchorScene =
      scene.parent_scene_id != null
        ? activeBranchObj?.scenes.find((s) => s.scene_id === scene.parent_scene_id)
        : undefined;
    const prevTailCamA = prevAnchorScene?.media.cam_a_tail_frame_url ?? null;
    const prevTailCamB = prevAnchorScene?.media.cam_b_tail_frame_url ?? null;

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
        prevTailFrameUrl: prevTailCamA,
        prevTailCamBUrl: prevTailCamB,
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
      setCanRetryRender(true);
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
    const isExplicitToss = payload.entryMotion === 'throw_arc';
    const onFloor = payload.placement === 'floor';
    const propState: 'held' | 'glowing' = payload.placement === 'held' ? 'held' : 'glowing';
    const propGrid: [number, number] =
      payload.placement === 'table'
        ? [1.08, 1.08]
        : payload.placement === 'floor'
          ? [1.15, 1.48]
          : [0.92, 1.15];

    setThrowOpen(false);
    setErrorNote(null);
    setCanRetryRender(false);
    setCreatingProp(true);
    setSculptureFrozen(false);

    // W2-V3-04: Instant (<100ms) optimistic captured-object preview on the 2.5D stage
    const optimisticBlob =
      payload.previewThumbBlob ??
      (payload.mediaBlob && !payload.mediaBlob.type.includes('video')
        ? payload.mediaBlob
        : null);
    let tempUrl: string | null = null;
    const tempPropId = optimisticBlob ? `__optimistic_prop_${Date.now()}` : null;
    if (optimisticBlob && tempPropId) {
      tempUrl = URL.createObjectURL(optimisticBlob);
      optimisticUrlsRef.current.add(tempUrl);
      const previewUrl = tempUrl;
      setViewMode('preview');
      setBranches((prev) =>
        prev.map((b) => {
          if (b.branch_id !== targetBranchId) return b;
          return {
            ...b,
            props_registry: {
              ...b.props_registry,
              [tempPropId]: {
                name: `${payload.name} (Stylizing…)`,
                image_url: previewUrl,
                state: propState,
                origin: 'camera_physical',
              },
            },
            scenes: b.scenes.map((s) =>
              s.scene_id !== currentSceneId
                ? s
                : {
                    ...s,
                    stage_props: [
                      ...s.stage_props,
                      {
                        id: tempPropId,
                        grid: propGrid,
                        on_floor: onFloor,
                        state: 'glowing' as const,
                        entry_motion: isExplicitToss ? ('throw_arc' as const) : ('place' as const),
                      },
                    ],
                  },
            ),
          };
        }),
      );
      setStatusNote(`Stylizing "${payload.name}"… (preview placed on 2.5D stage)`);
    } else {
      setStatusNote(null);
    }

    setBusy({
      label: `Stylizing "${payload.name}" into a 2D anime stage prop`,
      typical: 'usually <2s fast-match or 10–18s cutout',
      since: Date.now(),
    });
    const propStartedAt = Date.now();
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
    const propElapsedMs = Date.now() - propStartedAt;
    setCreatingProp(false);
    setBusy(null);
    if (created.simulated) {
      void refreshHealth(isMockMode);
    }

    // Revoke temporary optimistic preview URL once real transparent PNG is ready
    if (tempUrl) {
      URL.revokeObjectURL(tempUrl);
      optimisticUrlsRef.current.delete(tempUrl);
    }

    const appearanceSummary = (
      created.appearance_summary ??
      created.item.appearance_summary ??
      ''
    ).trim();

    setBranches((prev) =>
      prev.map((b) => {
        if (b.branch_id !== targetBranchId) return b;
        const cleanedRegistry = { ...b.props_registry };
        if (tempPropId) delete cleanedRegistry[tempPropId];
        cleanedRegistry[created.prop_id] = {
          ...created.item,
          state: propState,
          appearance_summary: appearanceSummary || created.item.appearance_summary,
        };
        return {
          ...b,
          props_registry: cleanedRegistry,
          scenes: b.scenes.map((s) => {
            const baseProps = tempPropId
              ? s.stage_props.filter((p) => p.id !== tempPropId)
              : s.stage_props;
            if (s.scene_id < currentSceneId) {
              return tempPropId ? { ...s, stage_props: baseProps } : s;
            }
            const nextActors =
              isExplicitToss || payload.placement === 'held'
                ? s.actors.map((a) =>
                    a.id === 'lia'
                      ? {
                          ...a,
                          holding_prop:
                            payload.placement === 'held' ? created.prop_id : a.holding_prop,
                          action:
                            isExplicitToss && s.scene_id === currentSceneId
                              ? ('catch_from_camera' as const)
                              : a.action,
                        }
                      : a,
                  )
                : s.actors;
            const exists = baseProps.some((p) => p.id === created.prop_id);
            const resolvedMotion = isExplicitToss ? ('throw_arc' as const) : ('place' as const);
            const nextStageProps =
              payload.placement === 'held'
                ? baseProps.filter((p) => p.id !== created.prop_id)
                : exists
                  ? baseProps.map((p) =>
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
                      ...baseProps,
                      {
                        id: created.prop_id,
                        grid: propGrid,
                        on_floor: onFloor,
                        state: propState,
                        entry_motion: resolvedMotion,
                        entry_origin: isExplicitToss ? [1.0, 1.5, 0.8] : null,
                      },
                    ];
            // W2-V3-02: Upsert `<prop_id>: <appearance_summary>` via `|` delimiter without bloating multi-turn notes
            let nextEnvNotes = s.environment_notes || '';
            if (appearanceSummary) {
              const filteredParts = nextEnvNotes
                .split(/\s*\|\s*/)
                .map((p) => p.trim())
                .filter((p) => p.length > 0 && !p.startsWith(`${created.prop_id}:`));
              filteredParts.push(`${created.prop_id}: ${appearanceSummary}`);
              nextEnvNotes = filteredParts.join(' | ');
            }
            return {
              ...s,
              actors: nextActors,
              stage_props: nextStageProps,
              environment_notes: nextEnvNotes,
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

    const matchedLabel = appearanceSummary || created.item.name;
    const isFastMatch = !created.simulated && propElapsedMs < 1500;
    setHeardNote((prev) => ({
      heard: prev?.heard ?? null,
      saw: isExplicitToss
        ? `${matchedLabel} (tossed to Lia)`
        : `${matchedLabel} (placed on ${payload.placement})`,
    }));
    setStatusNote(
      created.simulated
        ? `Offline cutout fallback — ${isExplicitToss ? 'tossed' : 'placed'} "${payload.name}" (${payload.placement}) on Scene ${currentSceneId}+.`
        : isFastMatch
          ? `Fast matched: ${matchedLabel} (${(propElapsedMs / 1000).toFixed(2)}s)`
          : `Captured prop matched: ${matchedLabel} (${isExplicitToss ? 'tossed to Lia' : `placed on ${payload.placement}`}).`,
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
          ? `${queued.text} (ACTION REQUIREMENT: The viewer explicitly tossed "${created.item.name}" [${matchedLabel}, id=${created.prop_id}] to Lia — Lia catches it, puts aside her umbrella if needed, and visibly performs: "${queued.text}".)`
          : `${queued.text} (ACTION REQUIREMENT: The viewer placed "${created.item.name}" [${matchedLabel}, id=${created.prop_id}] on the ${payload.placement} — Lia puts aside her umbrella if needed, interacts with "${created.item.name}", and visibly performs: "${queued.text}".)`,
        clip: queued.clip,
        injectedProp: {
          prop_id: created.prop_id,
          item: created.item,
          placement: payload.placement,
          entry_motion: isExplicitToss
            ? 'throw_arc'
            : payload.placement === 'held'
              ? 'held'
              : 'place',
        },
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

  const hasSavedRun = branches.some(
    (b) => !liveBranchIds.has(b.branch_id) && b.branch_id !== activeBranch.branch_id,
  );

  return (
    <div className="app-shell" data-mood={activeScene.lighting}>
      <header className="topbar">
        <span className="brand" title="StageGenesis">
          StageGenesis
        </span>
        <button type="button" className="topbar-tab" onClick={() => setModelsOpen(true)}>
          Models &amp; Evaluation
        </button>
        <button type="button" className="topbar-tab topbar-tab-next" onClick={() => setTimelinesOpen(true)}>
          Timelines
        </button>
        {apiStatus === 'saved' && (
          <span
            className="mode-badge saved"
            role="status"
            title={
              backendReachable
                ? 'No model key configured: browsing saved runs.'
                : 'Backend unreachable: browsing saved runs.'
            }
          >
            Saved
          </span>
        )}
        {apiStatus === 'live' && (
          <span className="mode-badge live" role="status" title="Connected to live backend models.">
            Live
          </span>
        )}
      </header>

      <DualCameraStage
        ref={stageRef}
        activeBranch={activeBranch}
        compareBranch={compareBranch}
        allBranches={branches}
        activeSceneIndex={activeSceneIndex}
        viewMode={viewMode}
        compareEnabled={false}
        pixelArtMode={false}
        autoCutEnabled={false}
        diffHaloEnabled={true}
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
        onAdvanceStoryScene={handleAdvanceStoryScene}
        onExportStart={() => setStatusNote('Capturing a 4s dual-camera preview…')}
        onExportComplete={handleExportComplete}
        onRenderScene={handleRenderScene}
      />

      <StoryBranchTree
        branches={branches}
        activeBranchId={activeBranch.branch_id}
        activeSceneIndex={activeSceneIndex}
        renderedSceneKeys={renderedSceneKeys}
        liveBranchIds={liveBranchIds}
        showSavedExamples={false}
        onSelectScene={handleSelectScene}
      />

      <div className="dock">
        {errorNote && (
          <div className="notice error" role="alert">
            <span>
              <strong>{errorNote}</strong>
            </span>
            {canRetryExtend && !busy && lastExtendOptsRef.current && (
              <button
                type="button"
                className="notice-btn strong"
                onClick={() => {
                  const opts = lastExtendOptsRef.current;
                  if (opts) void runStoryExtend(opts);
                }}
              >
                Retry
              </button>
            )}
            {canRetryRender && !renderingScene && (
              <button
                type="button"
                className="notice-btn strong"
                onClick={() =>
                  void handleRenderSceneWithMode(
                    activeBranch.branch_id,
                    activeScene,
                    null,
                    true,
                  )
                }
              >
                Retry Render
              </button>
            )}
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
                setCanRetryRender(false);
                setCanRetryExtend(false);
              }}
            >
              Dismiss
            </button>
          </div>
        )}

        {busy && (
          <WaitingBanner
            label={busy.label}
            typical={busy.typical}
            since={busy.since}
            liveStage={
              renderingScene
                ? renderProgress?.scenes?.[String(activeScene.scene_id)]?.status ?? null
                : null
            }
          />
        )}

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
          submitting={submitting}
          contradictionWarning={
            pendingConflict?.warning ?? activeBranch.contradiction_warning
          }
          onDismissContradiction={() => setPendingConflict(null)}
          onForceRewrite={handleForceRewrite}
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

      <ModelsPanel open={modelsOpen} onClose={() => setModelsOpen(false)} />
      <TimelinePanel
        open={timelinesOpen}
        branches={branches}
        activeBranchId={activeBranch.branch_id}
        onClose={() => setTimelinesOpen(false)}
        onGrafted={(grafted) => {
          setBranches((prev) => [...prev.filter((b) => b.branch_id !== grafted.branch_id), grafted]);
          setLiveBranchIds((prev) => new Set(prev).add(grafted.branch_id));
          setActiveBranchId(grafted.branch_id);
          setActiveSceneIndex(0);
          setTimelinesOpen(false);
        }}
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
