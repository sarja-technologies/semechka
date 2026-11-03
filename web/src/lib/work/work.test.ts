// Run with: pnpm test   (node's built-in test runner; no browser, no server)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeEvents, cursorOf, buildItems, pendingApprovals, toolFailed, toolLabel } from './events.ts';
import { formatBytes, formatDuration, relativeTime, percent, sessionStatus, containerState, clip, isTextFile, isImage } from './format.ts';
import { parseSkillMarkdown, serializeSkill, validateDraft, slugify } from './skill.ts';
import type { WorkEvent } from './types.ts';

const ev = (id: number, kind: WorkEvent['kind'], data: any = {}, extra: Partial<WorkEvent> = {}): WorkEvent => ({ id, kind, created_at: '2026-11-04T10:00:00Z', data, ...extra });

test('mergeEvents drops duplicates, sorts, and keeps identity when nothing is new', () => {
  const a = [ev(1, 'message', { role: 'user', content: 'hi' }), ev(2, 'state', { status: 'running' })];
  const same = mergeEvents(a, [ev(2, 'state', { status: 'running' })]);
  assert.equal(same, a);
  const m = mergeEvents(a, [ev(4, 'error', { message: 'x' }), ev(3, 'message', { role: 'assistant', content: 'ok' }), ev(2, 'state', { status: 'running' })]);
  assert.deepEqual(m.map((e) => e.id), [1, 2, 3, 4]);
});

test('mergeEvents lets a corrected copy of the same id replace the old one', () => {
  const m = mergeEvents([ev(1, 'job', { job_id: 'j', status: 'running', progress: 0.1 })], [ev(1, 'job', { job_id: 'j', status: 'running', progress: 0.2 })]);
  assert.equal(m.length, 1);
  assert.equal(m[0].data.progress, 0.2);
});

test('mergeEvents ignores rows without a numeric id', () => {
  assert.equal(mergeEvents([], [{ kind: 'message' } as any]).length, 0);
});

test('cursorOf resumes from the highest id seen, or the server value when it is higher', () => {
  assert.equal(cursorOf([]), 0);
  assert.equal(cursorOf([ev(3, 'state'), ev(7, 'state'), ev(5, 'state')]), 7);
  assert.equal(cursorOf([ev(3, 'state')], 9), 9);
  assert.equal(cursorOf([ev(3, 'state')], 1), 3);
});

test('a tool call and its result become one item, in the position of the call', () => {
  const items = buildItems([
    ev(1, 'message', { role: 'user', content: 'go' }),
    ev(2, 'tool_call', { call_id: 'c1', tool: 'shell', command: 'ls -la' }),
    ev(3, 'tool_call', { call_id: 'c2', tool: 'python', command: 'print(1)' }),
    ev(4, 'tool_result', { call_id: 'c1', ok: true, exit_code: 0, output: 'a\nb', duration_ms: 12 })
  ]);
  assert.deepEqual(items.map((i) => i.type), ['message', 'tool', 'tool']);
  const t = items[1] as any;
  assert.equal(t.result.output, 'a\nb');
  assert.equal((items[2] as any).result, undefined);
  assert.equal(toolLabel(items[1] as any), 'ls -la');
});

test('a result that arrives before its call (out of order) still joins it', () => {
  const items = buildItems([ev(5, 'tool_result', { call_id: 'z', ok: false, exit_code: 2 }), ev(6, 'tool_call', { call_id: 'z', tool: 'shell', command: 'false' })]);
  assert.equal(items.length, 1);
  const t = items[0] as any;
  assert.equal(t.call.command, 'false');
  assert.equal(toolFailed(t.result), true);
});

test('job events update one item and the latest values win; progress is never invented', () => {
  const items = buildItems([
    ev(1, 'job', { job_id: 'j1', status: 'queued' }),
    ev(2, 'job', { job_id: 'j1', status: 'running', phase: 'sampling', progress: 0.25 }),
    ev(3, 'job', { job_id: 'j1', status: 'running', phase: 'sampling', progress: 0.5 })
  ]);
  assert.equal(items.length, 1);
  assert.equal((items[0] as any).data.progress, 0.5);
  const q = buildItems([ev(1, 'job', { job_id: 'j2', status: 'queued' })]);
  assert.equal((q[0] as any).data.progress, undefined);
});

test('approvals fold per step and only unanswered ones are pending', () => {
  const items = buildItems([
    ev(1, 'approval', { step: 3, tool: 'shell', command: 'git push', status: 'pending' }),
    ev(2, 'approval', { step: 4, tool: 'write_file', status: 'pending' }),
    ev(3, 'approval', { step: 3, tool: 'shell', status: 'approved' })
  ]);
  assert.equal(items.length, 2);
  const p = pendingApprovals(items);
  assert.deepEqual(p.map((i) => i.stepKey), ['4']);
  assert.equal((items[0] as any).data.command, 'git push');
});

test('unknown event kinds are skipped; error and state events keep their place', () => {
  const items = buildItems([ev(1, 'state', { status: 'running' }), { ...ev(2, 'message'), kind: 'future' as any }, ev(3, 'error', { message: 'boom' })]);
  assert.deepEqual(items.map((i) => i.type), ['state', 'error']);
});

test('formatBytes and formatDuration', () => {
  assert.equal(formatBytes(0), '0 B');
  assert.equal(formatBytes(1023), '1023 B');
  assert.equal(formatBytes(1536), '1.5 KB');
  assert.equal(formatBytes(5 * 1024 * 1024), '5 MB');
  assert.equal(formatBytes(2048 * 1024 * 1024), '2 GB');
  assert.equal(formatBytes(undefined), '');
  assert.equal(formatDuration(80), '80 ms');
  assert.equal(formatDuration(1500), '1.5 s');
  assert.equal(formatDuration(65000), '1 min 5 s');
  assert.equal(formatDuration(null), '');
});

test('relativeTime', () => {
  const now = Date.parse('2026-11-04T12:00:00Z');
  assert.equal(relativeTime('2026-11-04T11:59:50Z', now), 'just now');
  assert.equal(relativeTime('2026-11-04T11:55:00Z', now), '5 min ago');
  assert.equal(relativeTime('2026-11-04T09:00:00Z', now), '3 h ago');
  assert.equal(relativeTime('2026-11-02T12:00:00Z', now), '2 d ago');
  assert.equal(relativeTime('nonsense', now), '');
});

test('status mapping covers every server value and survives an unknown one', () => {
  for (const s of ['idle', 'running', 'awaiting_approval', 'paused', 'failed', 'cancelled']) assert.ok(sessionStatus(s).label);
  assert.equal(sessionStatus('awaiting_approval').tone, 'warn');
  assert.equal(sessionStatus('whatever').label, 'whatever');
  assert.equal(containerState('error').tone, 'bad');
  assert.equal(containerState('absent').label, 'No container yet');
});

test('percent only reports a number the engine gave', () => {
  assert.equal(percent(undefined), null);
  assert.equal(percent(null), null);
  assert.equal(percent(NaN), null);
  assert.equal(percent(0.424), 42);
  assert.equal(percent(1.4), 100);
  assert.equal(percent(-1), 0);
});

test('clip reports what it left out', () => {
  assert.deepEqual(clip('abc', 10), { shown: 'abc', hidden: 0 });
  assert.deepEqual(clip('abcdef', 4), { shown: 'abcd', hidden: 2 });
  assert.equal(clip('a\nb\nc', 100, 2).shown, 'a\nb');
});

test('file kind detection', () => {
  assert.equal(isTextFile('x.py'), true);
  assert.equal(isTextFile('x.png'), false);
  assert.equal(isTextFile('blob', 'application/json'), true);
  assert.equal(isImage('x.PNG'), true);
  assert.equal(isImage('x.svg', 'image/svg+xml'), false);
});

const SAMPLE = `---\nname: chart-maker\ndescription: "Make clean charts: bar, line"\ncategory: data-analysis\nextra: ignored\ntools: [shell]\n---\n\n# Steps\n\n1. Load data\n`;

test('front matter is parsed, unknown keys ignored, body kept', () => {
  const p = parseSkillMarkdown(SAMPLE);
  assert.equal(p.hasFrontMatter, true);
  assert.deepEqual(p.meta, { name: 'chart-maker', description: 'Make clean charts: bar, line', category: 'data-analysis' });
  assert.equal(p.body, '# Steps\n\n1. Load data\n');
  assert.deepEqual(p.problems, []);
});

test('front matter handles CRLF, BOM, single quotes and block scalars', () => {
  const p = parseSkillMarkdown('﻿---\r\nname: a-b\r\ndescription: >\r\n  folded text\r\n  continues\r\ncategory: \'general\'\r\n---\r\nBody');
  assert.equal(p.meta.name, 'a-b');
  assert.equal(p.meta.description, 'folded text continues');
  assert.equal(p.meta.category, 'general');
  assert.equal(p.body, 'Body');
});

test('a file without front matter, or with a bad name or category, is reported', () => {
  assert.equal(parseSkillMarkdown('# Just text').hasFrontMatter, false);
  assert.ok(parseSkillMarkdown('# Just text').problems.length);
  const bad = parseSkillMarkdown('---\nname: Bad Name\ndescription: d\ncategory: nope\n---\nx');
  assert.equal(bad.problems.length, 2);
  assert.ok(parseSkillMarkdown('---\ndescription: d\n---\nx').problems.some((m) => /name/.test(m)));
});

test('serialize then parse returns the same skill, including awkward descriptions', () => {
  const meta = { name: 'x-1', description: 'Uses: colons, # hashes and "quotes"', category: 'coding' };
  const text = serializeSkill(meta, 'Body line\n');
  const p = parseSkillMarkdown(text);
  assert.deepEqual(p.meta, meta);
  assert.equal(p.body, 'Body line\n');
});

test('slugify and draft validation', () => {
  assert.equal(slugify('  Data Cleanup: Pandas! '), 'data-cleanup-pandas');
  const ok = { name: 'data-cleanup', title: 'Data cleanup', description: 'Clean tables', category: 'data-analysis', body_md: 'Do it', files: [] };
  assert.deepEqual(validateDraft(ok), {});
  assert.ok(validateDraft({ ...ok, name: 'Bad Name' }).name);
  assert.ok(validateDraft({ ...ok, body_md: ' ' }).body);
  assert.ok(validateDraft({ ...ok, body_md: 'x'.repeat(65 * 1024) }).body);
  assert.ok(validateDraft({ ...ok, files: [{ name: 'a.txt', content: '' }, { name: 'a.txt', content: '' }] }).files);
  assert.ok(validateDraft({ ...ok, files: [{ name: '../x', content: '' }] }).files);
  assert.ok(validateDraft({ ...ok, files: [{ name: 'big.txt', content: 'x'.repeat(1024 * 1024 + 1) }] }).files);
});
