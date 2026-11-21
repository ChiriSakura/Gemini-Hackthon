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
import {
  ThreeStageManager,
  type PreviewExportResult,
} from '../stage/ThreeStageManager';
import type { BranchState, Scene } from '../types/schema';
import { getBranchLabelEn, getSceneTextEn } from '../utils/displayLocale';
import { findParentScene, hasOwnFinalMedia } from '../utils/sceneMedia';
import {
  IconClapper,
  IconFilm,
  IconPlay,
  IconStage,
  IconStop,
  IconVolume,
  IconVolumeOff,
} from './Icons';

export interface DualCameraStageHandle {
  /** Capture the 4s dual-camera preview; optionally send it on to the AI render. */
  exportPreview: (triggerAiRender?: boolean) => Promise<void>;
  /** Restart every visible clip together. */
  syncReplay: () => void;
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
  onSelectCompareBranch?: (branchId: string) => void;
  onOpenSavedRun?: () => void;
  onTogglePlayStory: () => void;
  onExportStart?: () => void;
  onExportComplete?: (result: PreviewExportResult) => void;
  onRenderScene?: (scene: Scene, preview: PreviewExportResult) => void;
}

export const DualCameraStage = forwardRef<DualCameraStageHandle, DualCameraStageProps>(
  function DualCameraStage(
    {
      activeBranch,
      compareBranch,
      allBranches,
      activeSceneIndex,
      viewMode,
      compareEnabled,
      pixelArtMode,
      autoCutEnabled,
      diffHaloEnabled,
      renderingScene,
      renderProgress = null,
      isPlayingStory,
      soundEnabled,
      modalOpen = false,
      sceneRenderedLive = false,
      cleanCaptureMode = false,
      footer,
      onSoundEnabledChange,
      onViewModeChange,
      onSelectCompareBranch,
      onOpenSavedRun,
      onTogglePlayStory,
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
    const [beforeAudioOn, setBeforeAudioOn] = useState(false);
    const [narrationExpanded, setNarrationExpanded] = useState(false);
    const [failedVideoUrls, setFailedVideoUrls] = useState<Record<string, boolean>>({});
    const [renderElapsedSec, setRenderElapsedSec] = useState(0);

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

    useEffect(() => {
      if (!renderingScene) {
        setRenderElapsedSec(0);
        return;
      }
      const startedAt = Date.now();
      const timer = window.setInterval(() => {
        setRenderElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
      }, 500);
      return () => window.clearInterval(timer);
    }, [renderingScene]);

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
    const compareSceneText =
      compareBranch && compareScene
        ? getSceneTextEn(compareBranch.branch_id, compareScene)
        : null;

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
      if (!managerRef.current) return;
      managerRef.current.setDiffHaloEnabled(diffHaloEnabled);
    }, [diffHaloEnabled]);

    useEffect(() => {
      if (!managerRef.current) return;
      managerRef.current.setPixelArtMode(pixelArtMode);
    }, [pixelArtMode]);

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
    }));

    // Only treat final clips as this scene's own when they were rendered for (or
    // explicitly reused by) this branch, never when merely copied from the parent.
    const sceneOwnsFinal = hasOwnFinalMedia(
      scene,
      findParentScene(activeBranch, allBranches, scene.scene_id),
    );
    const compareOwnsFinal =
      compareBranch && compareScene
        ? hasOwnFinalMedia(
            compareScene,
            findParentScene(compareBranch, allBranches, compareScene.scene_id),
          )
        : false;

    const camAUrl = sceneOwnsFinal ? (scene.media.cam_a_final_url ?? null) : null;
    const camBUrl = sceneOwnsFinal ? (scene.media.cam_b_final_url ?? null) : null;
    const camAPoster = sceneOwnsFinal
      ? (scene.media.cam_a_tail_frame_url ?? undefined)
      : undefined;
    const camBPoster = sceneOwnsFinal
      ? (scene.media.cam_b_tail_frame_url ?? undefined)
      : undefined;
    const beforeUrl = compareOwnsFinal
      ? (compareScene?.media.cam_a_final_url ?? null)
      : null;
    const beforeStill = compareOwnsFinal
      ? (compareScene?.media.cam_a_tail_frame_url ?? null)
      : null;

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

    const notRenderedYet = !sceneOwnsFinal && !scene.media.fallback_to_preview;
    const isDegradedA = viewMode === 'final' && !showFinalA && !notRenderedYet;
    const isDegradedB = viewMode === 'final' && !showFinalB && !notRenderedYet;

    const compareOpen = compareEnabled && Boolean(compareBranch && compareScene);
    const showBeforeVideo =
      compareOpen &&
      viewMode === 'final' &&
      Boolean(beforeUrl) &&
      !(beforeUrl && failedVideoUrls[beforeUrl]);

    const clipTag = sceneRenderedLive ? 'Live' : 'Saved';
    const stateChip = (
      showFinal: boolean,
      degraded: boolean,
      idleLabel: string,
    ): React.ReactNode =>
      showFinal ? (
        <span className="chip gold">Omni Flash Render · {clipTag}</span>
      ) : degraded ? (
        <span className="chip rose">Degraded (2.5D Preview)</span>
      ) : notRenderedYet ? (
        <span className="chip">2.5D Preview · not rendered yet</span>
      ) : (
        <span className="chip">{idleLabel}</span>
      );

    const onAir = (cam: 'A' | 'B') =>
      autoCutEnabled && !compareOpen
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

          {!cleanCaptureMode && (
            <div className="stage-transport">
              <button
                type="button"
                className={`icon-btn ${isPlayingStory ? 'on' : ''}`}
                aria-label={isPlayingStory ? 'Stop playing scenes 1 to 4' : 'Play scenes 1 to 4'}
                title={isPlayingStory ? 'Stop (Space)' : 'Play scenes 1 to 4 (Space)'}
                onClick={onTogglePlayStory}
              >
                {isPlayingStory ? <IconStop /> : <IconPlay />}
              </button>
              <span className="seg" role="group" aria-label="Stage view">
                <button
                  type="button"
                  className={`icon-btn ${viewMode === 'preview' ? 'on' : ''}`}
                  aria-label="2.5D stage preview"
                  aria-pressed={viewMode === 'preview'}
                  title="2.5D stage preview (Tab)"
                  onClick={() => onViewModeChange('preview')}
                >
                  <IconStage />
                </button>
                <button
                  type="button"
                  className={`icon-btn ${viewMode === 'final' ? 'on' : ''}`}
                  aria-label="Rendered film clips"
                  aria-pressed={viewMode === 'final'}
                  title="Rendered film clips (Tab)"
                  onClick={() => onViewModeChange('final')}
                >
                  <IconFilm />
                </button>
              </span>
              <button
                type="button"
                className={`icon-btn ${soundEnabled ? 'on' : ''}`}
                aria-label={soundEnabled ? 'Mute clip sound' : 'Turn clip sound on'}
                aria-pressed={soundEnabled}
                title={soundEnabled ? 'Sound on' : 'Muted'}
                onClick={() => {
                  const next = !soundEnabled;
                  onSoundEnabledChange(next);
                  if (next) setBeforeAudioOn(false);
                }}
              >
                {soundEnabled ? <IconVolume /> : <IconVolumeOff />}
              </button>
            </div>
          )}
        </header>

        <div className="stage-cams">
          {/* Camera A stays mounted so the Three.js loop keeps running */}
          <div className={`cam ${onAir('A')}`}>
            <canvas ref={canvasARef} style={{ display: showFinalA ? 'none' : 'block' }} />
            {showFinalA && camAUrl && (
              <video
                ref={videoARef}
                key={camAUrl}
                src={camAUrl}
                poster={camAPoster}
                preload="auto"
                controls
                autoPlay
                loop
                muted={!soundEnabled || modalOpen}
                playsInline
                onError={() => markVideoFailed(camAUrl)}
              />
            )}
            <div className="cam-label">
              <span className="chip">
                {compareOpen
                  ? `After · ${getBranchLabelEn(activeBranch)}`
                  : `${autoCutEnabled && activeCutCam === 'A' ? 'ON AIR · ' : ''}CAM A · Front Wide`}
              </span>
              {stateChip(
                showFinalA,
                isDegradedA,
                pixelArtMode ? 'Pixel-Art 2.5D Preview' : '2.5D Live Preview',
              )}
            </div>
            {notRenderedYet && !cleanCaptureMode && onRenderScene && (
              <div className="cam-cta">
                <button
                  type="button"
                  className="cta"
                  disabled={exporting || renderingScene}
                  onClick={() => handleExportPreview(true)}
                  title="Render this scene on both cameras, then run the judge"
                >
                  <IconClapper size={15} />
                  {renderingScene
                    ? `Rendering ${renderElapsedSec}s`
                    : exporting
                      ? 'Capturing preview'
                      : 'Render scene'}
                </button>
              </div>
            )}
          </div>

          {/* Camera B stays mounted too; Compare only hides it */}
          <div className={`cam ${onAir('B')}`} style={{ display: compareOpen ? 'none' : undefined }}>
            <canvas ref={canvasBRef} style={{ display: showFinalB ? 'none' : 'block' }} />
            {showFinalB && camBUrl && !compareOpen && (
              <video
                ref={videoBRef}
                key={camBUrl}
                src={camBUrl}
                poster={camBPoster}
                preload="auto"
                controls
                autoPlay
                loop
                muted
                playsInline
                onError={() => markVideoFailed(camBUrl)}
              />
            )}
            <div className="cam-label">
              <span className="chip">
                {autoCutEnabled && activeCutCam === 'B' ? 'ON AIR · ' : ''}CAM B · 45° Close-Up
              </span>
              {stateChip(showFinalB, isDegradedB, 'Synced World Coordinates')}
            </div>
          </div>

          {compareOpen && compareBranch && compareScene && compareSceneText && (
            <div className="cam cam-before">
              {showBeforeVideo && beforeUrl ? (
                <video
                  ref={beforeVideoRef}
                  key={beforeUrl}
                  src={beforeUrl}
                  poster={beforeStill ?? undefined}
                  preload="auto"
                  controls
                  autoPlay
                  loop
                  muted={!beforeAudioOn || modalOpen}
                  playsInline
                  onError={() => markVideoFailed(beforeUrl)}
                />
              ) : beforeStill ? (
                <img className="cam-still" src={beforeStill} alt="" />
              ) : (
                <div className="cam-summary">
                  <strong>
                    Scene {compareScene.scene_id} · {compareSceneText.title}
                  </strong>
                  <span>{compareSceneText.narration}</span>
                  <small>
                    No clip for this branch yet · lighting {compareScene.lighting}
                    {compareScene.stage_props.length > 0
                      ? ` · ${compareScene.stage_props.map((p) => `${p.id} ${p.state}`).join(', ')}`
                      : ''}
                  </small>
                </div>
              )}
              <div className="cam-label">
                {allBranches.length > 2 && onSelectCompareBranch ? (
                  <select
                    className="chip-select"
                    value={compareBranch.branch_id}
                    onChange={(e) => onSelectCompareBranch(e.target.value)}
                    aria-label="Branch shown as Before"
                  >
                    {allBranches
                      .filter((b) => b.branch_id !== activeBranch.branch_id)
                      .map((b) => (
                        <option key={b.branch_id} value={b.branch_id}>
                          Before · {getBranchLabelEn(b)}
                        </option>
                      ))}
                  </select>
                ) : (
                  <span className="chip">Before · {getBranchLabelEn(compareBranch)}</span>
                )}
                {showBeforeVideo && (
                  <button
                    type="button"
                    className={`icon-btn ${beforeAudioOn ? 'on' : ''}`}
                    aria-label={beforeAudioOn ? 'Mute the Before clip' : 'Listen to the Before clip'}
                    aria-pressed={beforeAudioOn}
                    title={beforeAudioOn ? 'Before clip sound on' : 'Listen to the Before clip'}
                    onClick={() => {
                      const next = !beforeAudioOn;
                      setBeforeAudioOn(next);
                      if (next) onSoundEnabledChange(false);
                    }}
                  >
                    {beforeAudioOn ? <IconVolume size={15} /> : <IconVolumeOff size={15} />}
                  </button>
                )}
              </div>
            </div>
          )}

          {renderingScene && (
            <div className="stage-float" role="status" aria-live="polite">
              {renderProgress &&
                Object.values(renderProgress.scenes)
                  .sort((a, b) => a.scene_id - b.scene_id)
                  .map((sc) => (
                    <span key={sc.scene_id} className="chip">
                      Scene {sc.scene_id}: {sc.status}
                      {sc.fallback_to_preview ? ' (2.5D)' : ''}
                    </span>
                  ))}
              {renderElapsedSec >= 35 && onOpenSavedRun && (
                <>
                  <span className="chip gold">Render is taking {renderElapsedSec}s</span>
                  <button type="button" className="cta" onClick={onOpenSavedRun}>
                    Open a saved run
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        {footer}
      </section>
    );
  },
);
