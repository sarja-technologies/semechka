import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ReasonRegister, activeFilterCount, appendPage, dayStart, fmtDur, fmtSize, paramRows, paramsLine, reasonOk, statusTone, toApiFilters, workflowLabel } from './explorer.ts';

test('filters keep only what is set and turn dates into a half-open local day range', () => {
  const f = toApiFilters({ user_id: 'u_1', model_id: '', status: 'failed', q: '  harbour ', since: '2026-11-01', until: '2026-11-02' });
  assert.deepEqual(Object.keys(f).sort(), ['q', 'since', 'status', 'until', 'user_id']);
  assert.equal(f.q, 'harbour');
  assert.equal((f.until as number) - (f.since as number), 2 * 86400);
});
test('a date that does not exist is ignored, not shifted', () => {
  assert.equal(dayStart('2026-02-31'), null);
  assert.equal(dayStart('yesterday'), null);
  assert.equal(dayStart(''), null);
  assert.deepEqual(toApiFilters({ since: '2026-13-01' }), {});
});
test('active filter count ignores blanks', () => {
  assert.equal(activeFilterCount({ user_id: 'a', q: ' ', status: '' }), 1);
});
test('reasons need ten characters and at most five hundred', () => {
  assert.equal(reasonOk('too short'), false);
  assert.equal(reasonOk('Legal hold 2026-114'), true);
  assert.equal(reasonOk('x'.repeat(501)), false);
});
test('status tones pair a colour with a label elsewhere; unknown statuses are neutral', () => {
  assert.equal(statusTone('failed'), 'bad');
  assert.equal(statusTone('completed'), 'ok');
  assert.equal(statusTone('mystery'), '');
  assert.equal(workflowLabel('openjutsu'), 'OpenJutsu');
  assert.equal(workflowLabel('new_thing'), 'new_thing');
});
test('sizes and durations read plainly', () => {
  assert.equal(fmtSize(512), '512 B');
  assert.equal(fmtSize(3 * 2 ** 20), '3.0 MiB');
  assert.equal(fmtSize(null), '—');
  assert.equal(fmtDur(4.26), '4.3 s');
  assert.equal(fmtDur(75), '1 min 15 s');
  assert.equal(fmtDur(null), '—');
});
test('parameter line and rows', () => {
  assert.equal(paramsLine({ width: 512, height: 512, steps: 4, seed: 7 }), '512 × 512 · 4 steps · seed 7');
  assert.equal(paramsLine({ seed: -1 }), '');
  assert.deepEqual(paramRows({ zeta: 1, steps: 4, width: 512 }).map((r) => r[0]), ['Width', 'Steps', 'Zeta']);
});
test('paging appends and drops repeats', () => {
  assert.deepEqual(appendPage([{ id: 'a' }, { id: 'b' }], [{ id: 'b' }, { id: 'c' }]).map((x) => x.id), ['a', 'b', 'c']);
});
test('the reason register asks once for lists and once per opened item, prefilled', () => {
  const r = new ReasonRegister();
  assert.equal(r.needs('browse', 'list'), true);
  r.confirm('list', ' Legal hold request 114 ');
  assert.equal(r.last, 'Legal hold request 114');
  assert.equal(r.needs('browse', 'list'), false);
  assert.equal(r.needs('item', 'job:1'), true);
  r.confirm('job:1', r.last);
  assert.equal(r.needs('item', 'job:1'), false);
  assert.equal(r.needs('item', 'job:2'), true);
  r.clear();
  assert.equal(r.needs('browse', 'list'), true);
  assert.equal(r.last, '');
});
