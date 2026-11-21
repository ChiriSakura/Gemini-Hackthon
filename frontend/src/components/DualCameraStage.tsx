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
import {
  ThreeStageManager,
  type PreviewExportResult,
} from '../stage/ThreeStageManager';
import type { BranchState, Scene } from '../types/schema';
import { getBranchLabelEn, getSceneTextEn } from '../utils/displayLocale';

interface DualCameraStageProps {
  activeBranch: BranchState;
  compareBranch: BranchState | null;
  activeSceneIndex: number;
  viewMode: 'preview' | 'final';
  compareEnabled: boolean;
  pixelArtMode: boolean;
  renderingScene: boolean;
  isPlayingStory: boolean;
  cleanCaptureMode?: boolean;
  onTogglePlayStory: () => void;
  onExportComplete?: (result: PreviewExportResult) => void;
  onRenderScene?: (scene: Scene, preview: PreviewExportResult) => void;
}

export const DualCameraStage: React.FC<DualCameraStageProps> = ({
  activeBranch,
  compareBranch,
  activeSceneIndex,
  viewMode,
  compareEnabled,
  pixelArtMode,
  renderingScene,
  isPlayingStory,
  cleanCaptureMode = false,
  onTogglePlayStory,
  onExportComplete,
  onRenderScene,
}) => {
  const canvasARef = useRef<HTMLCanvasElement | null>(null);
  const canvasBRef = useRef<HTMLCanvasElement | null>(null);
  const managerRef = useRef<ThreeStageManager | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [autoCutEnabled, setAutoCutEnabled] = useState(false);
  const [activeCutCam, setActiveCutCam] = useState<'A' | 'B'>('A');
  const [soundEnabled, setSoundEnabled] = useState(false);

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
    managerRef.current.applyScene(scene, activeBranch.props_registry);
  }, [scene, activeBranch.props_registry]);

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

  const showFinalA =
    viewMode === 'final' &&
    Boolean(scene?.media.cam_a_final_url) &&
    !scene?.media.fallback_to_preview;
  const showFinalB =
    viewMode === 'final' &&
    Boolean(scene?.media.cam_b_final_url) &&
    !scene?.media.fallback_to_preview;

  const isDegraded =
    viewMode === 'final' &&
    (scene?.media.fallback_to_preview || !scene?.media.cam_a_final_url);

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
          {viewMode === 'final' && (
            <button
              type="button"
              className={`pill-btn ${soundEnabled ? 'active' : ''}`}
              onClick={() => setSoundEnabled((v) => !v)}
              title="Toggle native Omni Flash audio track (Cam A)"
            >
              {soundEnabled ? '🔊 Sound On' : '🔇 Muted'}
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
                  {renderingScene ? '⏳ Rendering…' : '✨ Render Scene'}
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
          {showFinalA && scene.media.cam_a_final_url && (
            <video
              key={scene.media.cam_a_final_url}
              src={scene.media.cam_a_final_url}
              controls
              autoPlay
              loop
              muted={!soundEnabled}
              playsInline
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">
              {autoCutEnabled && activeCutCam === 'A' ? '🔴 ON AIR · ' : ''}
              CAM A · Front Wide
            </span>
            {showFinalA ? (
              <span className="cam-tag ai-ready">Omni Flash Render</span>
            ) : isDegraded ? (
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
          {showFinalB && scene.media.cam_b_final_url && (
            <video
              key={scene.media.cam_b_final_url}
              src={scene.media.cam_b_final_url}
              controls
              autoPlay
              loop
              muted
              playsInline
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">
              {autoCutEnabled && activeCutCam === 'B' ? '🔴 ON AIR · ' : ''}
              CAM B · 45° Close-Up
            </span>
            {showFinalB ? (
              <span className="cam-tag ai-ready">Omni Flash Render</span>
            ) : isDegraded ? (
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
          <div className="compare-card">
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#fda4af' }}>
              Baseline: {getBranchLabelEn(compareBranch)} (Scene {compareScene.scene_id})
            </div>
            {viewMode === 'final' && compareScene.media.cam_a_final_url && (
              <video
                key={compareScene.media.cam_a_final_url}
                src={compareScene.media.cam_a_final_url}
                controls
                autoPlay
                loop
                muted
                playsInline
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

          <div className="compare-card">
            <div style={{ fontSize: '0.8rem', fontWeight: 700, color: '#6ee7b7' }}>
              Active: {getBranchLabelEn(activeBranch)} (Scene {scene.scene_id})
            </div>
            {viewMode === 'final' && scene.media.cam_a_final_url && (
              <video
                key={scene.media.cam_a_final_url}
                src={scene.media.cam_a_final_url}
                controls
                autoPlay
                loop
                muted
                playsInline
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
