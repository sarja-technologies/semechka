// Run with: pnpm test   (node's built-in test runner; no browser, no server)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  mergeEvents, maxSeq, phaseOf, isFinal, progressView, sceneCells, sceneHeadline, nodeRows, nodeCounts, nodeBar, stateOfGraphNode, activeNodeId,
  describeEvent, elapsedNow, silentFor, fmtElapsed, relTime, filterEvents, eventsToText, shortValue, isLink, fmtMb, basisText, segmentSummary, KEEP_EVENTS
} from './logic.ts';
import type { InspectEvent, InspectSummary } from './types.ts';
import { ScriptedRun, IDS, GRAPH, completedRun, failedRun } from '../../../../e2e_media/fixtures/inspector/run.ts';

const ev = (seq: number, type = 'log', extra: Partial<InspectEvent> = {}): InspectEvent => ({ seq, t: 1000 + seq, type, ...extra });
const doc = (r: ScriptedRun): InspectSummary => { const { events, ...s } = r.inspect(0); void events; return s; };

test('mergeEvents sorts by seq, drops duplicates, lets a corrected copy win, and keeps identity when nothing changed', () => {
  const a = [ev(1), ev(2)];
  assert.equal(mergeEvents(a, [ev(2)]), a);
  assert.equal(mergeEvents(a, []), a);
  assert.equal(mergeEvents(a, undefined), a);
  const m = mergeEvents(a, [ev(4), ev(3), ev(2)]);
  assert.deepEqual(m.map((e) => e.seq), [1, 2, 3, 4]);
  const fixed = mergeEvents(m, [ev(3, 'log', { message: 'corrected' })]);
  assert.equal(fixed.find((e) => e.seq === 3)!.message, 'corrected');
  assert.equal(fixed.length, 4);
  assert.equal(mergeEvents([], [{ type: 'log' } as any]).length, 0);
});

test('mergeEvents keeps only the newest KEEP_EVENTS', () => {
  const many = Array.from({ length: 1500 }, (_, i) => ev(i + 1));
  const m = mergeEvents([], many);
  assert.equal(m.length, KEEP_EVENTS);
  assert.equal(m[0].seq, 501);
  assert.equal(maxSeq(m), 1500);
  assert.equal(maxSeq([]), 0);
});

test('paging the whole run through after=<max seq> rebuilds exactly the server trace (what a page refresh does)', () => {
  const r = completedRun();
  let held: InspectEvent[] = [];
  let pages = 0;
  for (;;) { const d = r.inspect(maxSeq(held)); pages++; const before = held; held = mergeEvents(held, d.events); if (held === before) break; }
  assert.ok(pages >= 3, 'the run has more than one 500-event page');
  assert.equal(held.length, Math.min(r.events.length, KEEP_EVENTS));
  assert.deepEqual(held.map((e) => e.seq), r.events.slice(-held.length).map((e) => e.seq));
});

test('a Loop End never completes the job: only status completed AND output ready does', () => {
  const r = new ScriptedRun();
  r.advanceTo('scene:2:loop_end');
  const d = doc(r);
  assert.equal(d.loop!.iterations_ended, 2);
  assert.ok(r.events.some((e) => e.type === 'loop_end'));
  assert.equal(phaseOf(d), 'running');
  assert.equal(isFinal(d), false);
  r.advanceTo('scene:7:loop_end');
  const last = doc(r);
  assert.equal(last.loop!.iterations_ended, 7);
  assert.equal(phaseOf(last), 'running', 'seven Loop Ends and still not finished');
  assert.equal(isFinal(last), false);
  r.advanceTo('output_writing');
  assert.equal(phaseOf(doc(r)), 'running');
  assert.equal(doc(r).output!.state, 'writing');
  r.advanceTo('completed');
  assert.equal(phaseOf(doc(r)), 'completed');
  assert.equal(isFinal(doc(r)), true);
});

test('completed without a ready output is "finishing", and is not final', () => {
  const d: InspectSummary = { status: 'completed', output: { state: 'writing' } };
  assert.equal(phaseOf(d), 'finishing');
  assert.equal(isFinal(d), false);
  assert.equal(phaseOf({ status: 'completed', output: null }), 'finishing');
  assert.equal(phaseOf({ status: 'completed', output: { state: 'ready' } }), 'completed');
});

test('failed, cancelled and trace-less jobs are final at once; queued and running are not', () => {
  assert.equal(isFinal({ status: 'failed' }), true);
  assert.equal(isFinal({ status: 'cancelled' }), true);
  assert.equal(isFinal({ status: 'running' }), false);
  assert.equal(isFinal({ status: 'queued' }), false);
  assert.equal(isFinal({ trace_available: false, status: 'completed' }), true);
  assert.equal(isFinal({ trace_available: false, status: 'running' }), false, 'no trace yet is not the end of a running job');
  assert.equal(isFinal({ stale: true, status: 'completed' }), false, 'a stale answer is never the final one');
  assert.equal(phaseOf({ trace_available: false, status: 'completed', output: { state: 'none' } }), 'completed', "Semechka's own record of a collected job");
  assert.equal(isFinal(null), false);
  assert.equal(phaseOf(failedRun().doc), 'failed');
});

test('progress is indeterminate until the engine gives a fraction, and never reaches 100% before the output is ready', () => {
  const r = new ScriptedRun();
  r.advanceTo('started');
  assert.deepEqual(progressView(doc(r)), { determinate: false, percent: null, basis: null, held: false });
  r.advanceTo('scene:2:saved');
  const p = progressView(doc(r));
  assert.equal(p.determinate, true);
  assert.ok(Math.abs(p.percent! - (100 * 2) / 7) < 1e-9);
  assert.equal(p.basis, '2 of 7 scenes saved');
  r.advanceTo('scene:7:saved');
  const full = progressView(doc(r));
  assert.equal(full.percent, 99);
  assert.equal(full.held, true);
  r.advanceAll();
  assert.equal(progressView(doc(r)).percent, 100);
  assert.equal(progressView({ progress: { fraction: 0.5, indeterminate: true } }).determinate, false);
  assert.equal(progressView({ progress: { fraction: null, indeterminate: false } }).determinate, false);
  assert.equal(progressView(null).determinate, false);
  assert.equal(basisText({ progress: { fraction: 0.1, basis: 'nodes_done/nodes_total' } }), 'Based on nodes done/nodes total');
});

test('scene cells: saved, current, pending, and the headline wording', () => {
  const r = new ScriptedRun();
  r.advanceTo('started');
  assert.deepEqual(sceneCells(doc(r)), []);
  assert.equal(sceneHeadline(doc(r)), 'Waiting for the first scene');
  r.advanceTo(`scene:3:${IDS.sampler}:start`);
  r.advanceTo('scene:3:step:4');
  const d = doc(r);
  const cells = sceneCells(d);
  assert.equal(cells.length, 7);
  assert.deepEqual(cells.map((c) => c.state), ['saved', 'saved', 'current', 'pending', 'pending', 'pending', 'pending']);
  assert.equal(cells[0].segment!.frames, 141);
  assert.equal(sceneHeadline(d), 'Scene 3 of 7');
  assert.equal(d.sampling!.step, 4);
  r.advanceTo('scene:3:saved');
  assert.equal(sceneCells(doc(r))[2].state, 'saved');
});

test('scene cells with an unknown total show what is known plus one open cell; a segment still being written is not playable', () => {
  const d: InspectSummary = { status: 'running', scenes: { total: null, completed: 1, current: 2, segments: [{ index: 1, state: 'saved' }] } };
  const c = sceneCells(d);
  assert.deepEqual(c.map((x) => x.state), ['saved', 'current', 'pending']);
  assert.equal(c[2].unknownTotal, true);
  assert.equal(sceneHeadline(d), 'Scene 2, total not known yet');
  const w = sceneCells({ status: 'running', scenes: { total: 3, completed: 0, current: 1, segments: [{ index: 1, state: 'writing' }] } });
  assert.equal(w[0].state, 'writing');
  assert.equal(sceneCells({ status: 'running', scenes: null }).length, 0);
});

test('on a failed job the scene that was running is shown as interrupted, on a completed one all are saved', () => {
  const f = failedRun();
  const c = sceneCells(f.doc);
  assert.deepEqual(c.map((x) => x.state), ['saved', 'saved', 'interrupted', 'pending', 'pending', 'pending', 'pending']);
  const done = completedRun();
  assert.ok(sceneCells(done.doc).every((x) => x.state === 'saved'));
  assert.equal(sceneHeadline(done.doc), 'All 7 scenes saved');
});

test('node counts and the stacked bar add up to the total', () => {
  const r = new ScriptedRun();
  r.advanceTo('scene:2:step:3');
  const c = nodeCounts(doc(r))!;
  assert.equal(c.total, 70);
  assert.equal(c.running, 1);
  assert.equal(c.done + c.running + c.cached + c.failed + c.pending, 70);
  const bar = nodeBar(c);
  assert.ok(Math.abs(bar.reduce((a, s) => a + s.pct, 0) - 100) < 1e-6);
  assert.equal(nodeCounts({ nodes: null }), null);
});

test('node rows: running first, then failed, then recently finished; filters and search work', () => {
  const r = failedRun();
  const rows = nodeRows(r.doc, r.events);
  assert.equal(rows[0].state, 'failed');
  assert.equal(rows[0].id, IDS.sampler);
  const live = new ScriptedRun();
  live.advanceTo('scene:2:step:3');
  const lr = nodeRows(doc(live), live.events);
  assert.equal(lr[0].state, 'running');
  const firstDone = lr.findIndex((x) => x.state === 'done');
  assert.ok(lr.slice(0, firstDone).every((x) => x.rank < 2));
  // recently finished nodes come before older ones
  const doneRows = lr.filter((x) => x.state === 'done');
  for (let i = 1; i < doneRows.length; i++) assert.ok(doneRows[i - 1].seen >= doneRows[i].seen);
  assert.equal(nodeRows(doc(live), live.events, 'running').length, 1);
  assert.ok(nodeRows(doc(live), live.events, 'all', 'sampl').every((x) => /sampl/i.test(x.title + x.class_type)));
  assert.equal(nodeRows(null, []).length, 0);
});

test('loop-expanded node states roll up onto the graph node they came from', () => {
  const states = {
    '73': { state: 'done', runs: 1, total_ms: 100, last_ms: 100 },
    '73.0.1': { state: 'running', runs: 1, total_ms: 40, last_ms: 40 },
    '73.0.2': { state: 'done', runs: 2, total_ms: 60, last_ms: 30 },
    '74:1': { state: 'done', runs: 1, total_ms: 5, last_ms: 5 },
    '740': { state: 'failed' }
  };
  const a = stateOfGraphNode('73', states);
  assert.equal(a.state, 'running');
  assert.equal(a.expanded, 2);
  assert.equal(a.runs, 4);
  assert.equal(a.total_ms, 200);
  const b = stateOfGraphNode('74', states);
  assert.equal(b.state, 'done');
  assert.equal(b.expanded, 1);
  assert.equal(stateOfGraphNode('7', states).known, false, '7 must not match 73 or 740');
  assert.equal(stateOfGraphNode('1', null).state, 'unknown');
});

test('the active node is the sampler while sampling, else the newest running node in the graph', () => {
  const r = new ScriptedRun();
  r.advanceTo('scene:1:step:2');
  assert.equal(activeNodeId(doc(r), GRAPH), IDS.sampler);
  r.advanceTo(`scene:1:${IDS.sampler}:done`);
  assert.equal(activeNodeId(doc(r), GRAPH), null, 'nothing is running between nodes');
  r.advance();
  assert.ok(activeNodeId(doc(r), GRAPH));
  assert.equal(activeNodeId({ nodes: { total: 1, done: 0, running: ['73.0.3'], pending: 0, cached: 0, failed: 0 } }, { nodes: { '73': { class_type: 'x', inputs: {} } } }), '73');
  assert.equal(activeNodeId(null), null);
});

test('event lines: Loop End reads as a saved scene, not as completion', () => {
  const l = describeEvent(ev(1, 'loop_end', { scene: 3 }));
  assert.equal(l.text, 'Scene 3 saved, starting next scene');
  assert.equal(l.tone, 'loop');
  assert.equal(l.label, 'Loop End');
  assert.equal(describeEvent(ev(1, 'loop_end')).text, 'A scene was saved, starting next scene');
  assert.equal(describeEvent(ev(2, 'progress', { title: 'Sample Video + Audio', value: 3, max: 6, scene: 3 })).text, 'Sample Video + Audio: step 3 of 6 (scene 3)');
  assert.equal(describeEvent(ev(3, 'scene_saved', { scene: 2 })).tone, 'ok');
  assert.equal(describeEvent(ev(4, 'error', { message: 'boom' })).tone, 'bad');
  assert.equal(describeEvent(ev(5, 'node_cached', { title: 'VAE' })).text, 'Reused VAE from cache');
  assert.equal(describeEvent(ev(6, 'something_new', { message: 'x' })).label, 'something_new');
});

test('log filters: milestones hide per-node noise, errors only keeps errors, and copy-all text has relative times', () => {
  const r = failedRun();
  const all = r.events;
  const ms = filterEvents(all, 'milestones');
  assert.ok(ms.length < all.length && ms.every((e) => ['stage', 'scene_saved', 'loop_end', 'error', 'output'].includes(e.type)));
  assert.equal(filterEvents(all, 'errors').length, 1);
  assert.equal(filterEvents(all, 'all'), all);
  const txt = eventsToText(filterEvents(all, 'errors'), r.doc.started_at);
  assert.match(txt, /Error\s+CUDA out of memory/);
  assert.match(txt, /\+\d/);
});

test('elapsed time ticks from the server value, stops when terminal, and is unknown while queued', () => {
  const run: InspectSummary = { status: 'running', started_at: 100, updated_at: 160, elapsed_s: 60 };
  assert.equal(elapsedNow(run, 10_000, 10_000), 60);
  assert.equal(elapsedNow(run, 10_000, 15_500), 65.5);
  assert.equal(elapsedNow({ ...run, status: 'completed' }, 10_000, 99_000), 60);
  assert.equal(elapsedNow({ status: 'queued' }, 0, 1), null);
  assert.equal(elapsedNow({ status: 'running', started_at: 100, updated_at: 130 }, 0, 2000), 32);
  assert.equal(elapsedNow({ status: 'running' }, 0, 1), null);
  assert.equal(elapsedNow(null, 0, 1), null);
  assert.equal(silentFor(run, 10_000, 10_000), 0);
  assert.equal(silentFor(run, 10_000, 55_000), 45);
  assert.equal(silentFor({ ...run, status: 'completed' }, 0, 1), null);
});

test('small formatters', () => {
  assert.equal(fmtElapsed(5), '5.0 s');
  assert.equal(fmtElapsed(143.2), '2 m 23 s');
  assert.equal(fmtElapsed(3725), '1 h 02 m 05 s');
  assert.equal(fmtElapsed(null), 'not reported');
  assert.equal(relTime(1012.34, 1000), '+12.3 s');
  assert.equal(relTime(1075, 1000), '+1 m 15 s');
  assert.equal(relTime(null, 1), '');
  assert.equal(fmtMb(21400), '20.9 GB');
  assert.equal(fmtMb(512), '512 MB');
  assert.equal(fmtMb(null), 'not reported');
  assert.equal(shortValue('x'.repeat(100), 10), 'xxxxxxxxx…');
  assert.equal(shortValue({ a: 1 }), '{"a":1}');
  assert.equal(shortValue(null), 'null');
  assert.equal(isLink(['26', 1]), true);
  assert.equal(isLink(['a', 'b']), false);
  assert.equal(isLink('26'), false);
  assert.match(segmentSummary({ index: 1, state: 'saved', frames: 141, duration_s: 5.875, width: 736, height: 1312, seconds_to_make: 38.1, bytes: 1234567 }), /141 frames · 5\.9 s · 736×1312 · made in 38 s · 1\.2 MB/);
  assert.equal(segmentSummary({ index: 1, state: 'saved' }), 'no details reported');
});

test('node counts come from the per-node states, so reused nodes are not counted twice', () => {
  const d: InspectSummary = { nodes: { total: 5, done: 4, running: ['3'], pending: 0, cached: 2, failed: 0 },
    node_states: { '1': { state: 'done' }, '2': { state: 'cached' }, '3': { state: 'running' }, '4': { state: 'cached' }, '5': { state: 'done' } } };
  assert.deepEqual(nodeCounts(d), { total: 5, done: 2, running: 1, cached: 2, failed: 0, pending: 0 });
  assert.deepEqual(nodeCounts({ nodes: { total: 10, done: 4, running: ['1', '2'], pending: 3, cached: 1, failed: 0 } }), { total: 10, done: 4, running: 2, cached: 1, failed: 0, pending: 3 });
});

test('rollupStates agrees with stateOfGraphNode for every node', async () => {
  const { rollupStates } = await import('./logic.ts');
  const states = { '73': { state: 'done', runs: 1 }, '73.0.1': { state: 'running', runs: 1 }, '74:1': { state: 'done', runs: 1 }, '5': { state: 'pending' } };
  const ids = ['73', '74', '5', '7', '99'];
  const m = rollupStates(states, ids);
  for (const id of ids) assert.deepEqual(m.get(id), stateOfGraphNode(id, states), id);
  assert.equal(rollupStates(null, ['1']).get('1')!.known, false);
});

test('a cancelled job (stage "failed") is shown as cancelled, never as failed or completed', () => {
  const d: InspectSummary = { status: 'cancelled', stage: 'failed', output: { state: 'none' } };
  assert.equal(phaseOf(d), 'cancelled');
  assert.equal(isFinal(d), true);
  const cells = sceneCells({ ...d, scenes: { total: 3, completed: 1, current: 2, segments: [{ index: 1, state: 'saved' }] } });
  assert.deepEqual(cells.map((c) => c.state), ['saved', 'interrupted', 'pending']);
});

test('a finished picture job (no scenes at all) says Finished, never "0 scenes saved"', () => {
  const d: any = { status: 'completed', trace_available: false, scenes: { total: null, completed: 0, current: null, segments: [] }, progress: null };
  assert.equal(sceneHeadline(d), 'Finished');
});
