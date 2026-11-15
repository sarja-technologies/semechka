import test from 'node:test';
import assert from 'node:assert/strict';
import { apply, emptyFold, fromTranscript, diffLines } from './fold.ts';

const ev = (seq: number, type: string, f: Record<string, unknown> = {}) => ({ seq, type, ...f });

test('assistant deltas append to one message; reasoning stays separate', () => {
  const f = apply(emptyFold(), [ev(1, 'turn_start', { turn_id: 't1', text: 'hi' }), ev(2, 'reasoning_delta', { message_id: 'm1', text: 'think ' }), ev(3, 'assistant_delta', { message_id: 'm1', text: 'Hel' }), ev(4, 'assistant_delta', { message_id: 'm1', text: 'lo' })]);
  assert.equal(f.items.length, 2);
  const a = f.items[1] as any;
  assert.equal(a.text, 'Hello'); assert.equal(a.reasoning, 'think '); assert.equal(f.turn, 't1');
});

test('a repeated or older seq is ignored (reconnect never duplicates)', () => {
  let f = apply(emptyFold(), [ev(1, 'assistant_delta', { message_id: 'm', text: 'a' }), ev(2, 'assistant_delta', { message_id: 'm', text: 'b' })]);
  const again = apply(f, [ev(2, 'assistant_delta', { message_id: 'm', text: 'b' }), ev(1, 'assistant_delta', { message_id: 'm', text: 'a' })]);
  assert.equal(again, f); assert.equal((f.items[0] as any).text, 'ab'); assert.equal(f.lastSeq, 2);
});

test('a tool call runs, streams output and ends with its own final output and diff', () => {
  let f = apply(emptyFold(), [ev(1, 'tool_start', { tool_call_id: 'c1', tool: 'shell', title: 'pytest', input: { command: 'pytest' } }), ev(2, 'tool_output', { tool_call_id: 'c1', stream: 'stdout', text: '1 failed\n' })]);
  assert.equal((f.items[0] as any).status, 'running'); assert.equal((f.items[0] as any).output, '1 failed\n');
  f = apply(f, [ev(3, 'tool_end', { tool_call_id: 'c1', status: 'failed', output: '1 failed\nexit 1', exit_code: 1, diff: { path: 'a.py', unified: '+x' } })]);
  const t = f.items[0] as any;
  assert.equal(t.status, 'failed'); assert.equal(t.exit_code, 1); assert.equal(t.output, '1 failed\nexit 1'); assert.equal(t.diff.path, 'a.py');
});

test('an approval is asked, then closed by id with who decided', () => {
  let f = apply(emptyFold(), [ev(1, 'approval_request', { approval: { id: 'ap_1', tool: 'shell', title: 'rm -r build', options: ['allow_once', 'deny'] } }), ev(2, 'state', { state: 'awaiting_approval' })]);
  assert.equal(f.state, 'awaiting_approval'); assert.equal((f.items[0] as any).decision, undefined);
  f = apply(f, [ev(3, 'approval_resolved', { approval_id: 'ap_1', decision: 'deny', by: 'user' })]);
  assert.equal((f.items[0] as any).decision, 'deny'); assert.equal((f.items[0] as any).by, 'user');
});

test('cancelling ends running tools as cancelled and records why the turn stopped', () => {
  let f = apply(emptyFold(), [ev(1, 'turn_start', { turn_id: 't', text: 'x' }), ev(2, 'tool_start', { tool_call_id: 'c', tool: 'shell', title: 'sleep 99' })]);
  f = apply(f, [ev(3, 'turn_end', { turn_id: 't', stop_reason: 'cancelled' })]);
  assert.equal((f.items[1] as any).status, 'cancelled'); assert.equal((f.items[2] as any).reason, 'cancelled'); assert.equal(f.turn, null);
});

test('a normal end adds nothing; model loading is one item that follows the phase', () => {
  let f = apply(emptyFold(), [ev(1, 'model_loading', { model: 'q', phase: 'loading', ts: 100 }), ev(2, 'state', { state: 'loading_model' })]);
  assert.equal(f.loading?.model, 'q'); assert.equal(f.items.length, 1);
  f = apply(f, [ev(3, 'model_loading', { model: 'q', phase: 'ready' }), ev(4, 'state', { state: 'running' }), ev(5, 'turn_end', { stop_reason: 'end_turn' })]);
  assert.equal(f.items.length, 1); assert.equal((f.items[0] as any).phase, 'ready'); assert.equal(f.loading, null);
});

test('unknown event types are skipped, and the transcript maps to the same items', () => {
  const f = apply(emptyFold(), [ev(1, 'from_the_future', {})]);
  assert.equal(f.items.length, 0); assert.equal(f.lastSeq, 1);
  const t = fromTranscript([{ kind: 'user', text: 'a' }, { kind: 'assistant', message_id: 'm', text: 'b' }, { kind: 'tool', tool_call_id: 'c', tool: 'shell', title: 't', status: 'completed', output: 'ok' }, { kind: 'approval', id: 'ap', tool: 'shell', title: 'x', options: [], decision: 'allow_once' }], 9);
  assert.deepEqual(t.items.map((i) => i.k), ['user', 'assistant', 'tool', 'approval']); assert.equal(t.lastSeq, 9); assert.equal((t.items[3] as any).decision, 'allow_once');
});

test('diff lines are classified and keep their markers', () => {
  const l = diffLines('--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n same');
  assert.deepEqual(l.map((x) => x.t), ['meta', 'meta', 'hunk', 'del', 'add', 'ctx']);
});
