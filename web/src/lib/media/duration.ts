// Video length in SECONDS for every video-generation form. A browser mirror of backend/semechka/video_duration.py (the server stays the authority: the form sends the
// ACTUAL length it displays, which the server maps back to the very same frame count). The vectors in duration.test.ts are the same ones the Python tests use.

export type DurationSpec = {
  fps: number; fps_configurable: boolean; fps_range: [number, number];
  min: number; max: number; step: number; default: number;
  options: number[] | null; declared: boolean;
  frames_min: number; frames_max: number; frame_step: number;
  available: boolean; why: string | null;
};
export type DurationPlan = { frames: number; fps: number; seconds: number; requested: number; adjusted: boolean };

/** Every frame count the model accepts at `fps`, cut to the organisation's longest clip. */
export function validFrames(spec: DurationSpec, fps: number = spec.fps, maxSeconds: number | null = null): number[] {
  const out: number[] = [];
  for (let f = spec.frames_min; f <= spec.frames_max; f += spec.frame_step) if (!maxSeconds || f / fps <= maxSeconds + 1e-9) out.push(f);
  return out;
}

/** The clip the model will really make for `seconds` (what the form shows next to the slider). */
export function planDuration(seconds: number, spec: DurationSpec, opts: { fps?: number | null; maxSeconds?: number | null } = {}): DurationPlan | null {
  if (!spec.available || !Number.isFinite(seconds) || seconds <= 0) return null;
  const fps = opts.fps && opts.fps > 0 ? opts.fps : spec.fps;
  const frames = validFrames(spec, fps, opts.maxSeconds ?? null);
  if (!frames.length) return null;
  let target = seconds;
  if (spec.declared && spec.options?.length && fps === spec.fps) {
    target = spec.options.reduce((best, o) => (Math.abs(o - seconds) < Math.abs(best - seconds) ? o : best), spec.options[0]);
  }
  const f = frames.reduce((best, c) => (Math.abs(c / fps - target) < Math.abs(best / fps - target) - 1e-12 ? c : best), frames[0]);
  const actual = f / fps;
  return { frames: f, fps, seconds: Math.round(actual * 10000) / 10000, requested: seconds, adjusted: Math.abs(actual - seconds) > 0.1 };
}

/** The lengths to offer as buttons: only when the model supports specific ones (declared, or few enough to list) at its own frame rate. */
export function listedDurations(spec: DurationSpec, fps: number | null = null): number[] | null {
  if (!spec.available || !spec.options?.length) return null;
  return !fps || fps === spec.fps || spec.declared ? spec.options : null;
}

/** "6 s", "2.06 s": two decimals only when they matter. */
export function fmtSeconds(s: number): string {
  const r = Math.round(s * 100) / 100;
  return `${Number.isInteger(r) ? r : r.toFixed(2).replace(/0$/, '')} s`;
}

/** What to tell the person about a length: the actual duration, and why it may differ from what they typed. */
export function describePlan(p: DurationPlan | null, spec: DurationSpec): string {
  if (!p) return spec.why ?? 'This model cannot make a clip within your limits.';
  const base = `Will make ${fmtSeconds(p.seconds)}`;
  if (p.requested > spec.max + 1e-9) return `${base}: the longest this model makes`;
  if (p.requested < spec.min - 1e-9) return `${base}: the shortest this model makes`;
  return p.adjusted ? `${base}: the nearest length this model supports` : base;
}
