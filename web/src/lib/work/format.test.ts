// The formatting helpers the job inspector shares (bytes, durations, relative times, percentages).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatBytes, formatDuration, relativeTime, percent, sessionStatus, containerState, clip, isTextFile, isImage } from './format.ts';

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

