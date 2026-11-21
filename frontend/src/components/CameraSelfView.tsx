/**
 * Bottom-Right Camera Self-View & Throw-In Capture (W2-V2-02).
 *
 * Feature & Use Cases:
 * - The camera is OFF by default: nothing asks for camera permission on page
 *   load. "Turn camera on" requests access and shows a mirrored
 *   (`scaleX(-1)`) self-view; "Turn camera off" stops every track (so the
 *   hardware light goes out) and clears the video element.
 * - Recording never starts by itself. Opening the capture panel (from the
 *   dock's Throw Object button, the card's own Capture button, or when
 *   `/api/director/perceive` returns `needs_prop_prompt = true`) only turns the
 *   camera on. The user presses "Start" to begin recording, and only then does
 *   the countdown run. "Stop" ends early and submits; reaching
 *   `MAX_RECORDING_MS` stops and submits automatically. Clips shorter than
 *   `MIN_RECORDING_MS` are discarded with a short hint.
 * - A denied permission or a missing camera shows a message with a retry
 *   button and never throws.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { PropPlacement } from './DirectControlBar';

/** Longest clip the user can record after pressing Start. */
const MAX_RECORDING_MS = 30000;
/** Clips shorter than this are discarded instead of being submitted. */
const MIN_RECORDING_MS = 1000;

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

type CameraStatus = 'off' | 'starting' | 'ready' | 'blocked' | 'unavailable';

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
  const [camStatus, setCamStatus] = useState<CameraStatus>('off');
  const [camMessage, setCamMessage] = useState<string | null>(null);
  const [captureHint, setCaptureHint] = useState<string | null>(null);
  const [propName, setPropName] = useState('');
  // W2-V2-02 (15:06 rule): Default captured objects to resting on table (`entry_motion='place'`).
  // Only switch to `held` (`entry_motion='throw_arc'`) when story explicitly asks to hand/toss to Lia
  // or user explicitly selects "Toss to Lia".
  const [placement, setPlacement] = useState<PropPlacement>('table');
  const [remainingMs, setRemainingMs] = useState(MAX_RECORDING_MS);
  const [recordingThrow, setRecordingThrow] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // True while the user wants the camera on; a late permission grant is dropped otherwise
  const cameraWantedRef = useRef(false);
  const pendingStartRef = useRef<Promise<MediaStream | null> | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const recordStartedAtRef = useRef(0);
  const countdownTimerRef = useRef<number | null>(null);
  const stopTimerRef = useRef<number | null>(null);
  const submitPendingRef = useRef(false);
  const propNameRef = useRef(propName);
  const placementRef = useRef(placement);
  // Always call the latest submit handler, even from a recorder started many renders ago
  const onSubmitThrowRef = useRef(onSubmitThrow);

  useEffect(() => {
    propNameRef.current = propName;
  }, [propName]);

  useEffect(() => {
    placementRef.current = placement;
  }, [placement]);

  useEffect(() => {
    onSubmitThrowRef.current = onSubmitThrow;
  }, [onSubmitThrow]);

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

  const clearRecordingTimers = useCallback(() => {
    if (countdownTimerRef.current !== null) {
      window.clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    if (stopTimerRef.current !== null) {
      window.clearTimeout(stopTimerRef.current);
      stopTimerRef.current = null;
    }
  }, []);

  /** Stop the recorder; the clip is submitted only when `shouldSubmit` is true. */
  const finishRecording = useCallback(
    (shouldSubmit: boolean) => {
      clearRecordingTimers();
      submitPendingRef.current = shouldSubmit;
      const rec = recorderRef.current;
      if (rec && rec.state !== 'inactive') {
        rec.stop();
        return;
      }
      recorderRef.current = null;
      submitPendingRef.current = false;
      setRecordingThrow(false);
      setRemainingMs(MAX_RECORDING_MS);
    },
    [clearRecordingTimers],
  );

  /** Stop every track so the hardware light goes out, and clear the video element. */
  const releaseCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (videoRef.current) {
      videoRef.current.srcObject = null;
    }
  }, []);

  const turnCameraOff = useCallback(() => {
    cameraWantedRef.current = false;
    finishRecording(false);
    releaseCamera();
    setCamStatus('off');
    setCamMessage(null);
  }, [finishRecording, releaseCamera]);

  /** Ask for the camera. Resolves to null (never throws) when it cannot be used. */
  const turnCameraOn = useCallback((): Promise<MediaStream | null> => {
    cameraWantedRef.current = true;
    if (streamRef.current && streamRef.current.active) {
      if (videoRef.current && videoRef.current.srcObject !== streamRef.current) {
        videoRef.current.srcObject = streamRef.current;
      }
      setCamStatus('ready');
      setCamMessage(null);
      return Promise.resolve(streamRef.current);
    }
    // A second press while the permission prompt is open reuses the same request
    if (pendingStartRef.current) return pendingStartRef.current;

    const media = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
    if (!media?.getUserMedia) {
      setCamStatus('unavailable');
      setCamMessage('Camera unavailable in this browser.');
      return Promise.resolve(null);
    }

    setCamStatus('starting');
    setCamMessage(null);
    const request = (async (): Promise<MediaStream | null> => {
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
        if (!cameraWantedRef.current) {
          // Turned off (or unmounted) while the permission prompt was open
          stream.getTracks().forEach((t) => t.stop());
          return null;
        }
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
        }
        setCamStatus('ready');
        setCamMessage(null);
        return stream;
      } catch (err) {
        if (!cameraWantedRef.current) return null;
        const name = (err as { name?: string } | null)?.name;
        if (name === 'NotAllowedError' || name === 'SecurityError') {
          setCamStatus('blocked');
          setCamMessage('Camera blocked — click retry to allow.');
        } else {
          setCamStatus('unavailable');
          setCamMessage('No camera detected.');
        }
        return null;
      } finally {
        pendingStartRef.current = null;
      }
    })();
    pendingStartRef.current = request;
    return request;
  }, []);

  // Nothing is requested on mount; on unmount stop the recorder and all tracks
  useEffect(() => {
    return () => {
      cameraWantedRef.current = false;
      clearRecordingTimers();
      const rec = recorderRef.current;
      if (rec && rec.state !== 'inactive') {
        rec.onstop = null;
        rec.stop();
      }
      recorderRef.current = null;
      releaseCamera();
    };
  }, [clearRecordingTimers, releaseCamera]);

  // Attach the live stream once the <video> is showing
  useEffect(() => {
    if (camStatus === 'ready' && videoRef.current && streamRef.current) {
      videoRef.current.srcObject = streamRef.current;
    }
  }, [camStatus]);

  const submitCapture = useCallback((mediaBlob: Blob | null, still: Blob | null) => {
    const chosenPlacement = placementRef.current;
    onSubmitThrowRef.current({
      name: propNameRef.current.trim() || 'Stage Object',
      mediaBlob: mediaBlob ?? still,
      previewThumbBlob: still,
      placement: chosenPlacement,
      entryMotion: chosenPlacement === 'held' ? 'throw_arc' : 'place',
      detectThrowPeak: true,
    });
  }, []);

  /** Begin recording. Runs only from the Start button; the countdown starts here. */
  const startRecording = useCallback(() => {
    const stream = streamRef.current;
    if (!stream || !stream.active || recorderRef.current) return;
    setCaptureHint(null);

    if (typeof MediaRecorder === 'undefined') {
      // No recorder in this browser: send one still frame instead of a clip
      void captureStillPng(videoRef.current).then((still) => {
        if (still) submitCapture(null, still);
        else setCaptureHint('Could not capture from the camera — try again.');
      });
      return;
    }

    const mime = pickVideoMime();
    let rec: MediaRecorder;
    try {
      rec = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream);
    } catch {
      setCaptureHint('Recording is not supported in this browser.');
      return;
    }

    chunksRef.current = [];
    submitPendingRef.current = false;
    recorderRef.current = rec;
    rec.ondataavailable = (ev) => {
      if (ev.data.size > 0) chunksRef.current.push(ev.data);
    };
    rec.onerror = () => {
      setCaptureHint('Recording failed — press Start to try again.');
      finishRecording(false);
    };
    rec.onstop = () => {
      const shouldSend = submitPendingRef.current;
      const durationMs = Date.now() - recordStartedAtRef.current;
      const chunks = chunksRef.current;
      submitPendingRef.current = false;
      chunksRef.current = [];
      recorderRef.current = null;
      clearRecordingTimers();
      setRecordingThrow(false);
      setRemainingMs(MAX_RECORDING_MS);
      if (!shouldSend) return;
      if (durationMs < MIN_RECORDING_MS || chunks.length === 0) {
        setCaptureHint(
          `Clip too short — record for at least ${Math.round(MIN_RECORDING_MS / 1000)} second.`,
        );
        return;
      }
      const clipBlob = new Blob(chunks, { type: rec.mimeType || mime || 'video/webm' });
      void captureStillPng(videoRef.current).then((still) => submitCapture(clipBlob, still));
    };

    recordStartedAtRef.current = Date.now();
    setRemainingMs(MAX_RECORDING_MS);
    rec.start(250);
    setRecordingThrow(true);
    countdownTimerRef.current = window.setInterval(() => {
      setRemainingMs(
        Math.max(0, MAX_RECORDING_MS - (Date.now() - recordStartedAtRef.current)),
      );
    }, 100);
    // Reaching the limit stops and submits automatically
    stopTimerRef.current = window.setTimeout(() => {
      finishRecording(true);
    }, MAX_RECORDING_MS);
  }, [clearRecordingTimers, finishRecording, submitCapture]);

  // Opening the capture panel only turns the camera on and waits for Start;
  // closing it discards any recording in progress. The camera itself stays as it is.
  useEffect(() => {
    if (!throwOpen) {
      finishRecording(false);
      setCaptureHint(null);
      return;
    }
    void turnCameraOn();
  }, [throwOpen, turnCameraOn, finishRecording]);

  const cameraOn = camStatus === 'ready';
  const progressPct = Math.round((remainingMs / MAX_RECORDING_MS) * 100);
  const limitSec = Math.round(MAX_RECORDING_MS / 1000);
  const overlayText = creatingProp
    ? 'Stylizing…'
    : recordingThrow
      ? `${(remainingMs / 1000).toFixed(0)}s`
      : camStatus === 'starting'
        ? 'Starting camera…'
        : cameraOn
          ? throwOpen
            ? 'Press Start'
            : 'Camera on'
          : 'Camera off';

  return (
    <aside
      className={`selfview-card ${throwOpen ? 'throwing' : ''} ${cleanCaptureMode && !throwOpen ? 'compact' : ''}`}
      aria-label="Camera self-view"
    >
      <div className="selfview-video-wrap">
        <video
          ref={videoRef}
          className="selfview-video"
          autoPlay
          muted
          playsInline
          style={{ display: cameraOn ? 'block' : 'none' }}
        />

        <div className="selfview-overlay-label" aria-hidden="true">
          <span className={`selfview-dot ${cameraOn ? 'on' : ''} ${recordingThrow ? 'rec' : ''}`} />
          <span>{overlayText}</span>
        </div>

        {!cameraOn && (
          <div className="selfview-fallback">
            <span>
              {camStatus === 'starting'
                ? 'Waiting for camera permission…'
                : (camMessage ?? 'Camera is off.')}
            </span>
          </div>
        )}

        {throwOpen && cameraOn && (
          <div className="selfview-reticle" aria-hidden="true">
            <span className="reticle-corner tl" />
            <span className="reticle-corner tr" />
            <span className="reticle-corner bl" />
            <span className="reticle-corner br" />
            <span className="reticle-label">Hold or toss object here</span>
          </div>
        )}
        {recordingThrow && (
          <div className="selfview-countdown-bar" aria-hidden="true">
            <div style={{ width: `${progressPct}%` }} />
          </div>
        )}
      </div>

      <div className="selfview-cam-row">
        {cameraOn ? (
          <button
            type="button"
            className="selfview-mini-btn"
            onClick={turnCameraOff}
            title="Stop the camera and release it"
          >
            Turn camera off
          </button>
        ) : (
          <button
            type="button"
            className="selfview-mini-btn accent"
            disabled={camStatus === 'starting'}
            onClick={() => void turnCameraOn()}
            title="Ask for camera permission and show the self-view"
          >
            {camStatus === 'starting'
              ? 'Starting…'
              : camStatus === 'blocked' || camStatus === 'unavailable'
                ? 'Retry camera'
                : 'Turn camera on'}
          </button>
        )}
        {cameraOn && !throwOpen && (
          <button
            type="button"
            className="selfview-mini-btn accent"
            disabled={creatingProp}
            onClick={onRequestOpenThrow}
            title="Open the capture panel; recording starts only when you press Start"
          >
            Capture object
          </button>
        )}
      </div>

      {throwOpen && (
        <div className="selfview-throw-controls">
          {throwHint && <div className="selfview-hint">{throwHint}</div>}
          {captureHint && (
            <div className="selfview-hint" role="status">
              {captureHint}
            </div>
          )}

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
                    finishRecording(false);
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
                  finishRecording(false);
                  onCancelThrow();
                }}
              >
                Cancel
              </button>
              {recordingThrow ? (
                <button
                  type="button"
                  className="selfview-mini-btn primary"
                  onClick={() => finishRecording(true)}
                  title="Stop recording and send the clip"
                >
                  Stop
                </button>
              ) : (
                <button
                  type="button"
                  className="selfview-mini-btn primary"
                  disabled={creatingProp || !cameraOn}
                  onClick={startRecording}
                  title={
                    cameraOn
                      ? `Start recording (up to ${limitSec}s); press Stop to send`
                      : 'Turn the camera on first'
                  }
                >
                  {creatingProp ? 'Sending…' : 'Start'}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </aside>
  );
};
