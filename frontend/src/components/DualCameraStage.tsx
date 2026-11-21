/**
 * Dual-Camera 2.5D Stage, Auto-Sequence Playback & Branch Comparison View.
 *
 * Feature & Use Cases:
 * - Hosts Camera A (Front Wide) and Camera B (45° Close-Up) Three.js canvases.
 * - Supports 2.5D real-time preview vs. Omni Flash AI Render toggle with
 *   explicit `Degraded (2.5D Preview)` badge and native audio mute/unmute toggle.
 * - Includes `▶ Play 1→4` auto-sequence playback (4s per scene), `Auto-Cut A/B`
 *   smart camera focus, multimodal signal pills (`vocal_emotion`, `gesture_intent`,
 *   `environment_notes`), and side-by-side Old vs. New Branch comparison.
 */
import React, { useEffect, useRef, useState } from 'react';
import type { RenderProgressSnapshot } from '../api/stageClient';
import {
  ThreeStageManager,
  type PreviewExportResult,
} from '../stage/ThreeStageManager';
import type { BranchState, Scene } from '../types/schema';
import { getBranchLabelEn, getSceneTextEn } from '../utils/displayLocale';

interface DualCameraStageProps {
  activeBranch: BranchState;
  compareBranch: BranchState | null;
  allBranches: BranchState[];
  activeSceneIndex: number;
  viewMode: 'preview' | 'final';
  compareEnabled: boolean;
  pixelArtMode: boolean;
  renderingScene: boolean;
  renderProgress?: RenderProgressSnapshot | null;
  isPlayingStory: boolean;
  cleanCaptureMode?: boolean;
  onSelectCompareBranch?: (branchId: string) => void;
  onSwitchToCachedSafeBranch?: () => void;
  onTogglePlayStory: () => void;
  onExportComplete?: (result: PreviewExportResult) => void;
  onRenderScene?: (scene: Scene, preview: PreviewExportResult) => void;
}

export const DualCameraStage: React.FC<DualCameraStageProps> = ({
  activeBranch,
  compareBranch,
  allBranches,
  activeSceneIndex,
  viewMode,
  compareEnabled,
  pixelArtMode,
  renderingScene,
  renderProgress = null,
  isPlayingStory,
  cleanCaptureMode = false,
  onSelectCompareBranch,
  onSwitchToCachedSafeBranch,
  onTogglePlayStory,
  onExportComplete,
  onRenderScene,
}) => {
  const canvasARef = useRef<HTMLCanvasElement | null>(null);
  const canvasBRef = useRef<HTMLCanvasElement | null>(null);
  const videoARef = useRef<HTMLVideoElement | null>(null);
  const videoBRef = useRef<HTMLVideoElement | null>(null);
  const compareOldVideoRef = useRef<HTMLVideoElement | null>(null);
  const compareNewVideoRef = useRef<HTMLVideoElement | null>(null);
  const managerRef = useRef<ThreeStageManager | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [autoCutEnabled, setAutoCutEnabled] = useState(false);
  const [activeCutCam, setActiveCutCam] = useState<'A' | 'B'>('A');
  const [soundEnabled, setSoundEnabled] = useState(false);
  const [compareAudioSource, setCompareAudioSource] = useState<'none' | 'baseline' | 'active'>('none');
  const [diffHaloEnabled, setDiffHaloEnabled] = useState(true);
  const [failedVideoUrls, setFailedVideoUrls] = useState<Record<string, boolean>>({});
  const [renderElapsedSec, setRenderElapsedSec] = useState(0);

  const markVideoFailed = (url: string) => {
    setFailedVideoUrls((prev) => (prev[url] ? prev : { ...prev, [url]: true }));
  };

  // W2-T6D: Reset transient video error flags cleanly whenever active/compare branch changes
  useEffect(() => {
    setFailedVideoUrls({});
  }, [activeBranch.branch_id, compareBranch?.branch_id]);

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
    const videos = [
      videoARef.current,
      videoBRef.current,
      compareOldVideoRef.current,
      compareNewVideoRef.current,
    ];
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
    if (!autoCutEnabled) return;
    const timer = window.setInterval(() => {
      setActiveCutCam((prev) => (prev === 'A' ? 'B' : 'A'));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [autoCutEnabled]);

  const scene: Scene =
    activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];
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

  // Keep compare audio and main Cam A audio mutually exclusive to avoid overlap
  const handleSelectCompareAudio = (target: 'baseline' | 'active') => {
    setCompareAudioSource((prev) => {
      const next = prev === target ? 'none' : target;
      if (next !== 'none') setSoundEnabled(false);
      return next;
    });
  };

  useEffect(() => {
    if (!managerRef.current) return;
    managerRef.current.setPixelArtMode(pixelArtMode);
  }, [pixelArtMode]);

  const handleExportPreview = async (triggerAiRender = false) => {
    if (!managerRef.current || exporting || renderingScene) return;
    setExporting(true);
    setExportStatus('Capturing 4s dual-camera .webm preview...');
    const result = await managerRef.current.exportScenePreview(4000);
    setExporting(false);
    if (result.mode === 'webm') {
      const kbA = Math.round((result.camABlob?.size ?? 0) / 1024);
      const kbB = Math.round((result.camBBlob?.size ?? 0) / 1024);
      setExportStatus(
        `Export ready (.webm): Cam A ${kbA}KB / Cam B ${kbB}KB (${result.elapsedMs}ms)`,
      );
    } else {
      setExportStatus(
        `Switched to start+end PNG keyframe fallback (${result.elapsedMs}ms)`,
      );
    }
    onExportComplete?.(result);
    if (triggerAiRender) {
      onRenderScene?.(scene, result);
    }
  };

  const camAUrl = scene?.media.cam_a_final_url ?? null;
  const camBUrl = scene?.media.cam_b_final_url ?? null;
  const camAPoster = scene?.media.cam_a_tail_frame_url ?? undefined;
  const camBPoster = scene?.media.cam_b_tail_frame_url ?? undefined;
  const compareCamAUrl = compareScene?.media.cam_a_final_url ?? null;
  const compareCamAPoster = compareScene?.media.cam_a_tail_frame_url ?? undefined;

  // W2-T6E: Preload adjacent scene MP4 metadata during AI Render / Play 1->4 to avoid black transition flashes
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
    !scene?.media.fallback_to_preview &&
    !(camAUrl && failedVideoUrls[camAUrl]);
  const showFinalB =
    viewMode === 'final' &&
    Boolean(camBUrl) &&
    !scene?.media.fallback_to_preview &&
    !(camBUrl && failedVideoUrls[camBUrl]);

  const isDegradedA = viewMode === 'final' && !showFinalA;
  const isDegradedB = viewMode === 'final' && !showFinalB;
  const hasAnyLiveVideo = showFinalA || showFinalB;

  const showCompareOldVideo =
    viewMode === 'final' &&
    Boolean(compareCamAUrl) &&
    !(compareCamAUrl && failedVideoUrls[compareCamAUrl]);
  const showCompareNewVideo =
    viewMode === 'final' &&
    Boolean(camAUrl) &&
    !(camAUrl && failedVideoUrls[camAUrl]);

  const shortEnvNotes =
    (scene.environment_notes || 'clean_stage').length > 44
      ? `${scene.environment_notes.slice(0, 44)}…`
      : scene.environment_notes || 'clean_stage';

  return (
    <section className="stage-theater">
      <div className="stage-header-row">
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <strong style={{ fontSize: '1.02rem' }}>
            Scene {scene.scene_id} · {sceneText.title}
          </strong>
          <span className="cue-pill" title={`Lighting: ${scene.lighting}`}>
            💡 {scene.lighting}
          </span>
          <span className="cue-pill" title="Raw Audio Vocal Emotion">
            🎙️ {scene.vocal_emotion}
          </span>
          <span className="cue-pill" title="Detected Director Gesture Intent">
            🤲 {scene.gesture_intent}
          </span>
          <span
            className="cue-pill env"
            title={`Tail-frame observation: ${scene.environment_notes || 'clean_stage'}`}
          >
            👁️ {shortEnvNotes}
          </span>
        </div>

        <div className="toolbar-actions">
          <button
            type="button"
            className={`pill-btn ${isPlayingStory ? 'active' : ''}`}
            onClick={onTogglePlayStory}
          >
            {isPlayingStory ? '⏹ Stop 1→4' : '▶ Play 1→4'}
          </button>
          <button
            type="button"
            className={`pill-btn ${autoCutEnabled ? 'active' : ''}`}
            onClick={() => setAutoCutEnabled((v) => !v)}
          >
            {autoCutEnabled ? `🎞️ Auto-Cut: CAM ${activeCutCam}` : '🎞️ Auto-Cut A/B'}
          </button>
          {viewMode === 'preview' && !cleanCaptureMode && (
            <button
              type="button"
              className={`pill-btn subtle ${diffHaloEnabled ? 'active' : ''}`}
              onClick={() => setDiffHaloEnabled((v) => !v)}
              title="Toggle warm-gold floor diff halo under modified actors in forked scenes"
            >
              ✨ Diff Halo
            </button>
          )}
          {viewMode === 'final' && (
            <button
              type="button"
              className={`pill-btn ${soundEnabled ? 'active' : ''}`}
              onClick={() => {
                setSoundEnabled((v) => {
                  const next = !v;
                  if (next) setCompareAudioSource('none');
                  return next;
                });
              }}
              title="Toggle native Omni Flash audio track (Cam A)"
            >
              {soundEnabled ? '🔊 Sound On' : '🔇 Muted'}
            </button>
          )}
          {hasAnyLiveVideo && (
            <button
              type="button"
              className="pill-btn blue"
              onClick={handleSyncReplay}
              title="Reset and sync playback across Cam A, Cam B & branch comparison"
            >
              🔄 Sync Replay
            </button>
          )}
          {!cleanCaptureMode && (
            <>
              <button
                type="button"
                className="pill-btn subtle"
                onClick={() => handleExportPreview(false)}
                disabled={exporting || renderingScene}
              >
                {exporting ? '⏳ Recording…' : '🎬 Export 4s'}
              </button>
              {onRenderScene && (
                <button
                  type="button"
                  className="pill-btn active"
                  onClick={() => handleExportPreview(true)}
                  disabled={exporting || renderingScene}
                >
                  {renderingScene
                    ? `⏳ Rendering (${renderElapsedSec}s)…`
                    : '✨ Render Scene'}
                </button>
              )}
            </>
          )}
        </div>
      </div>

      <div className="scene-narration">{sceneText.narration}</div>

      {!cleanCaptureMode && exportStatus && (
        <div className="status-banner blue">{exportStatus}</div>
      )}

      {renderingScene && renderProgress && Object.keys(renderProgress.scenes).length > 0 && (
        <div className="pipeline-stepper" role="status" aria-live="polite">
          <span style={{ fontSize: '0.73rem', fontWeight: 700, color: '#7dd3fc' }}>
            Live Per-Scene Progress:
          </span>
          {Object.values(renderProgress.scenes)
            .sort((a, b) => a.scene_id - b.scene_id)
            .map((sc) => {
              const lower = sc.status.toLowerCase();
              const isDone = lower.includes('done') || lower.includes('complete') || lower.includes('reused');
              const isActive = lower.includes('render') || lower.includes('run');
              const pillClass = isDone ? 'done' : isActive ? 'active' : '';
              const scoreText =
                typeof sc.qa_score === 'number' ? ` ${sc.qa_score.toFixed(2)}` : '';
              return (
                <span key={sc.scene_id} className={`pipeline-step ${pillClass}`}>
                  S{sc.scene_id} {sc.status}
                  {scoreText}
                  {sc.fallback_to_preview ? ' (2.5D)' : ''}
                </span>
              );
            })}
        </div>
      )}

      {renderingScene && renderElapsedSec >= 35 && onSwitchToCachedSafeBranch && (
        <div
          className="warning-banner"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 10,
            flexWrap: 'wrap',
          }}
        >
          <span>
            ⏱️ Live render taking longer than usual ({renderElapsedSec}s). Keep demo momentum smooth:
          </span>
          <button
            type="button"
            className="pill-btn active"
            onClick={onSwitchToCachedSafeBranch}
          >
            ⚡ Switch to Cached Starlight Branch
          </button>
        </div>
      )}

      {/* Dual-Camera 2.5D Stage (Always mounted so Three.js loop stays live) */}
      <div className="dual-cam-grid">
        <div
          className="cam-frame"
          style={{
            borderColor:
              autoCutEnabled && activeCutCam === 'A'
                ? 'var(--accent-gold)'
                : undefined,
            boxShadow:
              autoCutEnabled && activeCutCam === 'A'
                ? '0 0 0 2px rgba(251, 191, 36, 0.55)'
                : undefined,
            opacity: autoCutEnabled && activeCutCam !== 'A' ? 0.6 : 1,
            transition: 'all 0.35s ease',
          }}
        >
          <canvas
            ref={canvasARef}
            style={{ display: showFinalA ? 'none' : 'block' }}
          />
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
              muted={!soundEnabled}
              playsInline
              onError={() => markVideoFailed(camAUrl)}
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">
              {autoCutEnabled && activeCutCam === 'A' ? '🔴 ON AIR · ' : ''}
              CAM A · Front Wide
            </span>
            {showFinalA ? (
              <span className="cam-tag ai-ready">Omni Flash Render</span>
            ) : isDegradedA ? (
              <span className="cam-tag fallback">Degraded (2.5D Preview)</span>
            ) : (
              <span className="cam-tag">
                {pixelArtMode ? 'Pixel-Art 2.5D Preview' : '2.5D Live Preview (<1s)'}
              </span>
            )}
          </div>
        </div>

        <div
          className="cam-frame"
          style={{
            borderColor:
              autoCutEnabled && activeCutCam === 'B'
                ? 'var(--accent-gold)'
                : undefined,
            boxShadow:
              autoCutEnabled && activeCutCam === 'B'
                ? '0 0 0 2px rgba(251, 191, 36, 0.55)'
                : undefined,
            opacity: autoCutEnabled && activeCutCam !== 'B' ? 0.6 : 1,
            transition: 'all 0.35s ease',
          }}
        >
          <canvas
            ref={canvasBRef}
            style={{ display: showFinalB ? 'none' : 'block' }}
          />
          {showFinalB && camBUrl && (
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
          <div className="cam-badge-top">
            <span className="cam-tag">
              {autoCutEnabled && activeCutCam === 'B' ? '🔴 ON AIR · ' : ''}
              CAM B · 45° Close-Up
            </span>
            {showFinalB ? (
              <span className="cam-tag ai-ready">Omni Flash Render</span>
            ) : isDegradedB ? (
              <span className="cam-tag fallback">Degraded (2.5D Preview)</span>
            ) : (
              <span className="cam-tag">Synced World Coordinates</span>
            )}
          </div>
        </div>
      </div>

      {/* Side-by-Side Old vs New Branch Comparison */}
      {compareEnabled && compareBranch && compareScene && compareSceneText && (
        <div className="compare-grid">
          <div
            className="compare-card"
            style={{
              borderColor:
                compareAudioSource === 'baseline' ? 'var(--accent-rose)' : undefined,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                flexWrap: 'wrap',
              }}
            >
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#fda4af' }}>
                Baseline: {getBranchLabelEn(compareBranch)} (Scene {compareScene.scene_id})
              </span>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {showCompareOldVideo && (
                  <button
                    type="button"
                    className={`pill-btn subtle ${compareAudioSource === 'baseline' ? 'active' : ''}`}
                    onClick={() => handleSelectCompareAudio('baseline')}
                    title="Unmute only Baseline comparison audio"
                  >
                    {compareAudioSource === 'baseline' ? '🔊 Audio: Baseline' : '🔇 Listen'}
                  </button>
                )}
                {allBranches.length > 2 && onSelectCompareBranch && !cleanCaptureMode && (
                  <select
                    className="select-input"
                    style={{ padding: '3px 8px', fontSize: '0.72rem', maxWidth: 200 }}
                    value={compareBranch.branch_id}
                    onChange={(e) => onSelectCompareBranch(e.target.value)}
                    aria-label="Select baseline branch for left comparison slot"
                  >
                    {allBranches
                      .filter((b) => b.branch_id !== activeBranch.branch_id)
                      .map((b) => (
                        <option key={b.branch_id} value={b.branch_id}>
                          Compare: {getBranchLabelEn(b)}
                        </option>
                      ))}
                  </select>
                )}
              </div>
            </div>
            {showCompareOldVideo && compareCamAUrl && (
              <video
                ref={compareOldVideoRef}
                key={compareCamAUrl}
                src={compareCamAUrl}
                poster={compareCamAPoster}
                preload="auto"
                controls
                autoPlay
                loop
                muted={compareAudioSource !== 'baseline'}
                playsInline
                onError={() => markVideoFailed(compareCamAUrl)}
                style={{ width: '100%', maxHeight: 148, borderRadius: 8, background: '#000' }}
              />
            )}
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              {compareSceneText.narration}
            </div>
            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
              Lighting: {compareScene.lighting} · Props:{' '}
              {compareScene.stage_props.map((p) => `${p.id}=${p.state}`).join(', ')}
            </div>
          </div>

          <div
            className="compare-card"
            style={{
              borderColor:
                compareAudioSource === 'active' ? 'var(--accent-emerald)' : undefined,
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                flexWrap: 'wrap',
              }}
            >
              <span style={{ fontSize: '0.8rem', fontWeight: 700, color: '#6ee7b7' }}>
                Active: {getBranchLabelEn(activeBranch)} (Scene {scene.scene_id})
              </span>
              {showCompareNewVideo && (
                <button
                  type="button"
                  className={`pill-btn subtle ${compareAudioSource === 'active' ? 'active' : ''}`}
                  onClick={() => handleSelectCompareAudio('active')}
                  title="Unmute only Active comparison audio"
                >
                  {compareAudioSource === 'active' ? '🔊 Audio: Active' : '🔇 Listen'}
                </button>
              )}
            </div>
            {showCompareNewVideo && camAUrl && (
              <video
                ref={compareNewVideoRef}
                key={camAUrl}
                src={camAUrl}
                poster={camAPoster}
                preload="auto"
                controls
                autoPlay
                loop
                muted={compareAudioSource !== 'active'}
                playsInline
                onError={() => markVideoFailed(camAUrl)}
                style={{ width: '100%', maxHeight: 148, borderRadius: 8, background: '#000' }}
              />
            )}
            <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
              {sceneText.narration}
            </div>
            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
              Lighting: {scene.lighting} · Props:{' '}
              {scene.stage_props.map((p) => `${p.id}=${p.state}`).join(', ')}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
