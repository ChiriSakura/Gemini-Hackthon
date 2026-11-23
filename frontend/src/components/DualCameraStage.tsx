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
import { fetchBlockingPreview, moveStageActor } from '../api/stageClient';
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
  onAdvanceStoryScene?: () => void;
  onExportStart?: () => void;
  onExportComplete?: (result: PreviewExportResult) => void;
  onRenderScene?: (scene: Scene, preview: PreviewExportResult) => void;
}

interface StageVideoProps {
  src: string;
  poster?: string;
  muted: boolean;
  loop?: boolean;
  /** When false, mounts pre-buffered underneath (`opacity: 0`) ready for 0ms seamless swap. */
  active?: boolean;
  onError: () => void;
  /** Called with the clicked element so every visible clip toggles together. */
  onToggle: (video: HTMLVideoElement) => void;
  /** Fires when non-looping clip reaches its true end (drives zero-gap story playback). */
  onEnded?: () => void;
}

/**
 * Clip without native browser controls: click toggles play/pause, a thin gold
 * line shows progress, and a centred play glyph appears only while paused.
 * Supports pre-buffered (`active=false`) mounting underneath the active clip
 * so scene transitions during continuous playback have zero black flash.
 */
const StageVideo = forwardRef<HTMLVideoElement, StageVideoProps>(function StageVideo(
  { src, poster, muted, loop = true, active = true, onError, onToggle, onEnded },
  ref,
) {
  const [paused, setPaused] = useState(false);
  const innerRef = useRef<HTMLVideoElement | null>(null);
  const barRef = useRef<HTMLSpanElement | null>(null);
  const lastRatioRef = useRef(0);

  const assignRef = (el: HTMLVideoElement | null) => {
    innerRef.current = el;
    if (typeof ref === 'function') ref(el);
    else if (ref) ref.current = el;
  };

  useEffect(() => {
    const el = innerRef.current;
    if (!el) return;
    if (active) {
      try {
        el.currentTime = 0;
        const p = el.play();
        if (p && typeof p.catch === 'function') p.catch(() => {});
      } catch {
        // Ignore playback errors from unmounted/unready videos
      }
    } else {
      el.pause();
      try {
        el.currentTime = 0;
      } catch {
        // Ignore seek before metadata ready
      }
    }
  }, [active, src]);

  const handleTimeUpdate = (e: React.SyntheticEvent<HTMLVideoElement>) => {
    if (!active) return;
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
        ref={assignRef}
        className={active ? 'cam-video-active' : 'cam-video-prebuffer'}
        src={src}
        poster={poster}
        preload="auto"
        autoPlay={active}
        loop={loop}
        muted={active ? muted : true}
        playsInline
        title={active ? 'Click to pause or play' : undefined}
        onClick={active ? (e) => onToggle(e.currentTarget) : undefined}
        onPlay={active ? () => setPaused(false) : undefined}
        onPause={active ? () => setPaused(true) : undefined}
        onTimeUpdate={active ? handleTimeUpdate : undefined}
        onEnded={active ? onEnded : undefined}
        onError={onError}
      />
      {active && paused && (
        <span className="cam-paused" aria-hidden="true">
          <IconPlay size={26} />
        </span>
      )}
      {active && (
        <span className="cam-progress" aria-hidden="true">
          <span ref={barRef} />
        </span>
      )}
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
      viewMode: appViewMode,
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
      onTogglePlayStory,
      onAdvanceStoryScene,
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
    // Every scene opens on its clip (or a "not generated yet" card); the 3D world view is one toggle away.
    const [worldView, setWorldView] = useState(false);
    void appViewMode;
    const viewMode: 'preview' | 'final' = worldView ? 'preview' : 'final';
    useEffect(() => {
      setWorldView(false);
    }, [activeSceneIndex, activeBranch.branch_id]);
    const [exporting, setExporting] = useState(false);
    const [activeCutCam, setActiveCutCam] = useState<'A' | 'B'>('A');
    const [narrationExpanded, setNarrationExpanded] = useState(false);
    const [failedVideoUrls, setFailedVideoUrls] = useState<Record<string, boolean>>({});
    const [blockingPreviews, setBlockingPreviews] = useState<
      Record<string, { camA: string; camB: string } | 'pending' | 'none'>
    >({});

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
    const prevScene = activeSceneIndex > 0 ? activeBranch.scenes[activeSceneIndex - 1] : null;
    const nextScene = activeBranch.scenes[activeSceneIndex + 1] ?? null;
    const nextSceneOwnsFinal = nextScene
      ? hasOwnFinalMedia(
          nextScene,
          findParentScene(activeBranch, allBranches, nextScene.scene_id),
        )
      : false;

    const camAUrl = sceneOwnsFinal ? (scene.media.cam_a_final_url ?? null) : null;
    const camBUrl = sceneOwnsFinal ? (scene.media.cam_b_final_url ?? null) : null;
    const nextCamAUrl = nextSceneOwnsFinal ? (nextScene?.media.cam_a_final_url ?? null) : null;
    const nextCamBUrl = nextSceneOwnsFinal ? (nextScene?.media.cam_b_final_url ?? null) : null;
    // Use the previous scene's tail frame as poster (pixel-identical to frame 0 of this scene!)
    // so transitions between consecutive scenes have zero visual jump.
    const camAPoster = sceneOwnsFinal
      ? (prevScene?.media.cam_a_tail_frame_url ?? scene.media.cam_a_tail_frame_url ?? undefined)
      : undefined;
    const camBPoster = sceneOwnsFinal
      ? (prevScene?.media.cam_b_tail_frame_url ?? scene.media.cam_b_tail_frame_url ?? undefined)
      : undefined;

    // Preload ALL remaining clips of the active branch with preload="auto" for zero-gap playback
    useEffect(() => {
      if (viewMode !== 'final' && !isPlayingStory) return;
      const upcoming = activeBranch.scenes.slice(activeSceneIndex + 1);
      if (upcoming.length === 0) return;
      const urls = upcoming
        .flatMap((s) => [s.media.cam_a_final_url, s.media.cam_b_final_url])
        .filter((u): u is string => Boolean(u));
      const preloadEls: HTMLVideoElement[] = urls.map((url) => {
        const v = document.createElement('video');
        v.preload = 'auto';
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
      !(camAUrl && failedVideoUrls[camAUrl]);
    const showFinalB =
      viewMode === 'final' &&
      Boolean(camBUrl) &&
      !(camBUrl && failedVideoUrls[camBUrl]);

    // No final clip for this scene (not rendered yet, or it cannot be rendered): show the 3D preview clip,
    // the stage going from the previous state to this one, drawn from the world state alone.
    // A saved preview clip that no longer loads (for example a fresh checkout without the file) counts as missing,
    // so it is asked for again.
    const isBlockingClip = (url?: string | null): url is string =>
      Boolean(url && /_blocking_[0-9a-f]+\.mp4$/.test(url) && !failedVideoUrls[url]);
    const previewKey = `${activeBranch.branch_id}:${scene.scene_id}`;
    const fetchedPreview = blockingPreviews[previewKey];
    const previewAUrl = isBlockingClip(scene.media.cam_a_preview_url)
      ? scene.media.cam_a_preview_url
      : typeof fetchedPreview === 'object'
        ? fetchedPreview.camA
        : undefined;
    const previewBUrl = isBlockingClip(scene.media.cam_b_preview_url)
      ? scene.media.cam_b_preview_url
      : typeof fetchedPreview === 'object'
        ? fetchedPreview.camB
        : undefined;
    const needsPreview = viewMode === 'final' && (!showFinalA || !showFinalB) && (!previewAUrl || !previewBUrl);
    useEffect(() => {
      if (!needsPreview || fetchedPreview !== undefined) return;
      let cancelled = false;
      setBlockingPreviews((prev) => ({ ...prev, [previewKey]: 'pending' }));
      void fetchBlockingPreview(activeBranch.branch_id, scene.scene_id).then((urls) => {
        if (cancelled) return;
        setBlockingPreviews((prev) => ({ ...prev, [previewKey]: urls ?? 'none' }));
      });
      return () => {
        cancelled = true;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [needsPreview, previewKey]);
    const showPreviewA =
      viewMode === 'final' && !showFinalA && Boolean(previewAUrl) && !(previewAUrl && failedVideoUrls[previewAUrl]);
    const showPreviewB =
      viewMode === 'final' && !showFinalB && Boolean(previewBUrl) && !(previewBUrl && failedVideoUrls[previewBUrl]);
    const showClipA = showFinalA || showPreviewA;
    const showClipB = showFinalB || showPreviewB;
    const pendingLabel = renderingScene
      ? 'Generating…'
      : fetchedPreview === 'pending'
        ? 'Preparing 3D preview…'
        : 'Not generated yet';
    const previewBadge = renderingScene ? '3D preview · final clip rendering…' : '3D preview';

    const camADeck: Array<{ url: string; poster?: string; active: boolean }> = [];
    if (showPreviewA && previewAUrl) {
      camADeck.push({ url: previewAUrl, active: true });
    }
    if (showFinalA && camAUrl) {
      camADeck.push({ url: camAUrl, poster: camAPoster, active: true });
      if (nextCamAUrl && nextCamAUrl !== camAUrl && !failedVideoUrls[nextCamAUrl]) {
        camADeck.push({
          url: nextCamAUrl,
          poster: scene.media.cam_a_tail_frame_url ?? undefined,
          active: false,
        });
      }
    }
    const camBDeck: Array<{ url: string; poster?: string; active: boolean }> = [];
    if (showPreviewB && previewBUrl) {
      camBDeck.push({ url: previewBUrl, active: true });
    }
    if (showFinalB && camBUrl) {
      camBDeck.push({ url: camBUrl, poster: camBPoster, active: true });
      if (nextCamBUrl && nextCamBUrl !== camBUrl && !failedVideoUrls[nextCamBUrl]) {
        camBDeck.push({
          url: nextCamBUrl,
          poster: scene.media.cam_b_tail_frame_url ?? undefined,
          active: false,
        });
      }
    }

    const totalScenes = activeBranch.scenes.length;

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
          <div className="stage-head-actions">
            {totalScenes > 1 && (
              <button
                type="button"
                className={`stage-view-toggle ${isPlayingStory ? 'active' : ''}`}
                aria-pressed={isPlayingStory}
                title={
                  isPlayingStory
                    ? 'Stop continuous playback (Space)'
                    : 'Play all scenes of this branch seamlessly from Scene 1 to the end (Space)'
                }
                onClick={() => {
                  setWorldView(false);
                  onTogglePlayStory();
                }}
              >
                {isPlayingStory
                  ? `Stop (${activeSceneIndex + 1}/${totalScenes})`
                  : 'Play story'}
              </button>
            )}
            <button
              type="button"
              className={`stage-view-toggle ${worldView ? 'active' : ''}`}
              aria-pressed={worldView}
              title={worldView ? 'Back to the clip' : 'Open the 3D world view: drag to orbit, drag a character to move it'}
              onClick={() => setWorldView((v) => !v)}
            >
              {worldView ? 'Clip' : 'World view'}
            </button>
          </div>
        </header>

        <div className="stage-cams">
          {/* Camera A stays mounted so the Three.js loop keeps running */}
          <div className={`cam ${onAir('A')}`}>
            <canvas ref={canvasARef} style={{ display: showClipA ? 'none' : 'block' }} />
            {camADeck.map((slot) => (
              <StageVideo
                key={slot.url}
                ref={slot.active ? videoARef : undefined}
                src={slot.url}
                poster={slot.poster}
                active={slot.active}
                loop={!isPlayingStory}
                muted={!soundEnabled || modalOpen}
                onEnded={slot.active && isPlayingStory ? onAdvanceStoryScene : undefined}
                onError={() => markVideoFailed(slot.url)}
                onToggle={handleToggleVideos}
              />
            ))}
            {!worldView && !showClipA && (
              <div className="cam-pending" role="status">
                <span>{pendingLabel}</span>
              </div>
            )}
            {showPreviewA && <div className="cam-preview-badge">{previewBadge}</div>}
            <div className="cam-caption">
              <span>A</span>
            </div>
          </div>

          {/* Camera B stays mounted alongside Camera A */}
          <div className={`cam ${onAir('B')}`}>
            <canvas ref={canvasBRef} style={{ display: showClipB ? 'none' : 'block' }} />
            {camBDeck.map((slot) => (
              <StageVideo
                key={slot.url}
                ref={slot.active ? videoBRef : undefined}
                src={slot.url}
                poster={slot.poster}
                active={slot.active}
                loop={!isPlayingStory}
                muted
                onError={() => markVideoFailed(slot.url)}
                onToggle={handleToggleVideos}
              />
            ))}
            {!worldView && !showClipB && (
              <div className="cam-pending" role="status">
                <span>{pendingLabel}</span>
              </div>
            )}
            {showPreviewB && <div className="cam-preview-badge">{previewBadge}</div>}
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
