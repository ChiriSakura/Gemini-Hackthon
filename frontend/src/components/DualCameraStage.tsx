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
  onExportComplete?: (result: PreviewExportResult) => void;
}

export const DualCameraStage: React.FC<DualCameraStageProps> = ({
  activeBranch,
  compareBranch,
  activeSceneIndex,
  viewMode,
  compareEnabled,
  onExportComplete,
}) => {
  const canvasARef = useRef<HTMLCanvasElement | null>(null);
  const canvasBRef = useRef<HTMLCanvasElement | null>(null);
  const managerRef = useRef<ThreeStageManager | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

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

  const handleExportPreview = async () => {
    if (!managerRef.current || exporting) return;
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
            className="pill-btn"
            onClick={handleExportPreview}
            disabled={exporting}
          >
            {exporting ? '⏳ 录制双机位预览中...' : '🎬 导出 4s 双机位预览 (.webm / PNG)'}
          </button>
        </div>
      </div>

      <div className="scene-narration">{scene.narration}</div>

      {exportStatus && (
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
        <div className="cam-frame">
          <canvas
            ref={canvasARef}
            style={{ display: showFinalA ? 'none' : 'block' }}
          />
          {showFinalA && scene.media.cam_a_final_url && (
            <video
              src={scene.media.cam_a_final_url}
              controls
              autoPlay
              loop
              muted
              playsInline
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">CAM A · 正面全景</span>
            {showFinalA ? (
              <span className="cam-tag ai-ready">Omni Flash 成片</span>
            ) : isDegraded ? (
              <span className="cam-tag fallback">已降级 · 2.5D 实时预览</span>
            ) : (
              <span className="cam-tag">2.5D 实时预览 (&lt;1s)</span>
            )}
          </div>
        </div>

        <div className="cam-frame">
          <canvas
            ref={canvasBRef}
            style={{ display: showFinalB ? 'none' : 'block' }}
          />
          {showFinalB && scene.media.cam_b_final_url && (
            <video
              src={scene.media.cam_b_final_url}
              controls
              autoPlay
              loop
              muted
              playsInline
            />
          )}
          <div className="cam-badge-top">
            <span className="cam-tag">CAM B · 45° 斜侧特写</span>
            {showFinalB ? (
              <span className="cam-tag ai-ready">Omni Flash 成片</span>
            ) : isDegraded ? (
              <span className="cam-tag fallback">已降级 · 2.5D 实时预览</span>
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
            <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
              {compareScene.narration}
            </div>
            <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
              灯光: {compareScene.lighting} · 道具状态:{' '}
              {compareScene.stage_props
                .map((p) => `${p.id}=${p.state}`)
                .join(', ')}
            </div>
          </div>

          <div className="compare-card">
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#6ee7b7' }}>
              当前分支：{activeBranch.branch_label}（第 {scene.scene_id} 幕）
            </div>
            <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>
              {scene.narration}
            </div>
            <div style={{ fontSize: '0.75rem', color: '#94a3b8' }}>
              灯光: {scene.lighting} · 道具状态:{' '}
              {scene.stage_props.map((p) => `${p.id}=${p.state}`).join(', ')}
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
