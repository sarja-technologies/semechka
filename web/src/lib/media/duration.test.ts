import test from 'node:test';
import assert from 'node:assert/strict';
import { describePlan, fmtSeconds, listedDurations, planDuration, validFrames, type DurationSpec } from './duration.ts';

// the same models as backend/tests/test_video_duration.py
const spec = (frames_min: number, frames_max: number, frame_step: number, fps: number, extra: Partial<DurationSpec> = {}): DurationSpec => ({
  fps, fps_configurable: false, fps_range: [fps, fps], min: frames_min / fps, max: frames_max / fps, step: frame_step / fps, default: frames_min / fps, options: null, declared: false,
  frames_min, frames_max, frame_step, available: true, why: null, ...extra });
const WAN = spec(5, 129, 4, 16, { fps_configurable: true, fps_range: [8, 24] });
const LTX = spec(9, 241, 8, 24);

test('6 s on a 4n+1 model at 16 fps is 97 frames = 6.0625 s, shown as the actual length', () => {
  const p = planDuration(6, WAN)!;
  assert.deepEqual([p.frames, p.fps, p.seconds, p.adjusted], [97, 16, 6.0625, false]);
  assert.equal(describePlan(p, WAN), 'Will make 6.06 s');
});
test('8n+1 model at a fixed 24 fps: every typed value lands on a valid frame count within half a step', () => {
  for (let t = 0.5; t < 10; t += 0.37) {
    const p = planDuration(t, LTX)!;
    assert.equal((p.frames - 1) % 8, 0);
    assert.ok(Math.abs(p.seconds - t) <= 8 / 24 / 2 + 1e-9 || t > LTX.max);
  }
});
test('clamps to the model range and to the organisation limit, and says so', () => {
  assert.equal(planDuration(999, WAN)!.frames, 129);
  assert.equal(planDuration(0.1, WAN)!.frames, 5);
  assert.ok(planDuration(30, WAN, { maxSeconds: 5 })!.seconds <= 5);
  assert.match(describePlan(planDuration(999, WAN), WAN), /the longest this model makes/);
  assert.match(describePlan(planDuration(0.01, WAN), WAN), /the shortest this model makes/);
  assert.match(describePlan(planDuration(2.55, LTX), LTX), /nearest length/);
});
test('fps override stays valid and changes the seconds per frame', () => {
  const p = planDuration(4, WAN, { fps: 24 })!;
  assert.equal(p.fps, 24); assert.equal((p.frames - 1) % 4, 0); assert.ok(Math.abs(p.seconds - 4) < 0.1);
});
test('models that only support specific lengths snap to them and are listed', () => {
  const d = spec(9, 241, 8, 24, { options: [5, 10], declared: true });
  assert.equal(Math.round(planDuration(9, d)!.seconds), 10);
  assert.equal(Math.round(planDuration(6, d)!.seconds), 5);
  assert.deepEqual(listedDurations(d), [5, 10]);
  assert.equal(listedDurations(LTX), null);
});
test('nonsense and unavailable models give no plan (the form disables Generate instead of guessing)', () => {
  for (const bad of [0, -1, NaN, Infinity]) assert.equal(planDuration(bad, WAN), null);
  const off = spec(33, 65, 8, 24, { available: false, why: 'This model’s shortest clip is longer than your organisation’s 1 s limit.' });
  assert.equal(planDuration(2, off), null);
  assert.match(describePlan(null, off), /shortest clip/);
});
test('valid frames and formatting', () => {
  assert.deepEqual(validFrames(spec(9, 29, 4, 16)), [9, 13, 17, 21, 25, 29]);   // the server already starts at the first aligned count
  assert.equal(fmtSeconds(6), '6 s'); assert.equal(fmtSeconds(6.0625), '6.06 s'); assert.equal(fmtSeconds(2.5), '2.5 s');
});
