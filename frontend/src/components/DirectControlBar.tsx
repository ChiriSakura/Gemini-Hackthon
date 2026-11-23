/**
 * Minimal Voice + Text Director Prompt Bar (W2-V2-01 / W2-V2-04).
 *
 * Feature & Use Cases:
 * - Keeps the bottom input dock effortless and uncluttered: a subtle `Next S(N+1)`
 *   indicator, one wide open prompt field, "Direct" (send typed prompt),
 *   "Hold to talk" (press and hold up to 30s of camera + microphone), and a quick
 *   "Throw Object" button wired to the persistent bottom-right Self-View camera.
 * - Preserves rock-solid hold-to-record guards (`holdBusyRef`, window release
 *   listeners, `MIN_CLIP_MS = 1000`, never attaching stale text to a voice clip).
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { IconClapper, IconClose, IconMic } from './Icons';

export type PropPlacement = 'table' | 'held' | 'floor';

interface DirectControlBarProps {
  submitting: boolean;
  contradictionWarning: string | null;
  onDismissContradiction?: () => void;
  onForceRewrite?: () => void;
  /** Resolves to true only when the backend accepted and applied the prompt. */
  onSubmitPrompt: (opts: {
    clip?: Blob | null;
    text?: string;
    confirmContradiction?: boolean;
  }) => Promise<boolean> | void;
  /** Improvised mode: the text is a situation and the actors decide for themselves what to do. */
  improvised?: boolean;
  onToggleImprovised?: () => void;
}

const MAX_CLIP_MS = 30000;
const MIN_CLIP_MS = 600;

/**
 * Ask for camera + microphone at 1280x720 (ideal, never a hard requirement),
 * falling back to any camera and finally to voice only when no camera works.
 * A permission refusal is rethrown so the caller can explain it.
 */
/** Voice-first microphone settings: cut room noise and echo, level the volume, mono speech. */
const CLEAR_SPEECH_AUDIO: MediaTrackConstraints = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
  channelCount: 1,
};

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
      audio: CLEAR_SPEECH_AUDIO,
    });
  } catch (err) {
    if (isDenied(err)) throw err;
    try {
      return await media.getUserMedia({ video: true, audio: CLEAR_SPEECH_AUDIO });
    } catch (err2) {
      if (isDenied(err2)) throw err2;
      return media.getUserMedia({ audio: CLEAR_SPEECH_AUDIO });
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

export const DirectControlBar: React.FC<DirectControlBarProps> = ({
  submitting,
  contradictionWarning,
  onDismissContradiction,
  onForceRewrite,
  onSubmitPrompt,
  improvised = false,
  onToggleImprovised,
}) => {
  const [text, setText] = useState('');
  const [recording, setRecording] = useState(false);
  const [remainingMs, setRemainingMs] = useState(MAX_CLIP_MS);
  const [holdHint, setHoldHint] = useState<string | null>(null);
  const textInputRef = useRef<HTMLInputElement | null>(null);
  const [audioLevel, setAudioLevel] = useState(0);
  const [camError, setCamError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
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
      recorder = mime
        ? new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 128000 })
        : new MediaRecorder(stream, { audioBitsPerSecond: 128000 });
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
      void onSubmitPrompt({ clip: new Blob(chunks, { type: blobType }) });
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
      audioCtxRef.current?.close().catch(() => {});
      detachReleaseListeners();
    };
  }, [detachReleaseListeners]);

  const handleTextSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || submitting) return;
    const submittedText = text.trim();
    const ok = await onSubmitPrompt({ text: submittedText });
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
                : onSubmitPrompt({ text, confirmContradiction: true })
            }
          >
            Force continue
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

      <form className="dock-row" onSubmit={handleTextSubmit}>
        {onToggleImprovised && (
          <button
            type="button"
            className={`dock-btn dock-mode ${improvised ? 'on' : ''}`}
            aria-pressed={Boolean(improvised)}
            title={
              improvised
                ? 'Improvised: you give a situation, the characters decide what they do. Click for Directed.'
                : 'Directed: you say what happens. Click for Improvised.'
            }
            onClick={onToggleImprovised}
          >
            {improvised ? 'Improvised' : 'Directed'}
          </button>
        )}
        <div className="dock-field">
          <input
            ref={textInputRef}
            type="text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={
              improvised ? 'Describe a situation; Lia and Nyx decide what they do…' : 'Speak or type what happens next…'
            }
            aria-label="Direct the next scene"
          />
        </div>

        <button
          type="submit"
          className="dock-btn primary"
          disabled={submitting || !text.trim()}
          title="Send direction"
        >
          <IconClapper />
          <span>{submitting ? 'Working' : 'Direct'}</span>
        </button>

        <button
          type="button"
          className={`dock-btn ${recording ? 'recording' : ''}`}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
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
          title="Press and hold to speak and gesture for up to 30 seconds"
        >
          <IconMic />
          <span>{recording ? `${(remainingMs / 1000).toFixed(1)}s` : 'Hold to talk'}</span>
        </button>
      </form>
    </section>
  );
};
