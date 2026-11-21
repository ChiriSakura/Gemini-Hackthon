/**
 * Persistent Bottom-Right Live Camera Self-View & 30s Throw-In Capture (W2-V2-02).
 *
 * Feature & Use Cases:
 * - Renders a sleek, persistent mirrored (`scaleX(-1)`) bottom-right live
 *   Self-View camera window (`224px` glass card, gracefully collapsible with
 *   permission retry if blocked).
 * - Activates a 30-second Throw-In Capture overlay with a golden countdown
 *   border and center target reticle whenever `/api/director/perceive` returns
 *   `needs_prop_prompt = true` OR the user clicks `Throw Object` on the corner
 *   camera, turning a tossed everyday item (drink, snack, laptop, mug) into a
 *   2D anime prop flying into the stage (`detect_throw_peak=true`, `entry_motion=throw_arc`).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { PropPlacement } from './DirectControlBar';

const MAX_THROW_MS = 30000;

export interface ThrowObjectSubmission {
  name: string;
  mediaBlob: Blob | null;
  /** Quick PNG frame captured at submit time for <100ms optimistic 2.5D preview (W2-V3-04). */
  previewThumbBlob?: Blob | null;
  placement: PropPlacement;
  entryMotion: 'throw_arc' | 'place' | 'held';
  detectThrowPeak: boolean;
}

interface CameraSelfViewProps {
  throwOpen: boolean;
  throwHint: string | null;
  suggestedPropName: string | null;
  creatingProp: boolean;
  /** True when `/api/director/perceive` requested an object before extending. */
  promptedByStory: boolean;
  cleanCaptureMode?: boolean;
  onRequestOpenThrow: () => void;
  onCancelThrow: () => void;
  onSkipThrowAndContinue?: () => void;
  onSubmitThrow: (payload: ThrowObjectSubmission) => void;
}

function pickVideoMime(): string | undefined {
  if (typeof MediaRecorder === 'undefined') return undefined;
  const candidates = ['video/webm;codecs=vp8', 'video/webm;codecs=vp9', 'video/webm'];
  return candidates.find((m) => MediaRecorder.isTypeSupported(m));
}

function captureStillPng(video: HTMLVideoElement | null): Promise<Blob | null> {
  return new Promise((resolve) => {
    if (!video || video.videoWidth <= 0 || video.videoHeight <= 0) {
      resolve(null);
      return;
    }
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    if (!ctx) {
      resolve(null);
      return;
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((blob) => resolve(blob), 'image/png');
  });
}

export const CameraSelfView: React.FC<CameraSelfViewProps> = ({
  throwOpen,
  throwHint,
  suggestedPropName,
  creatingProp,
  promptedByStory,
  cleanCaptureMode = false,
  onRequestOpenThrow,
  onCancelThrow,
  onSkipThrowAndContinue,
  onSubmitThrow,
}) => {
  const [camStatus, setCamStatus] = useState<'idle' | 'ready' | 'blocked' | 'unavailable'>('idle');
  const [camMessage, setCamMessage] = useState<string | null>(null);
  const [propName, setPropName] = useState('');
  // W2-V2-02 (15:06 rule): Default captured objects to resting on table (`entry_motion='place'`).
  // Only switch to `held` (`entry_motion='throw_arc'`) when story explicitly asks to hand/toss to Lia
  // or user explicitly selects "Toss to Lia".
  const [placement, setPlacement] = useState<PropPlacement>('table');
  const [remainingMs, setRemainingMs] = useState(MAX_THROW_MS);
  const [recordingThrow, setRecordingThrow] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const countdownTimerRef = useRef<number | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  const submitPendingRef = useRef(false);
  const propNameRef = useRef(propName);
  const placementRef = useRef(placement);

  useEffect(() => {
    propNameRef.current = propName;
  }, [propName]);

  useEffect(() => {
    placementRef.current = placement;
  }, [placement]);

  // W2-V3-01: Default to `table` (`entry_motion='place'`). ONLY auto-select `held` (`throw_arc`)
  // when the story explicitly asks to hand/give/toss/throw/catch an object to Lia.
  useEffect(() => {
    if (throwOpen) {
      const shouldAutoToss = Boolean(
        promptedByStory && /hand|give|toss|throw|catch/i.test(throwHint ?? ''),
      );
      setPlacement(shouldAutoToss ? 'held' : 'table');
      if (suggestedPropName && suggestedPropName.trim()) {
        setPropName(suggestedPropName.trim());
      } else {
        setPropName('');
      }
    }
  }, [throwOpen, suggestedPropName, promptedByStory, throwHint]);

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const startCamera = useCallback(async (): Promise<MediaStream | null> => {
    if (streamRef.current && streamRef.current.active) {
      if (videoRef.current && videoRef.current.srcObject !== streamRef.current) {
        videoRef.current.srcObject = streamRef.current;
      }
      setCamStatus('ready');
      setCamMessage(null);
      return streamRef.current;
    }
    const media = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
    if (!media?.getUserMedia) {
      setCamStatus('unavailable');
      setCamMessage('Camera unavailable in this browser.');
      return null;
    }
    try {
      let stream: MediaStream;
      try {
        stream = await media.getUserMedia({
          video: { width: { ideal: 1280 }, height: { ideal: 720 }, facingMode: 'user' },
          audio: false,
        });
      } catch (err) {
        const name = (err as { name?: string } | null)?.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') throw err;
        stream = await media.getUserMedia({ video: true, audio: false });
      }
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
      }
      setCamStatus('ready');
      setCamMessage(null);
      return stream;
    } catch (err) {
      const name = (err as { name?: string } | null)?.name;
      if (name === 'NotAllowedError' || name === 'SecurityError') {
        setCamStatus('blocked');
        setCamMessage('Camera blocked — click retry to allow.');
      } else {
        setCamStatus('unavailable');
        setCamMessage('No camera detected.');
      }
      return null;
    }
  }, []);

  // Boot live self-view automatically on mount
  useEffect(() => {
    void startCamera();
    return () => {
      if (countdownTimerRef.current !== null) window.clearInterval(countdownTimerRef.current);
      if (stopTimerRef.current !== null) window.clearTimeout(stopTimerRef.current);
      const rec = recorderRef.current;
      if (rec && rec.state !== 'inactive') {
        rec.onstop = null;
        rec.stop();
      }
      stopCamera();
    };
  }, [startCamera, stopCamera]);

  // Re-attach live stream to video element when status changes
  useEffect(() => {
    if (videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, [camStatus]);

  const stopThrowRecorder = useCallback((shouldSubmit: boolean) => {
    if (countdownTimerRef.current !== null) {
      window.clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    if (stopTimerRef.current !== null) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
    submitPendingRef.current = shouldSubmit;
    const rec = recorderRef.current;
    if (rec && rec.state === 'recording') {
      rec.stop();
      return;
    }
    setRecordingThrow(false);
    setRemainingMs(MAX_THROW_MS);
    if (shouldSubmit) {
      const chosenPlacement = placementRef.current;
      const resolvedMotion = chosenPlacement === 'held' ? 'throw_arc' : 'place';
      void captureStillPng(videoRef.current).then((still) => {
        onSubmitThrow({
          name: propNameRef.current.trim() || 'Stage Object',
          mediaBlob: still,
          previewThumbBlob: still,
          placement: chosenPlacement,
          entryMotion: resolvedMotion,
          detectThrowPeak: true,
        });
      });
    }
  }, [onSubmitThrow]);

  // Start 30s Throw-In recording automatically when throwOpen becomes true
  useEffect(() => {
    if (!throwOpen) {
      if (recorderRef.current && recorderRef.current.state === 'recording') {
        submitPendingRef.current = false;
        recorderRef.current.stop();
      }
      if (countdownTimerRef.current !== null) {
        window.clearInterval(countdownTimerRef.current);
        countdownTimerRef.current = null;
      }
      if (stopTimerRef.current !== null) {
        window.clearTimeout(stopTimerRef.current);
        stopTimerRef.current = null;
      }
      setRecordingThrow(false);
      setRemainingMs(MAX_THROW_MS);
      return;
    }

    let cancelled = false;
    void startCamera().then((stream) => {
      if (cancelled || !stream) return;
      const mime = pickVideoMime();
      chunksRef.current = [];
      const startedAt = Date.now();
      setRemainingMs(MAX_THROW_MS);

      countdownTimerRef.current = window.setInterval(() => {
        setRemainingMs(Math.max(0, MAX_THROW_MS - (Date.now() - startedAt)));
      }, 100);

      if (typeof MediaRecorder !== 'undefined') {
        try {
          const rec = mime
            ? new MediaRecorder(stream, { mimeType: mime })
            : new MediaRecorder(stream);
          recorderRef.current = rec;
          rec.ondataavailable = (ev) => {
            if (ev.data.size > 0) chunksRef.current.push(ev.data);
          };
          rec.onstop = () => {
            const shouldSend = submitPendingRef.current;
            submitPendingRef.current = false;
            recorderRef.current = null;
            setRecordingThrow(false);
            setRemainingMs(MAX_THROW_MS);
            if (!shouldSend) return;
            const chunks = chunksRef.current;
            chunksRef.current = [];
            const chosenPlacement = placementRef.current;
            const resolvedMotion = chosenPlacement === 'held' ? 'throw_arc' : 'place';
            void captureStillPng(videoRef.current).then((still) => {
              if (chunks.length > 0) {
                const clipBlob = new Blob(chunks, { type: rec.mimeType || mime || 'video/webm' });
                onSubmitThrow({
                  name: propNameRef.current.trim() || 'Stage Object',
                  mediaBlob: clipBlob,
                  previewThumbBlob: still,
                  placement: chosenPlacement,
                  entryMotion: resolvedMotion,
                  detectThrowPeak: true,
                });
              } else {
                onSubmitThrow({
                  name: propNameRef.current.trim() || 'Stage Object',
                  mediaBlob: still,
                  previewThumbBlob: still,
                  placement: chosenPlacement,
                  entryMotion: resolvedMotion,
                  detectThrowPeak: true,
                });
              }
            });
          };
          rec.start(250);
          setRecordingThrow(true);
        } catch {
          setRecordingThrow(false);
        }
      }

      stopTimerRef.current = window.setTimeout(() => {
        stopThrowRecorder(true);
      }, MAX_THROW_MS);
    });

    return () => {
      cancelled = true;
    };
  }, [throwOpen, startCamera, stopThrowRecorder, onSubmitThrow]);

  const progressPct = Math.round((remainingMs / MAX_THROW_MS) * 100);

  return (
    <aside
      className={`selfview-card ${throwOpen ? 'throwing' : ''} ${cleanCaptureMode && !throwOpen ? 'compact' : ''}`}
      aria-label="Live camera self-view"
    >
      <div
        className="selfview-video-wrap"
        role="button"
        tabIndex={0}
        aria-label={
          throwOpen
            ? 'Capturing object in self-view camera'
            : camStatus === 'ready'
              ? 'Click camera to capture an object for the stage'
              : 'Click to allow camera access'
        }
        title={
          throwOpen
            ? `Capturing (${(remainingMs / 1000).toFixed(0)}s left)`
            : camStatus === 'ready'
              ? 'Click camera to capture or toss an object'
              : 'Click to enable camera'
        }
        style={{ cursor: creatingProp ? 'default' : 'pointer' }}
        onClick={() => {
          if (creatingProp) return;
          if (camStatus !== 'ready') {
            void startCamera();
            return;
          }
          if (!throwOpen) {
            onRequestOpenThrow();
          }
        }}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && !creatingProp) {
            e.preventDefault();
            if (camStatus !== 'ready') void startCamera();
            else if (!throwOpen) onRequestOpenThrow();
          }
        }}
      >
        <video
          ref={videoRef}
          className="selfview-video"
          autoPlay
          muted
          playsInline
          style={{ display: camStatus === 'ready' ? 'block' : 'none' }}
        />

        <div className="selfview-overlay-label" aria-hidden="true">
          <span className={`selfview-dot ${camStatus === 'ready' ? 'on' : ''} ${recordingThrow ? 'rec' : ''}`} />
          <span>
            {creatingProp
              ? 'Stylizing…'
              : throwOpen
                ? `${(remainingMs / 1000).toFixed(0)}s`
                : 'Click to capture'}
          </span>
        </div>

        {camStatus !== 'ready' && (
          <div className="selfview-fallback">
            <span>{camMessage ?? 'Starting camera…'}</span>
          </div>
        )}

        {throwOpen && (
          <>
            <div className="selfview-reticle" aria-hidden="true">
              <span className="reticle-corner tl" />
              <span className="reticle-corner tr" />
              <span className="reticle-corner bl" />
              <span className="reticle-corner br" />
              <span className="reticle-label">Hold or toss object here</span>
            </div>
            <div className="selfview-countdown-bar" aria-hidden="true">
              <div style={{ width: `${progressPct}%` }} />
            </div>
          </>
        )}
      </div>

      {throwOpen && (
        <div className="selfview-throw-controls">
          {throwHint && <div className="selfview-hint">{throwHint}</div>}

          <div className="selfview-row">
            <input
              type="text"
              className="selfview-input"
              value={propName}
              onChange={(e) => setPropName(e.target.value)}
              placeholder="Object (e.g. Iced Tea, Snack, Mug)"
              aria-label="Thrown object name"
            />
          </div>

          <div className="selfview-row spread">
            <div className="selfview-chips" role="group" aria-label="Object placement and motion">
              {(
                [
                  { id: 'table', label: 'Rest on Table' },
                  { id: 'floor', label: 'Rest on Floor' },
                  { id: 'held', label: 'Hand / Toss to Lia' },
                ] as Array<{ id: PropPlacement; label: string }>
              ).map((opt) => (
                <button
                  key={opt.id}
                  type="button"
                  className={`selfview-chip ${placement === opt.id ? 'active' : ''}`}
                  onClick={() => setPlacement(opt.id)}
                >
                  {opt.label}
                </button>
              ))}
            </div>

            <div className="selfview-actions">
              {promptedByStory && onSkipThrowAndContinue && (
                <button
                  type="button"
                  className="selfview-mini-btn"
                  disabled={creatingProp}
                  onClick={() => {
                    stopThrowRecorder(false);
                    onSkipThrowAndContinue();
                  }}
                >
                  Skip
                </button>
              )}
              <button
                type="button"
                className="selfview-mini-btn"
                disabled={creatingProp}
                onClick={() => {
                  stopThrowRecorder(false);
                  onCancelThrow();
                }}
              >
                Cancel
              </button>
              <button
                type="button"
                className="selfview-mini-btn primary"
                disabled={creatingProp}
                onClick={() => stopThrowRecorder(true)}
              >
                {creatingProp
                  ? 'Sending…'
                  : placement === 'held'
                    ? 'Toss to Lia'
                    : 'Place on Stage'}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
