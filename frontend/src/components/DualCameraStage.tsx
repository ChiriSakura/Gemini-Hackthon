/**
 * Dual-Camera 2.5D Stage (Zone 1 of the first view).
 *
 * Feature & Use Cases:
 * - Hosts Camera A (Front Wide) and Camera B (45-degree Close-Up) Three.js
 *   canvases side by side, as large as the viewport allows, under one scene
 *   title and a single line of narration (click to expand).
 * - Shows a scene's final clip only when the scene has media of its own; a
 *   scene that is not rendered yet stays on the 2.5D preview with a neutral
 *   label and a Render button. Clips are tagged "Saved" (loaded from disk) or
 *   "Live" (rendered in this session).
 * - Compare replaces Camera B with the other branch's same scene, so at most
 *   two videos are ever on screen: "Before" on the left, "After" on the right.
 * - Export, AI render and synced replay are exposed through an imperative
 *   handle so their entry points can live in the overflow menu.
 */
import React, {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { RenderProgressSnapshot } from '../api/stageClient';
import { moveStageActor } from '../api/stageClient';
import {
  ThreeStageManager,
  type PreviewExportResult,
} from '../stage/ThreeStageManager';
import type { BranchState, Scene } from '../types/schema';
import { getSceneTextEn } from '../utils/displayLocale';
import { findParentScene, hasOwnFinalMedia } from '../utils/sceneMedia';
import { IconPlay } from './Icons';

export interface DualCameraStageHandle {
  /** Capture the 4s dual-camera preview; optionally send it on to the AI render. */
  exportPreview: (triggerAiRender?: boolean) => Promise<void>;
  /** Restart every visible clip together. */
  syncReplay: () => void;
  /** Freeze/unfreeze the 2.5D stage into moonlit alabaster sculpture mode (W2-V2-03). */
  setSculptureFrozen: (frozen: boolean) => void;
  /** Launch a newly captured prop along a 3D throw-in parabolic arc into the stage. */
  spawnThrownProp: (
    propId: string,
    entryOrigin?: number[] | null,
    targetGrid?: [number, number] | null,
  ) => void;
  /** Animate Lia reaching forward toward the front camera (`catch_from_camera`). */
  triggerCameraGrab: () => void;
}

interface DualCameraStageProps {
  activeBranch: BranchState;
  compareBranch: BranchState | null;
  allBranches: BranchState[];
  activeSceneIndex: number;
  viewMode: 'preview' | 'final';
  compareEnabled: boolean;
  pixelArtMode: boolean;
  autoCutEnabled: boolean;
  diffHaloEnabled: boolean;
  sculptureFrozen?: boolean;
  renderingScene: boolean;
  renderProgress?: RenderProgressSnapshot | null;
  isPlayingStory: boolean;
  soundEnabled: boolean;
  modalOpen?: boolean;
  /** True when the active scene's clip was rendered during this session. */
  sceneRenderedLive?: boolean;
  cleanCaptureMode?: boolean;
  footer?: React.ReactNode;
  onSoundEnabledChange: (enabled: boolean) => void;
  onViewModeChange: (mode: 'preview' | 'final') => void;
  onToggleSculptureFreeze?: (frozen: boolean) => void;
  onSelectCompareBranch?: (branchId: string) => void;
  onOpenSavedRun?: () => void;
  onTogglePlayStory: () => void;
  onExportStart?: () => void;
  onExportComplete?: (result: PreviewExportResult) => void;
  onRenderScene?: (scene: Scene, preview: PreviewExportResult) => void;
}

interface StageVideoProps {
  src: string;
  poster?: string;
  muted: boolean;
  onError: () => void;
  /** Called with the clicked element so every visible clip toggles together. */
  onToggle: (video: HTMLVideoElement) => void;
}

/**
 * Clip without native browser controls: click toggles play/pause, a thin gold
 * line shows progress, and a centred play glyph appears only while paused.
 */
const StageVideo = forwardRef<HTMLVideoElement, StageVideoProps>(function StageVideo(
  { src, poster, muted, onError, onToggle },
  ref,
) {
  const [paused, setPaused] = useState(false);
  const barRef = useRef<HTMLSpanElement | null>(null);
  const lastRatioRef = useRef(0);

  const handleTimeUpdate = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    const video = e.currentTarget;
    const bar = barRef.current;
    if (!bar || !Number.isFinite(video.duration) || video.duration <= 0) return;
    const ratio = Math.min(1, video.currentTime / video.duration);
    // No easing when the clip loops back to the start
    bar.style.transition = ratio < lastRatioRef.current ? 'none' : 'transform 0.25s linear';
    bar.style.transform = `scaleX(${ratio})`;
    lastRatioRef.current = ratio;
  };

  return (
    <>
      <video
        ref={ref}
        src={src}
        poster={poster}
        preload="auto"
        autoPlay
        loop
        muted={muted}
        playsInline
        title="Click to pause or play"
        onClick={(e) => onToggle(e.currentTarget)}
        onPlay={() => setPaused(false)}
        onPause={() => setPaused(true)}
        onTimeUpdate={handleTimeUpdate}
        onError={onError}
      />
      {paused && (
        <span className="cam-paused" aria-hidden="true">
          <IconPlay size={26} />
        </span>
      )}
      <span className="cam-progress" aria-hidden="true">
        <span ref={barRef} />
      </span>
    </>
  );
});

export const DualCameraStage = forwardRef<DualCameraStageHandle, DualCameraStageProps>(
  function DualCameraStage(
    {
      activeBranch,
      compareBranch,
      allBranches,
      activeSceneIndex,
      viewMode,
      pixelArtMode,
      autoCutEnabled,
      diffHaloEnabled,
      sculptureFrozen = false,
      renderingScene,
      renderProgress = null,
      isPlayingStory,
      soundEnabled,
      modalOpen = false,
      footer,
      onExportStart,
      onExportComplete,
      onRenderScene,
    },
    ref,
  ) {
    const canvasARef = useRef<HTMLCanvasElement | null>(null);
    const canvasBRef = useRef<HTMLCanvasElement | null>(null);
    const videoARef = useRef<HTMLVideoElement | null>(null);
    const videoBRef = useRef<HTMLVideoElement | null>(null);
    const beforeVideoRef = useRef<HTMLVideoElement | null>(null);
    const managerRef = useRef<ThreeStageManager | null>(null);
    const [exporting, setExporting] = useState(false);
    const [activeCutCam, setActiveCutCam] = useState<'A' | 'B'>('A');
    const [narrationExpanded, setNarrationExpanded] = useState(false);
    const [failedVideoUrls, setFailedVideoUrls] = useState<Record<string, boolean>>({});

    const markVideoFailed = (url: string) => {
      setFailedVideoUrls((prev) => (prev[url] ? prev : { ...prev, [url]: true }));
    };

    // Reset transient video error flags whenever the active/compare branch changes
    useEffect(() => {
      setFailedVideoUrls({});
    }, [activeBranch.branch_id, compareBranch?.branch_id]);

    useEffect(() => {
      setNarrationExpanded(false);
    }, [activeBranch.branch_id, activeSceneIndex]);

    // Pause stage videos while the Eval modal is open to prevent overlapping audio
    useEffect(() => {
      const videos = [videoARef.current, videoBRef.current, beforeVideoRef.current];
      for (const vid of videos) {
        if (!vid) continue;
        if (modalOpen) {
          vid.pause();
        } else {
          const p = vid.play();
          if (p && typeof p.catch === 'function') {
            p.catch(() => {});
          }
        }
      }
    }, [modalOpen]);

    const handleSyncReplay = () => {
      const videos = [videoARef.current, videoBRef.current, beforeVideoRef.current];
      for (const vid of videos) {
        if (!vid) continue;
        try {
          vid.currentTime = 0;
          const p = vid.play();
          if (p && typeof p.catch === 'function') {
            p.catch(() => {});
          }
        } catch {
          // Ignore playback errors from unmounted/unready videos
        }
      }
    };

    // Clicking any clip pauses or resumes every visible clip together
    const handleToggleVideos = (clicked: HTMLVideoElement) => {
      const shouldPlay = clicked.paused;
      const videos = [videoARef.current, videoBRef.current, beforeVideoRef.current];
      for (const vid of videos) {
        if (!vid) continue;
        if (shouldPlay) {
          const p = vid.play();
          if (p && typeof p.catch === 'function') {
            p.catch(() => {});
          }
        } else {
          vid.pause();
        }
      }
    };

    useEffect(() => {
      if (viewMode !== 'final') return;
      const syncTimer = window.setInterval(() => {
        const master = videoARef.current;
        if (!master || master.paused || master.readyState < 2) return;
        const followers = [videoBRef.current, beforeVideoRef.current];
        for (const vid of followers) {
          if (!vid || vid.paused || vid.readyState < 2) continue;
          const duration =
            Number.isFinite(vid.duration) && vid.duration > 0 ? vid.duration : null;
          if (duration && master.currentTime >= duration) continue;
          if (Math.abs(vid.currentTime - master.currentTime) > 0.25) {
            try {
              vid.currentTime = master.currentTime;
            } catch {
              // Ignore seek errors while media buffer updates
            }
          }
        }
      }, 1500);
      return () => window.clearInterval(syncTimer);
    }, [viewMode, activeSceneIndex, activeBranch.branch_id, compareBranch?.branch_id]);

    useEffect(() => {
      if (!autoCutEnabled) return;
      const timer = window.setInterval(() => {
        setActiveCutCam((prev) => (prev === 'A' ? 'B' : 'A'));
      }, 2000);
      return () => window.clearInterval(timer);
    }, [autoCutEnabled]);

    const scene: Scene = activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];
    const compareScene: Scene | null = compareBranch
      ? (compareBranch.scenes[activeSceneIndex] ?? compareBranch.scenes[0])
      : null;

    const sceneText = getSceneTextEn(activeBranch.branch_id, scene);

    useEffect(() => {
      if (!canvasARef.current || !canvasBRef.current) return;
      const manager = new ThreeStageManager(canvasARef.current, canvasBRef.current);
      managerRef.current = manager;
      return () => {
        manager.dispose();
        managerRef.current = null;
      };
    }, []);

    useEffect(() => {
      if (!managerRef.current || !scene) return;
      managerRef.current.applyScene(scene, activeBranch.props_registry, compareScene);
    }, [scene, activeBranch.props_registry, compareScene]);

    useEffect(() => {
      const manager = managerRef.current;
      if (!manager || !scene) return;
      // Dragging a character on the 3D stage moves it in the world state, so later scenes start from the new spot.
      manager.onActorMoved = (actorId, grid) => {
        const actor = scene.actors.find((item) => item.id === actorId);
        if (actor) actor.grid = grid;
        void moveStageActor(activeBranch.branch_id, scene.scene_id, actorId, grid);
      };
      return () => {
        manager.onActorMoved = null;
      };
    }, [scene, activeBranch.branch_id]);

    useEffect(() => {
      if (!managerRef.current) return;
      managerRef.current.setDiffHaloEnabled(diffHaloEnabled);
    }, [diffHaloEnabled]);

    useEffect(() => {
      if (!managerRef.current) return;
      managerRef.current.setPixelArtMode(pixelArtMode);
    }, [pixelArtMode]);

    useEffect(() => {
      if (!managerRef.current) return;
      managerRef.current.setSculptureFrozen(sculptureFrozen);
    }, [sculptureFrozen]);

    const handleExportPreview = async (triggerAiRender = false) => {
      if (!managerRef.current || exporting || renderingScene) return;
      setExporting(true);
      onExportStart?.();
      const result = await managerRef.current.exportScenePreview(4000);
      setExporting(false);
      onExportComplete?.(result);
      if (triggerAiRender) {
        onRenderScene?.(scene, result);
      }
    };

    useImperativeHandle(ref, () => ({
      exportPreview: handleExportPreview,
      syncReplay: handleSyncReplay,
      setSculptureFrozen: (frozen: boolean) => {
        managerRef.current?.setSculptureFrozen(frozen);
      },
      spawnThrownProp: (propId, entryOrigin, targetGrid) => {
        managerRef.current?.spawnThrownPropAnimation(propId, entryOrigin, targetGrid);
      },
      triggerCameraGrab: () => {
        managerRef.current?.triggerCameraGrab('lia');
      },
    }));

    // Only treat final clips as this scene's own when they were rendered for (or
    // explicitly reused by) this branch, never when merely copied from the parent.
    const sceneOwnsFinal = hasOwnFinalMedia(
      scene,
      findParentScene(activeBranch, allBranches, scene.scene_id),
    );

    const camAUrl = sceneOwnsFinal ? (scene.media.cam_a_final_url ?? null) : null;
    const camBUrl = sceneOwnsFinal ? (scene.media.cam_b_final_url ?? null) : null;
    const camAPoster = sceneOwnsFinal
      ? (scene.media.cam_a_tail_frame_url ?? undefined)
      : undefined;
    const camBPoster = sceneOwnsFinal
      ? (scene.media.cam_b_tail_frame_url ?? undefined)
      : undefined;

    // Preload the next scene's clips during film view / Play 1-4 to avoid black flashes
    useEffect(() => {
      if (viewMode !== 'final' && !isPlayingStory) return;
      const nextScene = activeBranch.scenes[activeSceneIndex + 1];
      if (!nextScene) return;
      const urls = [
        nextScene.media.cam_a_final_url,
        nextScene.media.cam_b_final_url,
      ].filter((u): u is string => Boolean(u));
      const preloadEls: HTMLVideoElement[] = urls.map((url) => {
        const v = document.createElement('video');
        v.preload = 'metadata';
        v.muted = true;
        v.src = url;
        return v;
      });
      return () => {
        for (const v of preloadEls) {
          v.removeAttribute('src');
          v.load();
        }
      };
    }, [activeBranch.scenes, activeSceneIndex, viewMode, isPlayingStory]);

    const showFinalA =
      viewMode === 'final' &&
      Boolean(camAUrl) &&
      !scene.media.fallback_to_preview &&
      !(camAUrl && failedVideoUrls[camAUrl]);
    const showFinalB =
      viewMode === 'final' &&
      Boolean(camBUrl) &&
      !scene.media.fallback_to_preview &&
      !(camBUrl && failedVideoUrls[camBUrl]);

    const onAir = (cam: 'A' | 'B') =>
      autoCutEnabled
        ? activeCutCam === cam
          ? 'on-air'
          : 'off-air'
        : '';

    return (
      <section className="stage" aria-label="Stage">
        <header className="stage-head">
          <div className="stage-head-text">
            <h2 className="stage-title">
              Scene {scene.scene_id} · {sceneText.title}
            </h2>
            <button
              type="button"
              className={`stage-narration ${narrationExpanded ? 'expanded' : ''}`}
              aria-expanded={narrationExpanded}
              title={narrationExpanded ? 'Collapse narration' : 'Show full narration'}
              onClick={() => setNarrationExpanded((v) => !v)}
            >
              {sceneText.narration}
            </button>
          </div>
        </header>

        <div className="stage-cams">
          {/* Camera A stays mounted so the Three.js loop keeps running */}
          <div className={`cam ${onAir('A')}`}>
            <canvas ref={canvasARef} style={{ display: showFinalA ? 'none' : 'block' }} />
            {showFinalA && camAUrl && (
              <StageVideo
                ref={videoARef}
                key={camAUrl}
                src={camAUrl}
                poster={camAPoster}
                muted={!soundEnabled || modalOpen}
                onError={() => markVideoFailed(camAUrl)}
                onToggle={handleToggleVideos}
              />
            )}
            <div className="cam-caption">
              <span>A</span>
            </div>
          </div>

          {/* Camera B stays mounted alongside Camera A */}
          <div className={`cam ${onAir('B')}`}>
            <canvas ref={canvasBRef} style={{ display: showFinalB ? 'none' : 'block' }} />
            {showFinalB && camBUrl && (
              <StageVideo
                ref={videoBRef}
                key={camBUrl}
                src={camBUrl}
                poster={camBPoster}
                muted
                onError={() => markVideoFailed(camBUrl)}
                onToggle={handleToggleVideos}
              />
            )}
            <div className="cam-caption">
              <span>B</span>
            </div>
          </div>

          {renderingScene && renderProgress && Object.keys(renderProgress.scenes).length > 0 && (
            <div className="stage-float" role="status" aria-live="polite">
              {Object.values(renderProgress.scenes)
                .sort((a, b) => a.scene_id - b.scene_id)
                .map((sc) => (
                  <span key={sc.scene_id} className="chip">
                    Scene {sc.scene_id}: {sc.status}
                  </span>
                ))}
            </div>
          )}
        </div>

        {footer}
      </section>
    );
  },
);
