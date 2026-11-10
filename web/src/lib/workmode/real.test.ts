// The fold run over event streams RECORDED from a real Goose 1.58.1 session on the Spark (workstream `work`, docs/evidence/work-goose/ on branch consol/work,
// copied to e2e_media/fixtures/work-goose/). These are real events, not invented ones: the screen's reading of them is checked, not Goose.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { apply, emptyFold } from './fold.ts';

const load = (n: string) => readFileSync(new URL(`../../../e2e_media/fixtures/work-goose/${n}.jsonl`, import.meta.url), 'utf8').trim().split('\n').map((l) => JSON.parse(l));

test('a real repository-fixing turn folds into a clean timeline', () => {
  const ev = load('events-repo');
  const f = apply(emptyFold(), ev);
  assert.equal(f.lastSeq, ev[ev.length - 1].seq);
  const tools = f.items.filter((i) => i.k === 'tool') as any[], asks = f.items.filter((i) => i.k === 'approval') as any[];
  assert.equal(tools.length, ev.filter((e) => e.type === 'tool_start').length);
  assert.ok(tools.every((t) => t.status !== 'running'), 'every tool ended');
  assert.equal(asks.length, 14); assert.ok(asks.every((a) => a.decision), 'every question was answered');
  assert.ok(asks.some((a) => a.decision === 'deny'), 'the denial is shown as one');
  assert.ok(tools.some((t) => t.status === 'failed'), 'the denied tool call ended failed');
  assert.ok(tools.every((t) => t.title.length > 0));
  const files = f.items.filter((i) => i.k === 'files') as any[];
  assert.equal(files.length, 1); assert.equal(files[0].files[0].path, 'shop/pricing.py');
  assert.ok(f.items.some((i) => i.k === 'assistant' && i.text.length > 20), 'the agent\'s words are there');
  assert.ok(f.items.some((i) => i.k === 'assistant' && i.reasoning.length > 20), 'its reasoning is kept apart');
  assert.equal(f.turn, null); assert.equal(f.title, 'Fix failing tests'); assert.ok(f.usage && f.usage.size > 0);
  assert.equal(f.items.filter((i) => i.k === 'end').length, 0, 'a normal end adds nothing');
});

test('a real cancelled turn ends its running tool as stopped and says the turn was cancelled', () => {
  const ev = load('events-cancel');
  const f = apply(emptyFold(), ev);
  assert.ok(f.items.every((i) => !(i.k === 'tool' && i.status === 'running')));
  assert.ok(f.items.some((i) => i.k === 'end' && i.reason === 'cancelled'));
  assert.equal(f.turn, null);
});

test('a real multi-turn session: every event is read, in order, and re-applying the stream changes nothing', () => {
  const ev = load('events-all-alice');
  const f = apply(emptyFold(), ev);
  assert.equal(f.lastSeq, Math.max(...ev.map((e) => e.seq)));
  assert.equal(apply(f, ev), f);
  const types = new Set(ev.map((e) => e.type));
  console.log('event types in the recording:', [...types].sort().join(', '));
});
