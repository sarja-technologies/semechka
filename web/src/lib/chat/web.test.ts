import test from 'node:test';
import assert from 'node:assert/strict';
import { activityText, asMode, citeParts, WEB_MODES } from './web.ts';

test('markers become links only where the source exists; the rest of the text is untouched', () => {
  assert.deepEqual(citeParts('Fast [1], small [2] and unknown [7].', new Set([1, 2])), ['Fast ', 1, ', small ', 2, ' and unknown [7].']);
  assert.deepEqual(citeParts('[1][3]', new Set([1, 3])), [1, 3]);
  assert.equal(citeParts('No markers here.', new Set([1])), null);
  assert.equal(citeParts('Only [9].', new Set([1])), null);
  assert.equal(citeParts('Any [1].', new Set()), null);
  assert.equal(citeParts('array[1000] stays', new Set([1])), null); // not a citation number
});

test('status lines for what the system reports, and nothing for unknown states', () => {
  assert.equal(activityText({ state: 'searching_web', query: 'x' }), 'Searching the web…');
  assert.equal(activityText({ state: 'reading_sources' }), 'Reading sources…');
  assert.equal(activityText({ state: 'reading_sources', count: 1 }), 'Reading 1 source…');
  assert.equal(activityText({ state: 'reading_sources', count: 3 }), 'Reading 3 sources…');
  assert.equal(activityText({ state: 'verifying' }), 'Checking the answer…');
  assert.equal(activityText({ state: 'loading' }), '');
  assert.equal(activityText(null), '');
});

test('three modes, auto by default', () => {
  assert.deepEqual(WEB_MODES.map((m) => m.id), ['auto', 'on', 'off']);
  assert.equal(asMode(undefined), 'auto');
  assert.equal(asMode('always'), 'auto');
  assert.equal(asMode('off'), 'off');
});
