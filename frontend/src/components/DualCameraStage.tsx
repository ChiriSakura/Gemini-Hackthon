/**
 * Dual-Camera 2.5D Stage & Side-by-Side Branch Comparison View.
 *
 * Feature & Use Cases:
 * - Hosts Camera A (front wide) and Camera B (45-deg close-up) Three.js canvases.
 * - Supports toggling between real-time 2.5D preview and Omni Flash AI renders,
 *   showing an explicit fallback badge whenever AI video is degraded/pending.
 * - Supports side-by-side comparison between the original branch and forked branch.
 * - Provides a 4s `.webm` / start+end PNG keyframe export trigger for P2 rendering.
 */
import React, { useEffect, useRef, useState } from 'react';
import {
  ThreeStageManager,
  type PreviewExportResult,
} from '../stage/ThreeStageManager';
import type { BranchState, Scene } from '../types/schema';

interface DualCameraStageProps {
  activeBranch: BranchState;
  compareBranch: BranchState | null;
  activeSceneIndex: number;
  viewMode: 'preview' | 'final';
  compareEnabled: boolean;
  pixelArtMode: boolean;
  renderingScene: boolean;
  cleanCaptureMode?: boolean;
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
  cleanCaptureMode = false,
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
  const [failedVideoUrls, setFailedVideoUrls] = useState<Record<string, boolean>>({});
  const [renderElapsedSec, setRenderElapsedSec] = useState(0);

  const markVideoFailed = (url: string) => {
    setFailedVideoUrls((prev) => (prev[url] ? prev : { ...prev, [url]: true }));
  };

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

  useEffect(() => {
    if (!autoCutEnabled) return;
    const timer = window.setInterval(() => {
      setActiveCutCam((prev) => (prev === 'A' ? 'B' : 'A'));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [autoCutEnabled]);

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

  const scene: Scene =
    activeBranch.scenes[activeSceneIndex] ?? activeBranch.scenes[0];
  const compareScene: Scene | null = compareBranch
    ? (compareBranch.scenes[activeSceneIndex] ?? compareBranch.scenes[0])
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
    setExportStatus('正在录制 A/B 双机位 4 秒 .webm 预览...');
    const result = await managerRef.current.exportScenePreview(4000);
    setExporting(false);
    if (result.mode === 'webm') {
      const kbA = Math.round((result.camABlob?.size ?? 0) / 1024);
      const kbB = Math.round((result.camBBlob?.size ?? 0) / 1024);
      setExportStatus(
        `✅ 导出成功 (.webm): CamA ${kbA}KB / CamB ${kbB}KB (${result.elapsedMs}ms)`,
      );
    } else {
      setExportStatus(
        `⚠️ 已自动降级为首尾帧 PNG (${result.elapsedMs}ms)`,
      );
    }
    onExportComplete?.(result);
    if (triggerAiRender) {
      onRenderScene?.(scene, result);
    }
  };

  const camAUrl = scene?.media.cam_a_final_url ?? null;
  const camBUrl = scene?.media.cam_b_final_url ?? null;
  const compareCamAUrl = compareScene?.media.cam_a_final_url ?? null;

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

  return (
    <section className="stage-theater">
      <div className="stage-header-row">
        <div>
          <strong style={{ fontSize: '1.05rem' }}>
            第 {scene.scene_id} 幕 · {scene.title}
          </strong>
          <span
            style={{
              marginLeft: 10,
              fontSize: '0.78rem',
              color: 'var(--text-muted)',
            }}
          >
            灯光: {scene.lighting} · 情绪: {scene.vocal_emotion}
            {scene.reused_from_branch
              ? ` · 复用 ${scene.reused_from_branch}（0 重绘）`
              : ''}
          </span>
        </div>

        <div className="toolbar-actions">
          <button
            type="button"
            className={`pill-btn ${autoCutEnabled ? 'active' : ''}`}
            onClick={() => setAutoCutEnabled((v) => !v)}
          >
            {autoCutEnabled ? `🎞️ Auto-Cut: CAM ${activeCutCam}` : '🎞️ Auto-Cut A/B'}
          </button>
          {hasAnyLiveVideo && (
            <button
              type="button"
              className="pill-btn blue"
              onClick={handleSyncReplay}
              title="重置并同步播放双机位与新旧对比视频"
            >
              🔄 同步重播
            </button>
          )}
          {!cleanCaptureMode && (
            <button
              type="button"
              className="pill-btn"
              onClick={() => handleExportPreview(false)}
              disabled={exporting || renderingScene}
            >
              {exporting ? '⏳ 录制预览中...' : '🎬 导出 4s 双机位预览'}
            </button>
          )}
          {!cleanCaptureMode && onRenderScene && (
            <button
              type="button"
              className="pill-btn active"
              onClick={() => handleExportPreview(true)}
              disabled={exporting || renderingScene}
            >
              {renderingScene
                ? `⏳ Omni Flash 渲染中 (${renderElapsedSec}s)...`
                : '✨ 渲染本幕 AI 成片'}
            </button>
          )}
        </div>
      </div>

      <div className="scene-narration">{scene.narration}</div>

      {!cleanCaptureMode && exportStatus && (
        <div
          style={{
            fontSize: '0.8rem',
            color: '#bae6fd',
            padding: '4px 10px',
            background: 'rgba(56, 189, 248, 0.1)',
            borderRadius: 6,
          }}
        >
          {exportStatus}
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
            opacity: autoCutEnabled && activeCutCam !== 'A' ? 0.62 : 1,
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
              controls
              autoPlay
              loop
              muted
              playsInline
              onError={() => markVideoFailed(camAUrl)}
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">
              {autoCutEnabled && activeCutCam === 'A' ? '🔴 ON AIR · ' : ''}
              CAM A · 正面全景
            </span>
            {showFinalA ? (
              <span className="cam-tag ai-ready">Omni Flash 成片</span>
            ) : isDegradedA ? (
              <span className="cam-tag fallback">已降级 (2.5D Preview)</span>
            ) : (
              <span className="cam-tag">
                {pixelArtMode ? '像素风 2.5D 预览' : '2.5D 实时预览 (<1s)'}
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
            opacity: autoCutEnabled && activeCutCam !== 'B' ? 0.62 : 1,
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
              CAM B · 45° 斜侧特写
            </span>
            {showFinalB ? (
              <span className="cam-tag ai-ready">Omni Flash 成片</span>
            ) : isDegradedB ? (
              <span className="cam-tag fallback">已降级 (2.5D Preview)</span>
            ) : (
              <span className="cam-tag">同步世界坐标</span>
            )}
          </div>
        </div>
      </div>

      {/* Side-by-Side Old vs New Branch Comparison */}
      {compareEnabled && compareBranch && compareScene && (
        <div className="compare-grid">
          <div className="compare-card">
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#fda4af' }}>
              旧分支：{compareBranch.branch_label}（第 {compareScene.scene_id} 幕）
            </div>
            {showCompareOldVideo && compareCamAUrl && (
              <video
                ref={compareOldVideoRef}
                key={compareCamAUrl}
                src={compareCamAUrl}
                controls
                autoPlay
                loop
                muted
                playsInline
                onError={() => markVideoFailed(compareCamAUrl)}
                style={{ width: '100%', maxHeight: 160, borderRadius: 8, background: '#000' }}
              />
            )}
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {compareScene.narration}
            </div>
            <div style={{ fontSize: '0.73rem', color: '#94a3b8' }}>
              灯光: {compareScene.lighting} · 道具:{' '}
              {compareScene.stage_props
                .map((p) => `${p.id}=${p.state}`)
                .join(', ')}
            </div>
          </div>

          <div className="compare-card">
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#6ee7b7' }}>
              当前分支：{activeBranch.branch_label}（第 {scene.scene_id} 幕）
            </div>
            {showCompareNewVideo && camAUrl && (
              <video
                ref={compareNewVideoRef}
                key={camAUrl}
                src={camAUrl}
                controls
                autoPlay
                loop
                muted
                playsInline
                onError={() => markVideoFailed(camAUrl)}
                style={{ width: '100%', maxHeight: 160, borderRadius: 8, background: '#000' }}
              />
            )}
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
              {scene.narration}
            </div>
            <div style={{ fontSize: '0.73rem', color: '#94a3b8' }}>
              灯光: {scene.lighting} · 道具:{' '}
              {scene.stage_props.map((p) => `${p.id}=${p.state}`).join(', ')}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
