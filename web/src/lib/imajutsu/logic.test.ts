// Pure logic of the Imajutsu page: the selection document and its PNG text chunk, the adjustment recipe, the spec and the capabilities readers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { MAX_OPS, addOp, asBox, embedStrokes, emptyMask, fromBox, hasSelection, maskKey, readStrokes, thin } from './mask.ts';
import { cleanRecipe, describe, isNeutral, previewUrl, sizeAfter, turned } from './adjust.ts';
import { defaultSpec, normalizeSpec } from './spec.ts';
import { MODES, keyOf, modeState, needsOf, rolesFor, runnable } from './caps.ts';
import type { Caps, MaskDoc } from './types.ts';

// a real 1x1 PNG
const PNG = Uint8Array.from(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==', 'base64'));

test('a selection is empty until something is painted; erasing everything leaves nothing', () => {
  const d = emptyMask(800, 600);
  assert.equal(hasSelection(d), false);
  const a = addOp(d, { t: 'brush', size: 20, pts: [10, 10, 40, 40] });
  assert.equal(hasSelection(a), true);
  assert.equal(hasSelection(addOp(a, { t: 'erase', size: 50, pts: [0, 0] })), true, 'an eraser stroke alone does not tell the page that nothing is left');
  assert.equal(hasSelection(addOp(d, { t: 'rect', x: 0, y: 0, w: 10, h: 10, sub: true })), false);
});

test('the key follows the strokes and the size, nothing else', () => {
  const a = addOp(emptyMask(800, 600), { t: 'rect', x: 1, y: 2, w: 3, h: 4 });
  assert.equal(maskKey(a), maskKey({ ...a }));
  assert.notEqual(maskKey(a), maskKey(emptyMask(800, 600)));
  assert.notEqual(maskKey(a), maskKey({ ...a, w: 801 }));
});

test('the operations list is capped so the saved project stays small', () => {
  let d = emptyMask(10, 10);
  for (let i = 0; i < MAX_OPS + 25; i++) d = addOp(d, { t: 'brush', size: 1, pts: [i % 10, 1] });
  assert.equal(d.ops.length, MAX_OPS);
});

test('thinning keeps the ends and drops points that are too close', () => {
  const pts = [0, 0, 0.1, 0, 0.2, 0, 5, 0, 5.1, 0, 10, 0];
  assert.deepEqual(thin(pts, 1), [0, 0, 5, 0, 10, 0]);
  assert.deepEqual(thin([1, 2], 5), [1, 2]);
});

test('a single box is sent as a box (fractions of the picture) and comes back as the same box', () => {
  const d: MaskDoc = { w: 800, h: 600, ops: [{ t: 'rect', x: 200, y: 150, w: 400, h: 300 }] };
  const b = asBox(d)!;
  assert.deepEqual(b, { x: 0.25, y: 0.25, w: 0.5, h: 0.5 });
  assert.deepEqual(fromBox(b, 800, 600), d);
  assert.equal(asBox(addOp(d, { t: 'brush', size: 4, pts: [1, 1] })), null, 'a box plus a stroke is a picture, not a box');
  assert.equal(asBox({ ...d, ops: [{ t: 'rect', x: 1, y: 1, w: 5, h: 5, sub: true }] }), null);
});

test('the strokes ride inside the PNG and the picture is unchanged', () => {
  const d: MaskDoc = { w: 800, h: 600, ops: [{ t: 'brush', size: 24, pts: [1.5, 2.5, 30, 40] }, { t: 'rect', x: 5, y: 6, w: 7, h: 8, sub: true }] };
  const out = embedStrokes(PNG, d);
  assert.ok(out.length > PNG.length);
  assert.deepEqual([...out.subarray(0, 8)], [...PNG.subarray(0, 8)]);
  assert.deepEqual(readStrokes(out), d);
  assert.equal(readStrokes(PNG), null, 'a picture from elsewhere carries no strokes');
  assert.equal(readStrokes(new Uint8Array([1, 2, 3])), null);
  // the rest of the file (header, data, end) is byte for byte what it was
  const tail = out.subarray(out.length - (PNG.length - 33));
  assert.deepEqual([...tail], [...PNG.subarray(33)]);
});

test('a recipe keeps only what is not neutral, rounded, in the shape the server reads', () => {
  const r = cleanRecipe({ exposure: 0, contrast: 12.00001, saturation: -0.5, rotate: 360, flip: false, straighten: 0, crop: { x: 1.4, y: 2.6, w: 100.2, h: 80.7 }, output: { width: 800.2, height: 0 } });
  assert.deepEqual(r, { contrast: 12, saturation: -0.5, crop: { x: 1, y: 3, w: 100, h: 81 } });
  assert.equal(isNeutral({ exposure: 0, flip: false }), true);
  assert.equal(isNeutral({ exposure: 0.05 }), false);
  assert.equal(cleanRecipe({ rotate: -90 }).rotate, 270);
  assert.deepEqual(JSON.parse(decodeURIComponent(previewUrl('ma_1', { exposure: 1, tint: 0 }).split('recipe=')[1])), { exposure: 1 });
});

test('turning a quarter swaps the sides; the crop is measured on the turned picture', () => {
  assert.deepEqual(turned(800, 600, { rotate: 90 }), { width: 600, height: 800 });
  assert.deepEqual(turned(800, 600, { rotate: 180 }), { width: 800, height: 600 });
  assert.deepEqual(sizeAfter(800, 600, { rotate: 90 }), { width: 600, height: 800 });
  assert.deepEqual(sizeAfter(800, 600, { rotate: 90, crop: { x: 0, y: 0, w: 300, h: 400 } }), { width: 300, height: 400 });
  assert.deepEqual(sizeAfter(800, 600, { output: { width: 400, height: 300 } }), { width: 400, height: 300 });
  assert.deepEqual(describe({ exposure: 0.5, rotate: 90 }), ['Exposure +0.5 EV', 'Turned 90°']);
});

test('the spec reader fills in what the server does not keep and never invents a model or a picture', () => {
  const d = defaultSpec('iphone_photo');
  assert.equal(d.mode, 'iphone_photo'); assert.equal(d.model, null); assert.equal(d.source_asset, null);
  const s = normalizeSpec({ mode: 'edit', count: 9, references: [{ asset: 'ma_1', role: 'identity', preserve: 'scarf', active: true, label: '' }, { asset: '' } as any], size: { native: null, output: { width: 640, height: 480 } } } as any);
  assert.equal(s.count, 4);
  assert.equal(s.references.length, 1);
  assert.equal(s.references[0].role, 'identity');
  assert.deepEqual(s.size.output, { width: 640, height: 480 });
  assert.equal(s.selection.invert, false);
  assert.deepEqual(normalizeSpec(null).references, []);
});

const caps = (over: any = {}): Caps => ({
  modes: [
    { id: 'generate', label: 'Generate Image', status: 'ready', models: [{ model_id: 'm1', status: 'ready' }], recommended: { model_id: 'm1' } },
    { id: 'inpaint', label: 'Inpaint', status: 'unavailable', reason: 'No model.', models: [] },
    { id: 'edit', label: 'Edit Image', status: 'ready', models: [{ model_id: 'm2', status: 'unavailable', reason: 'x' }] },
    { id: 'upscale', label: 'Upscale', status: 'ready', models: [] }
  ], presets: [], aspects: [], limits: {}, roles: [{ id: 'identity' }, { id: 'style' }], ...over
});

test('a mode is operational only when the server says so and a model can run it; Upscale is scaling and needs none', () => {
  const c = caps();
  assert.equal(modeState(c, 'generate').ok, true);
  assert.equal(modeState(c, 'inpaint').ok, false);
  assert.equal(modeState(c, 'inpaint').reason, 'No model.');
  assert.equal(modeState(c, 'edit').ok, false, 'ready on paper but no model can do it');
  assert.equal(modeState(c, 'upscale').ok, true);
  assert.equal(modeState(c, 'outpaint').ok, false);
  assert.match(modeState(c, 'outpaint').reason, /does not offer/);
  assert.equal(modeState(null, 'generate').ok, false);
});

test('"available" (works but not benchmarked) can run a mode, "not_ready" cannot', () => {
  assert.equal(runnable('available'), true); assert.equal(runnable('experimental'), true); assert.equal(runnable('ready'), true);
  assert.equal(runnable('not_ready'), false); assert.equal(runnable('unavailable'), false); assert.equal(runnable(undefined), false);
});

test('the ten labels are fixed and map to the server\'s ids', () => {
  assert.deepEqual(MODES.map((m) => m.label), ['Generate Image', 'iPhone Photo', 'Edit Image', 'Replace Subject', 'Inpaint', 'Outpaint', 'Reference Image', 'Restyle', 'Upscale', 'Advanced Workflow']);
  assert.equal(keyOf({ id: 'iphone_photo', label: 'iPhone Photo' }), 'iphone_photo');
  assert.equal(keyOf({ id: 'x', label: 'Replace Subject' }), 'replace_subject');
});

test('what a mode needs: the server\'s own statement wins over the page\'s table', () => {
  assert.equal(needsOf('inpaint').selection, 'required');
  assert.equal(needsOf('generate').source, false);
  assert.equal(needsOf('edit', { id: 'edit', label: 'Edit Image', status: 'ready', models: [], needs: { source: true, references_ok: false } }).refs, 'none');
  assert.equal(needsOf('reference', { id: 'reference', label: 'x', status: 'ready', models: [], needs: { references_min: 1, references_ok: true, prompt: false } }).refs, 'required');
  assert.equal(needsOf('upscale').model, false);
});

test('only the roles the server lists are offered', () => {
  assert.deepEqual(rolesFor(caps()).map((r) => r.id), ['identity', 'style']);
  assert.deepEqual(rolesFor(caps({ roles: undefined })), []);
  assert.equal(rolesFor(caps())[0].label, 'Identity');
});
