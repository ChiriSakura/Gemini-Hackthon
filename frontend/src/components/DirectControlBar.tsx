/**
 * Unified Multimodal Director Control Bar (English-First UI).
 *
 * Feature & Use Cases:
 * - Combines camera + microphone into a single 3-second "Hold to Direct (3s Cam+Mic)"
 *   `.webm` capture button with live PiP preview.
 * - Includes "Snap Physical Prop" webcam capture panel wired to `POST /api/media/create-prop`.
 * - Provides text fallback input, target scene selector (1..4), quick hackathon
 *   demo preset chips, and inline contradiction confirmation.
 */
import React, { useRef, useState } from 'react';

interface DirectControlBarProps {
  targetSceneId: number;
  onTargetSceneChange: (sceneId: number) => void;
  submitting: boolean;
  creatingProp?: boolean;
  contradictionWarning: string | null;
  cleanCaptureMode?: boolean;
  onDismissContradiction?: () => void;
  onForceRewrite?: () => void;
  onCreateProp?: (opts: { name: string; imageBlob?: Blob | null }) => void;
  onSubmitFork: (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
  }) => void;
}

const DEMO_PROMPTS: Array<{ sceneId: number; label: string; text: string }> = [
  {
    sceneId: 2,
    label: 'HE · Scene 2 Starlight Gift',
    text: 'In Scene 2, Lia sets down her red umbrella and gently offers a glowing starlight token to soothe Nyx so the crystal vase stays intact.',
  },
  {
    sceneId: 2,
    label: 'Shield · Umbrella Guard',
    text: 'In Scene 2, Lia blocks the table with her red umbrella to protect the crystal vase from falling.',
  },
  {
    sceneId: 3,
    label: 'Snow · Scene 3 Golden Snow',
    text: 'In Scene 3, shift the conservatory lighting to warm golden snow as Nyx hops gently beside Lia.',
  },
  {
    sceneId: 3,
    label: 'Conflict Test · Unbroken Vase',
    text: 'In Scene 3, Lia picks up the completely unbroken crystal vase from the table and hands it to Nyx.',
  },
];

export const DirectControlBar: React.FC<DirectControlBarProps> = ({
  targetSceneId,
  onTargetSceneChange,
  submitting,
  creatingProp = false,
  contradictionWarning,
  cleanCaptureMode = false,
  onDismissContradiction,
  onForceRewrite,
  onCreateProp,
  onSubmitFork,
}) => {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [camError, setCamError] = useState<string | null>(null);
  const [propPanelOpen, setPropPanelOpen] = useState(false);
  const [propName, setPropName] = useState('Starlight Shield');
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const propVideoRef = useRef<HTMLVideoElement | null>(null);
  const propStreamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);

  const stopPropCam = () => {
    if (propStreamRef.current) {
      propStreamRef.current.getTracks().forEach((t) => t.stop());
      propStreamRef.current = null;
    }
    if (propVideoRef.current) {
      propVideoRef.current.srcObject = null;
    }
  };

  const handleTogglePropPanel = async () => {
    if (propPanelOpen) {
      stopPropCam();
      setPropPanelOpen(false);
      return;
    }
    setPropPanelOpen(true);
    setCamError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: 640, height: 360 },
        audio: false,
      });
      propStreamRef.current = stream;
      window.setTimeout(() => {
        if (propVideoRef.current) {
          propVideoRef.current.srcObject = stream;
        }
      }, 60);
    } catch {
      setCamError('Camera unavailable — enter a prop name to generate via text.');
    }
  };

  const handleCaptureProp = () => {
    if (!onCreateProp || !propName.trim() || creatingProp) return;
    const videoEl = propVideoRef.current;
    if (videoEl && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
      const canvas = document.createElement('canvas');
      canvas.width = videoEl.videoWidth;
      canvas.height = videoEl.videoHeight;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        onCreateProp({ name: propName.trim(), imageBlob: blob });
        stopPropCam();
        setPropPanelOpen(false);
      }, 'image/png');
    } else {
      onCreateProp({ name: propName.trim(), imageBlob: null });
      stopPropCam();
      setPropPanelOpen(false);
    }
  };

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
      setCamError('Camera/microphone unavailable — use text input on the right.');
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
            ⚠️ <strong>Contradiction Check:</strong> {contradictionWarning}
          </span>
          <span style={{ display: 'flex', gap: 8 }}>
            {onDismissContradiction && (
              <button
                type="button"
                className="pill-btn"
                onClick={onDismissContradiction}
              >
                Cancel
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
              Force Rewrite
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
          {recording ? '🔴 Recording 3s Cam+Mic…' : '🎥 Hold to Direct (3s Cam+Mic)'}
        </button>

        {onCreateProp && (
          <button
            type="button"
            className={`pill-btn ${propPanelOpen ? 'active' : ''}`}
            onClick={handleTogglePropPanel}
            disabled={creatingProp}
          >
            {creatingProp ? '⏳ Creating Prop…' : '📷 Snap Physical Prop'}
          </button>
        )}

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
          aria-label="Target scene to rewrite"
        >
          <option value={1}>Rewrite Scene 1</option>
          <option value={2}>Rewrite Scene 2</option>
          <option value={3}>Rewrite Scene 3</option>
          <option value={4}>Rewrite Scene 4</option>
        </select>

        <input
          type="text"
          className="text-input"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Type a one-sentence rewrite (e.g. In Scene 2, Lia offers a starlight gift to soothe Nyx...)"
        />

        <button
          type="submit"
          className="send-btn"
          disabled={submitting || !text.trim()}
        >
          {submitting ? 'Simulating…' : '⚡ Fork & Direct'}
        </button>
      </form>

      {propPanelOpen && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            flexWrap: 'wrap',
            padding: '8px 10px',
            borderRadius: 10,
            background: 'rgba(15, 23, 42, 0.85)',
            border: '1px solid rgba(56, 189, 248, 0.35)',
          }}
        >
          <video
            ref={propVideoRef}
            className="webcam-pip"
            autoPlay
            muted
            playsInline
          />
          <input
            type="text"
            className="text-input"
            style={{ maxWidth: 240 }}
            value={propName}
            onChange={(e) => setPropName(e.target.value)}
            placeholder="Prop name (e.g. Starlight Shield)"
          />
          <button
            type="button"
            className="send-btn"
            onClick={handleCaptureProp}
            disabled={creatingProp || !propName.trim()}
          >
            {creatingProp ? 'Generating…' : '📸 Capture & Place on Stage'}
          </button>
          <button
            type="button"
            className="pill-btn"
            onClick={handleTogglePropPanel}
          >
            Close
          </button>
        </div>
      )}

      {!cleanCaptureMode && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
          <span style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>
            Quick Presets:
          </span>
          {DEMO_PROMPTS.map((preset, idx) => (
            <button
              key={idx}
              type="button"
              className="pill-btn subtle"
              style={{ fontSize: '0.72rem', padding: '3px 9px' }}
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
