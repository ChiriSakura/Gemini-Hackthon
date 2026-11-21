/**
 * Director Input Dock (Zone 3 of the first view, pinned at the bottom).
 *
 * Feature & Use Cases:
 * - One bar with a compact scene selector, a wide text field, and three
 *   clearly separate controls: "Direct" (send the typed rewrite), "Hold to
 *   talk" (press and hold to record up to 3 seconds of camera + microphone)
 *   and "Show an object" (snap a physical prop with the camera and place it).
 * - Example prompts live in a small "Examples" popover; choosing one fills the
 *   text field so it is sent through the same live rewrite path.
 * - Hosts the inline contradiction confirmation and camera/microphone hints.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { IconCamera, IconClapper, IconClose, IconList, IconMic } from './Icons';

export type PropPlacement = 'table' | 'held' | 'floor';

interface DirectControlBarProps {
  targetSceneId: number;
  onTargetSceneChange: (sceneId: number) => void;
  submitting: boolean;
  creatingProp?: boolean;
  contradictionWarning: string | null;
  cleanCaptureMode?: boolean;
  onDismissContradiction?: () => void;
  onForceRewrite?: () => void;
  onCreateProp?: (opts: {
    name: string;
    imageBlob?: Blob | null;
    placement: PropPlacement;
  }) => void;
  /** Resolves to true only when a new branch was actually added. */
  onSubmitFork: (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
  }) => Promise<boolean> | void;
}

const MAX_CLIP_MS = 3000;
const MIN_CLIP_MS = 1000;

/**
 * Ask for camera + microphone at 1280x720 (ideal, never a hard requirement),
 * falling back to any camera and finally to voice only when no camera works.
 * A permission refusal is rethrown so the caller can explain it.
 */
async function acquireDirectorStream(): Promise<MediaStream> {
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) throw new Error('unsupported');
  const isDenied = (err: unknown) => {
    const name = (err as { name?: string } | null)?.name;
    return name === 'NotAllowedError' || name === 'SecurityError';
  };
  try {
    return await media.getUserMedia({
      video: { width: { ideal: 1280 }, height: { ideal: 720 } },
      audio: true,
    });
  } catch (err) {
    if (isDenied(err)) throw err;
    try {
      return await media.getUserMedia({ video: true, audio: true });
    } catch (err2) {
      if (isDenied(err2)) throw err2;
      return media.getUserMedia({ audio: true });
    }
  }
}

function describeMediaError(err: unknown): string {
  const name = (err as { name?: string } | null)?.name;
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return 'Camera/microphone permission was denied — allow access in the browser, or type instead.';
  }
  if (name === 'NotFoundError' || name === 'OverconstrainedError') {
    return 'No camera or microphone found — type instead.';
  }
  if (name === 'NotReadableError') {
    return 'Camera or microphone is in use by another app — close it, or type instead.';
  }
  return 'Camera/microphone unavailable — type instead.';
}

function pickRecorderMime(hasVideo: boolean): string | undefined {
  const candidates = hasVideo
    ? ['video/webm;codecs=vp8,opus', 'video/webm']
    : ['audio/webm;codecs=opus', 'audio/webm'];
  return candidates.find((m) => MediaRecorder.isTypeSupported(m));
}

const DEMO_PROMPTS: Array<{ sceneId: number; label: string; text: string }> = [
  {
    sceneId: 2,
    label: 'Starlight gift',
    text: 'In Scene 2, Lia sets down her red umbrella and gently offers a glowing starlight token to soothe Nyx so the crystal vase stays intact.',
  },
  {
    sceneId: 2,
    label: 'Umbrella guard',
    text: 'In Scene 2, Lia blocks the table with her red umbrella to protect the crystal vase from falling.',
  },
  {
    sceneId: 3,
    label: 'Golden snow',
    text: 'In Scene 3, shift the conservatory lighting to warm golden snow as Nyx hops gently beside Lia.',
  },
  {
    sceneId: 3,
    label: 'Contradiction test',
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
  const [remainingMs, setRemainingMs] = useState(MAX_CLIP_MS);
  const [holdHint, setHoldHint] = useState<string | null>(null);
  const [examplesOpen, setExamplesOpen] = useState(false);
  const textInputRef = useRef<HTMLInputElement | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [camError, setCamError] = useState<string | null>(null);
  const [propPanelOpen, setPropPanelOpen] = useState(false);
  const [propName, setPropName] = useState('Starlight Shield');
  const [propPlacement, setPropPlacement] = useState<PropPlacement>('table');
  const [capturedPropBlob, setCapturedPropBlob] = useState<Blob | null>(null);
  const [capturedPropPreviewUrl, setCapturedPropPreviewUrl] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const propVideoRef = useRef<HTMLVideoElement | null>(null);
  const propStreamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const countdownIntervalRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const meterRafRef = useRef<number | null>(null);
  // Set synchronously on press so a second press cannot start a second stream
  const holdBusyRef = useRef(false);
  const holdReleasedRef = useRef(false);
  const recordStartedAtRef = useRef(0);

  const clearCapturedProp = () => {
    if (capturedPropPreviewUrl) {
      URL.revokeObjectURL(capturedPropPreviewUrl);
    }
    setCapturedPropBlob(null);
    setCapturedPropPreviewUrl(null);
  };

  const stopPropCam = () => {
    if (propStreamRef.current) {
      propStreamRef.current.getTracks().forEach((t) => t.stop());
      propStreamRef.current = null;
    }
    if (propVideoRef.current) {
      propVideoRef.current.srcObject = null;
    }
  };

  const startPropCam = async () => {
    setCamError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      propStreamRef.current = stream;
      window.setTimeout(() => {
        if (propVideoRef.current) {
          propVideoRef.current.srcObject = stream;
        }
      }, 60);
    } catch (err) {
      const name = (err as { name?: string } | null)?.name;
      setCamError(
        name === 'NotAllowedError'
          ? 'Camera permission was denied — allow access, or enter a prop name to generate via text.'
          : 'Camera unavailable — enter a prop name to generate via text.',
      );
    }
  };

  const handleTogglePropPanel = async () => {
    setExamplesOpen(false);
    if (propPanelOpen) {
      stopPropCam();
      clearCapturedProp();
      setPropPanelOpen(false);
      return;
    }
    setPropPanelOpen(true);
    clearCapturedProp();
    await startPropCam();
  };

  const handleSnapPropPreview = () => {
    const videoEl = propVideoRef.current;
    if (videoEl && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
      const canvas = document.createElement('canvas');
      canvas.width = videoEl.videoWidth;
      canvas.height = videoEl.videoHeight;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        if (!blob) return;
        clearCapturedProp();
        setCapturedPropBlob(blob);
        setCapturedPropPreviewUrl(URL.createObjectURL(blob));
      }, 'image/png');
    }
  };

  const handleSubmitProp = () => {
    if (!onCreateProp || !propName.trim() || creatingProp) return;
    if (capturedPropBlob) {
      onCreateProp({
        name: propName.trim(),
        imageBlob: capturedPropBlob,
        placement: propPlacement,
      });
      stopPropCam();
      clearCapturedProp();
      setPropPanelOpen(false);
      return;
    }
    const videoEl = propVideoRef.current;
    if (videoEl && videoEl.videoWidth > 0 && videoEl.videoHeight > 0) {
      const canvas = document.createElement('canvas');
      canvas.width = videoEl.videoWidth;
      canvas.height = videoEl.videoHeight;
      const ctx = canvas.getContext('2d');
      ctx?.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
      canvas.toBlob((blob) => {
        onCreateProp({
          name: propName.trim(),
          imageBlob: blob,
          placement: propPlacement,
        });
        stopPropCam();
        clearCapturedProp();
        setPropPanelOpen(false);
      }, 'image/png');
    } else {
      onCreateProp({
        name: propName.trim(),
        imageBlob: null,
        placement: propPlacement,
      });
      stopPropCam();
      clearCapturedProp();
      setPropPanelOpen(false);
    }
  };

  const stopStream = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (countdownIntervalRef.current !== null) {
      window.clearInterval(countdownIntervalRef.current);
      countdownIntervalRef.current = null;
    }
    if (meterRafRef.current !== null) {
      cancelAnimationFrame(meterRafRef.current);
      meterRafRef.current = null;
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    setAudioLevel(0);
    setRemainingMs(MAX_CLIP_MS);
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  };

  const startAudioMeter = (stream: MediaStream) => {
    try {
      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AudioCtx || stream.getAudioTracks().length === 0) return;
      const ctx = new AudioCtx();
      audioCtxRef.current = ctx;
      const source = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      const data = new Uint8Array(analyser.fftSize);

      const sample = () => {
        if (!audioCtxRef.current) return;
        analyser.getByteTimeDomainData(data);
        let sumSq = 0;
        for (let i = 0; i < data.length; i++) {
          const norm = (data[i] - 128) / 128;
          sumSq += norm * norm;
        }
        const rms = Math.sqrt(sumSq / data.length);
        setAudioLevel(Math.min(1, rms * 3.8));
        meterRafRef.current = requestAnimationFrame(sample);
      };
      sample();
    } catch {
      // Ignore Web Audio API restrictions gracefully
    }
  };

  const handleRelease = useCallback(() => {
    holdReleasedRef.current = true;
    const recorder = recorderRef.current;
    if (recorder && recorder.state === 'recording') {
      recorder.stop();
    }
  }, []);

  const detachReleaseListeners = useCallback(() => {
    window.removeEventListener('pointerup', handleRelease);
    window.removeEventListener('pointercancel', handleRelease);
    window.removeEventListener('blur', handleRelease);
  }, [handleRelease]);

  const endHold = () => {
    recorderRef.current = null;
    holdBusyRef.current = false;
    setRecording(false);
    stopStream();
    detachReleaseListeners();
  };

  const handleStartHold = async () => {
    if (holdBusyRef.current || submitting) return;
    holdBusyRef.current = true;
    holdReleasedRef.current = false;
    setCamError(null);
    setHoldHint(null);
    // Release is tracked on the window so sliding off the button keeps recording
    window.addEventListener('pointerup', handleRelease);
    window.addEventListener('pointercancel', handleRelease);
    window.addEventListener('blur', handleRelease);

    let stream: MediaStream;
    try {
      stream = await acquireDirectorStream();
    } catch (err) {
      endHold();
      setCamError(describeMediaError(err));
      return;
    }

    if (holdReleasedRef.current) {
      // Released while the permission prompt was open: nothing was recorded
      stream.getTracks().forEach((t) => t.stop());
      endHold();
      setHoldHint('Camera and microphone are ready — press and hold again to record.');
      return;
    }

    streamRef.current = stream;
    startAudioMeter(stream);

    const chunks: BlobPart[] = [];
    const mime = pickRecorderMime(stream.getVideoTracks().length > 0);
    let recorder: MediaRecorder;
    try {
      recorder = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      endHold();
      setCamError('Recording is not supported in this browser — type instead.');
      return;
    }
    recorderRef.current = recorder;

    recorder.ondataavailable = (ev) => {
      if (ev.data.size > 0) chunks.push(ev.data);
    };
    recorder.onerror = () => {
      endHold();
      setCamError('Recording failed — try again, or type instead.');
    };
    recorder.onstop = () => {
      const durationMs = Date.now() - recordStartedAtRef.current;
      const blobType = recorder.mimeType || mime || 'video/webm';
      endHold();
      if (durationMs < MIN_CLIP_MS || chunks.length === 0) {
        setHoldHint(
          `Clip too short (${(durationMs / 1000).toFixed(1)}s) — hold for at least 1 second.`,
        );
        return;
      }
      // A recorded clip is sent on its own: text box content is never attached
      void onSubmitFork({ clip: new Blob(chunks, { type: blobType }) });
    };

    recordStartedAtRef.current = Date.now();
    setRemainingMs(MAX_CLIP_MS);
    recorder.start();
    setRecording(true);
    countdownIntervalRef.current = window.setInterval(() => {
      setRemainingMs(Math.max(0, MAX_CLIP_MS - (Date.now() - recordStartedAtRef.current)));
    }, 50);
    timerRef.current = window.setTimeout(() => {
      if (recorder.state === 'recording') {
        recorder.stop();
      }
    }, MAX_CLIP_MS);
  };

  // The PiP <video> only mounts once `recording` is true, so attach the stream here
  useEffect(() => {
    if (recording && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, [recording]);

  // Always release the camera, microphone and listeners when the bar unmounts
  useEffect(() => {
    return () => {
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== 'inactive') {
        recorder.onstop = null;
        recorder.stop();
      }
      streamRef.current?.getTracks().forEach((t) => t.stop());
      propStreamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => {});
      detachReleaseListeners();
    };
  }, [detachReleaseListeners]);

  const handleTextSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || submitting) return;
    const ok = await onSubmitFork({ text });
    if (ok) setText('');
  };

  return (
    <section className="dock-input" aria-label="Director input">
      {contradictionWarning && (
        <div className="notice error" role="alert">
          <span>
            <strong>Contradiction check:</strong> {contradictionWarning}
          </span>
          {onDismissContradiction && (
            <button type="button" className="notice-btn" onClick={onDismissContradiction}>
              Cancel
            </button>
          )}
          <button
            type="button"
            className="notice-btn strong"
            disabled={submitting}
            onClick={() =>
              onForceRewrite
                ? onForceRewrite()
                : onSubmitFork({ text, confirmContradiction: true })
            }
          >
            Force rewrite
          </button>
        </div>
      )}

      {camError && (
        <div className="notice error" role="alert">
          <span>{camError}</span>
          <button
            type="button"
            className="icon-btn"
            aria-label="Dismiss"
            title="Dismiss"
            onClick={() => setCamError(null)}
          >
            <IconClose size={15} />
          </button>
        </div>
      )}
      {holdHint && !camError && (
        <div className="notice" role="status">
          <span>{holdHint}</span>
        </div>
      )}

      {recording && (
        <div className="dock-pop row" role="status">
          <video ref={videoRef} className="webcam-pip mirrored" autoPlay muted playsInline />
          <div className="countdown-meter">
            <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#fde68a' }}>
              {(remainingMs / 1000).toFixed(1)}s left · release to send
            </div>
            <div className="countdown-track">
              <div
                className="countdown-fill"
                style={{ width: `${Math.round((remainingMs / MAX_CLIP_MS) * 100)}%` }}
              />
            </div>
            <div className="rms-row" title="Live microphone input level">
              <IconMic size={13} />
              <div className="rms-track">
                <div className="rms-fill" style={{ width: `${Math.round(audioLevel * 100)}%` }} />
              </div>
            </div>
          </div>
        </div>
      )}

      {examplesOpen && !cleanCaptureMode && (
        <div className="dock-pop" role="dialog" aria-label="Example rewrites">
          <div className="examples-list">
            {DEMO_PROMPTS.map((preset, idx) => (
              <button
                key={idx}
                type="button"
                onClick={() => {
                  setText(preset.text);
                  onTargetSceneChange(preset.sceneId);
                  setExamplesOpen(false);
                  textInputRef.current?.focus();
                }}
              >
                <small>
                  Scene {preset.sceneId} · {preset.label}
                </small>
                {preset.text}
              </button>
            ))}
          </div>
        </div>
      )}

      {propPanelOpen && (
        <div className="dock-pop row" role="dialog" aria-label="Show an object to the camera">
          <video
            ref={propVideoRef}
            className="webcam-pip mirrored"
            style={{ display: capturedPropPreviewUrl ? 'none' : 'block' }}
            autoPlay
            muted
            playsInline
          />
          {capturedPropPreviewUrl && (
            <img src={capturedPropPreviewUrl} alt="Captured object snapshot" className="webcam-pip" />
          )}
          <input
            type="text"
            className="text-input"
            style={{ maxWidth: 200 }}
            value={propName}
            onChange={(e) => setPropName(e.target.value)}
            placeholder="Name this object"
            aria-label="Object name"
          />
          <div className="placement-chips" role="group" aria-label="Where to place it">
            {(
              [
                { id: 'table', label: 'Table' },
                { id: 'held', label: 'Held' },
                { id: 'floor', label: 'Floor' },
              ] as Array<{ id: PropPlacement; label: string }>
            ).map((opt) => (
              <button
                key={opt.id}
                type="button"
                className={`pill-btn subtle ${propPlacement === opt.id ? 'active' : ''}`}
                onClick={() => setPropPlacement(opt.id)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          {!capturedPropPreviewUrl ? (
            <button
              type="button"
              className="pill-btn"
              onClick={handleSnapPropPreview}
              disabled={creatingProp}
            >
              Snap
            </button>
          ) : (
            <button
              type="button"
              className="pill-btn"
              onClick={clearCapturedProp}
              disabled={creatingProp}
            >
              Retake
            </button>
          )}
          <button
            type="button"
            className="dock-btn primary"
            onClick={handleSubmitProp}
            disabled={creatingProp || !propName.trim()}
          >
            {creatingProp ? 'Creating' : 'Place on stage'}
          </button>
          <button
            type="button"
            className="icon-btn"
            aria-label="Close"
            title="Close"
            onClick={handleTogglePropPanel}
          >
            <IconClose />
          </button>
        </div>
      )}

      <form className="dock-row" onSubmit={handleTextSubmit}>
        <select
          className="dock-scene"
          value={targetSceneId}
          onChange={(e) => onTargetSceneChange(Number(e.target.value))}
          aria-label="Scene to rewrite"
          title="Scene to rewrite"
        >
          <option value={1}>Scene 1</option>
          <option value={2}>Scene 2</option>
          <option value={3}>Scene 3</option>
          <option value={4}>Scene 4</option>
        </select>

        <div className="dock-field">
          <input
            ref={textInputRef}
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Type what should change…"
            aria-label="What should change"
          />
          {!cleanCaptureMode && (
            <button
              type="button"
              className={`icon-btn ${examplesOpen ? 'on' : ''}`}
              aria-label="Examples"
              aria-expanded={examplesOpen}
              title="Examples"
              onClick={() => setExamplesOpen((v) => !v)}
            >
              <IconList />
            </button>
          )}
        </div>

        <button
          type="submit"
          className="dock-btn primary"
          disabled={submitting || !text.trim()}
          title="Rewrite the story from the selected scene"
        >
          <IconClapper />
          <span>{submitting ? 'Working' : 'Direct'}</span>
        </button>

        <button
          type="button"
          className={`dock-btn ${recording ? 'recording' : ''}`}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            setExamplesOpen(false);
            void handleStartHold();
          }}
          onKeyDown={(e) => {
            if ((e.key === ' ' || e.key === 'Enter') && !e.repeat) {
              e.preventDefault();
              e.stopPropagation();
              void handleStartHold();
            }
          }}
          onKeyUp={(e) => {
            if (e.key === ' ' || e.key === 'Enter') {
              e.preventDefault();
              e.stopPropagation();
              handleRelease();
            }
          }}
          onContextMenu={(e) => e.preventDefault()}
          style={{ touchAction: 'none' }}
          disabled={submitting}
          aria-pressed={recording}
          title="Press and hold to speak and gesture for up to 3 seconds"
        >
          <IconMic />
          <span>{recording ? `${(remainingMs / 1000).toFixed(1)}s` : 'Hold to talk'}</span>
        </button>

        {onCreateProp && (
          <button
            type="button"
            className={`dock-btn ${propPanelOpen ? 'on' : ''}`}
            onClick={handleTogglePropPanel}
            disabled={creatingProp}
            aria-expanded={propPanelOpen}
            title="Hold an object up to the camera and place it on the stage"
          >
            <IconCamera />
            <span>{creatingProp ? 'Creating' : 'Show an object'}</span>
          </button>
        )}
      </form>
    </section>
  );
};
