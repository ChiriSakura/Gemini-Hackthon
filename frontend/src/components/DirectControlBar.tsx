/**
 * Unified Multimodal Director Control Bar.
 *
 * Feature & Use Cases:
 * - Combines camera + microphone into a single 3-second "Hold to Direct (Cam+Mic)"
 *   `.webm` capture button with live PiP preview.
 * - Provides a text fallback input, target scene selector (1..4), and quick
 *   hackathon demo prompts for live judge interaction.
 * - Handles contradiction confirmation if backend returns `contradiction_warning`.
 */
import React, { useRef, useState } from 'react';

interface DirectControlBarProps {
  targetSceneId: number;
  onTargetSceneChange: (sceneId: number) => void;
  submitting: boolean;
  contradictionWarning: string | null;
  cleanCaptureMode?: boolean;
  onDismissContradiction?: () => void;
  onForceRewrite?: () => void;
  onSubmitFork: (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
  }) => void;
}

const DEMO_PROMPTS: Array<{ sceneId: number; label: string; text: string }> = [
  {
    sceneId: 2,
    label: 'HE · 第2幕递星光礼物安抚黑猫',
    text: '第2幕她把红伞放下，轻轻递出星光礼物安抚黑猫，黑猫没有撞翻花瓶！',
  },
  {
    sceneId: 2,
    label: '防护 · 第2幕举伞挡在圆桌前',
    text: '其实第2幕她举起雨伞挡在圆桌前，花瓶稳稳留在了桌上。',
  },
  {
    sceneId: 3,
    label: '雪夜 · 第3幕暖金初雪共赏',
    text: '第3幕把灯光换成暖金初雪，黑猫跳上圆桌一起赏雪。',
  },
  {
    sceneId: 3,
    label: '冲突测试 · 拿未碎花瓶献花',
    text: '第3幕她把完好无损的水晶花瓶递给黑猫（测试矛盾检查）。',
  },
];

export const DirectControlBar: React.FC<DirectControlBarProps> = ({
  targetSceneId,
  onTargetSceneChange,
  submitting,
  contradictionWarning,
  cleanCaptureMode = false,
  onDismissContradiction,
  onForceRewrite,
  onSubmitFork,
}) => {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  const stopStream = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  };

  const handleStartHold = async () => {
    if (recording || submitting) return;
    setCamError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 360 },
        audio: true,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }

      const chunks: BlobPart[] = [];
      const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp8,opus')
        ? 'video/webm;codecs=vp8,opus'
        : 'video/webm';
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      recorderRef.current = recorder;

      recorder.ondataavailable = (ev) => {
        if (ev.data.size > 0) chunks.push(ev.data);
      };
      recorder.onstop = () => {
        setRecording(false);
        stopStream();
        if (chunks.length > 0) {
          const blob = new Blob(chunks, { type: mime });
          onSubmitFork({ clip: blob, text });
        }
      };

      recorder.start();
      setRecording(true);
      timerRef.current = window.setTimeout(() => {
        if (recorder.state === 'recording') {
          recorder.stop();
        }
      }, 3000);
    } catch {
      setCamError('未检测到摄像头/麦克风权限，请直接使用右侧文字输入框导戏。');
      stopStream();
    }
  };

  const handleStopHold = () => {
    if (recorderRef.current && recorderRef.current.state === 'recording') {
      recorderRef.current.stop();
    }
  };

  const handleTextSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || submitting) return;
    onSubmitFork({ text });
  };

  return (
    <section className="director-bar">
      {contradictionWarning && (
        <div className="warning-banner" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <span>
            ⚠️ <strong>逻辑矛盾检查：</strong>
            {contradictionWarning}
          </span>
          <span style={{ display: 'flex', gap: 8 }}>
            {onDismissContradiction && (
              <button
                type="button"
                className="pill-btn"
                onClick={onDismissContradiction}
              >
                取消改写
              </button>
            )}
            <button
              type="button"
              className="pill-btn active"
              onClick={() =>
                onForceRewrite
                  ? onForceRewrite()
                  : onSubmitFork({ text, confirmContradiction: true })
              }
            >
              仍要强制改写
            </button>
          </span>
        </div>
      )}

      {camError && <div className="warning-banner">{camError}</div>}

      <form className="director-controls-row" onSubmit={handleTextSubmit}>
        <button
          type="button"
          className={`hold-direct-btn ${recording ? 'recording' : ''}`}
          onMouseDown={handleStartHold}
          onMouseUp={handleStopHold}
          onMouseLeave={handleStopHold}
          onTouchStart={handleStartHold}
          onTouchEnd={handleStopHold}
          disabled={submitting}
        >
          {recording ? '🔴 正在录制 3s 音画手势...' : '🎥 按住手势+语音导戏 (3s)'}
        </button>

        {recording && (
          <video
            ref={videoRef}
            className="webcam-pip"
            autoPlay
            muted
            playsInline
          />
        )}

        <select
          className="select-input"
          value={targetSceneId}
          onChange={(e) => onTargetSceneChange(Number(e.target.value))}
          aria-label="选择改写幕次"
        >
          <option value={1}>改写第 1 幕</option>
          <option value={2}>改写第 2 幕</option>
          <option value={3}>改写第 3 幕</option>
          <option value={4}>改写第 4 幕</option>
        </select>

        <input
          type="text"
          className="text-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="文字兜底：输入一句话改写剧情（例如：第2幕她把红伞放下，递出礼物安抚黑猫）"
        />

        <button
          type="submit"
          className="send-btn"
          disabled={submitting || !text.trim()}
        >
          {submitting ? '推演中...' : '⚡ 改写并分叉'}
        </button>
      </form>

      {!cleanCaptureMode && (
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
            快捷试戏：
          </span>
          {DEMO_PROMPTS.map((preset, idx) => (
            <button
              key={idx}
              type="button"
              className="pill-btn"
              style={{ fontSize: '0.73rem', padding: '3px 10px' }}
              onClick={() => {
                setText(preset.text);
                onTargetSceneChange(preset.sceneId);
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
      )}
    </section>
  );
};
