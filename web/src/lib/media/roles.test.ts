// Which attachment plays which part in the ordinary chat: pictures beyond what a model takes are kept and marked "not used", never dropped or blocking.
import test from 'node:test';
import assert from 'node:assert/strict';
import { autoAssign, evaluate, plainSlots } from './roles.ts';
import { portraitSizes } from './preset.ts';

const pics = (n: number) => Array.from({ length: n }, (_, i) => ({ key: `k${i + 1}`, kind: 'image' as const, name: `pic${i + 1}.png`, role: null as string | null, roleAuto: true }));

test('an editing model that takes 4 pictures: the first is the picture to change, the next three are references', () => {
  const slots = plainSlots('image', { needSource: false, canUseSource: true, vision: true, refs: 4, alias: 'FLUX' });
  assert.deepEqual(slots.map((s) => [s.input, s.max]), [['source', 1], ['ref', 3]]);
  const items = pics(3);
  autoAssign(items, slots);
  assert.deepEqual(items.map((i) => i.role), ['source', 'ref', 'ref']);
  const v = evaluate(items, slots, null);
  assert.deepEqual(Object.values(v.state), ['used', 'used', 'used']);
  assert.equal(v.problems.length, 0);
});

test('more pictures than the model takes: the extras are marked not used with the reason, and nothing blocks', () => {
  const slots = plainSlots('image', { needSource: false, canUseSource: true, vision: true, refs: 3, alias: 'Qwen Edit' });
  const items = pics(5);
  autoAssign(items, slots);
  const v = evaluate(items, slots, null);
  assert.deepEqual(items.map((i) => v.state[i.key]), ['used', 'used', 'used', 'unused', 'unused']);
  assert.match(v.why.k4, /Qwen Edit takes 3 pictures in all/);
  assert.equal(v.problems.length, 0);
  assert.match(v.notes.join(' '), /2 pictures are not used/);
});

test('a model that restyles from one picture takes exactly one; the second is not used and says why', () => {
  const slots = plainSlots('image', { needSource: false, canUseSource: true, vision: true, refs: 0, alias: 'Z' });
  assert.deepEqual(slots.map((s) => s.input), ['source']);
  const items = pics(2);
  autoAssign(items, slots);
  const v = evaluate(items, slots, null);
  assert.deepEqual(items.map((i) => v.state[i.key]), ['used', 'unused']);
  assert.match(v.why.k2, /Z takes one picture/);
});

test('a model that cannot use pictures: all are not used with the given reason', () => {
  const slots = plainSlots('image', { needSource: false, canUseSource: false, vision: true, refs: 0 });
  assert.deepEqual(slots, []);
  const items = pics(2);
  const v = evaluate(items, slots, null, { noSlot: { image: 'X makes pictures from words only.' } });
  assert.deepEqual(items.map((i) => v.state[i.key]), ['unused', 'unused']);
  assert.equal(v.why.k1, 'X makes pictures from words only.');
});

test('a chat model that cannot see pictures takes none; one that can reads up to ten and flags the eleventh', () => {
  assert.deepEqual(plainSlots('text', { needSource: false, canUseSource: false, vision: false }), []);
  const slots = plainSlots('text', { needSource: false, canUseSource: false, vision: true, alias: 'Qwen' });
  const items = pics(11);
  autoAssign(items, slots);
  const v = evaluate(items, slots, null);
  assert.equal(items.filter((i) => v.state[i.key] === 'used').length, 10);
  assert.equal(v.state.k11, 'unused');
});

test('a model that needs a source picture blocks only until one is attached', () => {
  const slots = plainSlots('image', { needSource: true, canUseSource: true, vision: true, refs: 3 });
  assert.equal(slots[0].min, 1);
  assert.equal(evaluate([], slots, null).problems.length, 1);
  const items = pics(1);
  autoAssign(items, slots);
  assert.equal(evaluate(items, slots, null).problems.length, 0);
});

test('portrait sizes: 720x1280 where valid, a larger 9:16 only when it fits the model and really is larger', () => {
  assert.deepEqual(portraitSizes({ min_side: 256, max_side: 2048, multiple: 16 }).map((s) => [s.w, s.h]), [[720, 1280], [1008, 1792]]);
  assert.deepEqual(portraitSizes({ min_side: 256, max_side: 2048, multiple: 8 }).map((s) => [s.w, s.h]), [[720, 1280], [1080, 1920]]);
  // a model limited to 1024 on a side cannot do 720x1280: its largest 9:16 is offered, labelled as such
  const small = portraitSizes({ min_side: 256, max_side: 1024, multiple: 8 });
  assert.equal(small.length, 1); assert.equal(small[0].h, 1024); assert.match(small[0].note, /largest/);
  for (const s of portraitSizes({ multiple: 16 })) assert.equal(s.w * 16, s.h * 9);
});
