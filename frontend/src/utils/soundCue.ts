/**
 * Zero-Dependency Web Audio UI Confirmation Chime (W2-T8E).
 *
 * Feature & Use Cases:
 * - Synthesizes two soft harmonic sine notes (<0.25s total) when a live timeline
 *   fork or Omni Flash render completes.
 * - Strictly gated on the presenter's `soundEnabled` preference and wrapped in
 *   non-blocking try/catch so audio autoplay restrictions never throw errors.
 */

let sharedAudioCtx: AudioContext | null = null;

/**
 * Play a subtle two-note harmonic confirmation chime when `soundEnabled` is true.
 */
export function playCompletionChime(soundEnabled: boolean): void {
  if (!soundEnabled || typeof window === 'undefined') return;
  try {
    const AudioCtx =
      window.AudioContext ||
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AudioCtx) return;

    if (!sharedAudioCtx || sharedAudioCtx.state === 'closed') {
      sharedAudioCtx = new AudioCtx();
    }
    if (sharedAudioCtx.state === 'suspended') {
      sharedAudioCtx.resume().catch(() => {});
    }

    const now = sharedAudioCtx.currentTime;
    const notes = [523.25, 783.99]; // Soft C5 -> G5 fifth interval

    notes.forEach((freq, idx) => {
      if (!sharedAudioCtx) return;
      const osc = sharedAudioCtx.createOscillator();
      const gain = sharedAudioCtx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq, now + idx * 0.085);

      gain.gain.setValueAtTime(0.0001, now + idx * 0.085);
      gain.gain.exponentialRampToValueAtTime(0.055, now + idx * 0.085 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + idx * 0.085 + 0.14);

      osc.connect(gain);
      gain.connect(sharedAudioCtx.destination);
      osc.start(now + idx * 0.085);
      osc.stop(now + idx * 0.085 + 0.15);
    });
  } catch {
    // Non-blocking audio guard
  }
}
