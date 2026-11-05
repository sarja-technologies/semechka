import { test } from 'node:test';
import assert from 'node:assert/strict';
import { layoutGraph, graphEdges, edgePath, CARD_W, GAP_X, GAP_Y } from './layout.ts';
import type { GraphDoc } from './types.ts';
import { GRAPH } from '../../../../e2e_media/fixtures/inspector/run.ts';

function overlaps(l: ReturnType<typeof layoutGraph>) {
  const bad: string[] = [];
  const L = l.list;
  for (let i = 0; i < L.length; i++) for (let j = i + 1; j < L.length; j++) {
    const a = L[i], b = L[j];
    if (a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y) bad.push(`${a.id}/${b.id}`);
  }
  return bad;
}

test('the real 70-node OpenJutsu graph lays out left to right with no overlapping cards', () => {
  const l = layoutGraph(GRAPH);
  assert.equal(l.list.length, 70);
  assert.deepEqual(overlaps(l), []);
  assert.ok(l.levels >= 8 && l.levels <= 40, `levels ${l.levels}`);
  // cards in one column keep at least the gap between them
  const cols = new Map<number, typeof l.list>();
  for (const p of l.list) cols.set(p.level, [...(cols.get(p.level) ?? []), p]);
  for (const c of cols.values()) { c.sort((a, b) => a.y - b.y); for (let i = 1; i < c.length; i++) assert.ok(c[i].y - (c[i - 1].y + c[i - 1].h) >= GAP_Y - 1e-6); }
  for (const p of l.list) { assert.ok(p.x >= 0 && p.y >= 0); assert.equal(p.x, p.level * (CARD_W + GAP_X)); }
  assert.ok(l.width > 0 && l.height > 0);
});

test('every link points forward: a node is always to the right of the nodes it reads from', () => {
  const l = layoutGraph(GRAPH);
  const edges = graphEdges(GRAPH);
  assert.ok(edges.length > 70);
  for (const e of edges) assert.ok(l.nodes.get(e.to)!.level > l.nodes.get(e.from)!.level, `${e.from} -> ${e.to}`);
  assert.equal(l.edges.length, edges.length);
});

test('loaders sit next to their first consumer instead of far to the left', () => {
  const l = layoutGraph(GRAPH);
  const succ = new Map<string, number>();
  for (const e of graphEdges(GRAPH)) succ.set(e.from, Math.min(succ.get(e.from) ?? 1e9, l.nodes.get(e.to)!.level));
  for (const [id, n] of Object.entries(GRAPH.nodes)) {
    const isSource = !Object.values(n.inputs).some((v) => Array.isArray(v) && GRAPH.nodes[v[0] as string]);
    if (isSource && succ.has(id)) assert.equal(l.nodes.get(id)!.level, succ.get(id)! - 1, `${id} ${n.class_type}`);
  }
});

test('layout is deterministic', () => {
  const a = JSON.stringify([...layoutGraph(GRAPH).list]);
  const b = JSON.stringify([...layoutGraph(structuredClone(GRAPH)).list]);
  assert.equal(a, b);
});

test('edge curves start at the right side of the source and end at the left side of the target', () => {
  const l = layoutGraph(GRAPH);
  const e = l.edges[0];
  const a = l.nodes.get(e.from)!, b = l.nodes.get(e.to)!;
  const d = edgePath(a, b);
  assert.match(d, new RegExp(`^M${(a.x + a.w).toFixed(1)} ${(a.y + a.h / 2).toFixed(1)}C`));
  assert.ok(d.endsWith(`${b.x.toFixed(1)} ${(b.y + b.h / 2).toFixed(1)}`));
});

test('degenerate graphs: empty, one node, a chain, links to unknown nodes, and a cycle do not break', () => {
  assert.equal(layoutGraph({ nodes: {} }).list.length, 0);
  const one = layoutGraph({ nodes: { '1': { class_type: 'A', inputs: { x: 1 } } } });
  assert.equal(one.list.length, 1);
  assert.equal(one.levels, 1);
  const chain: GraphDoc = { nodes: { a: { class_type: 'A', inputs: {} }, b: { class_type: 'B', inputs: { i: ['a', 0] } }, c: { class_type: 'C', inputs: { i: ['b', 0], j: ['ghost', 0] } } } };
  const c = layoutGraph(chain);
  assert.deepEqual(c.list.map((p) => p.level), [0, 1, 2]);
  assert.equal(c.edges.length, 2);
  const cyc: GraphDoc = { nodes: { a: { class_type: 'A', inputs: { i: ['b', 0] } }, b: { class_type: 'B', inputs: { i: ['a', 0] } } } };
  const cl = layoutGraph(cyc);
  assert.equal(cl.list.length, 2);
  assert.deepEqual(overlaps(cl), []);
  const self: GraphDoc = { nodes: { a: { class_type: 'A', inputs: { i: ['a', 0] } } } };
  assert.equal(layoutGraph(self).edges.length, 0);
});

test('a 160-node random DAG lays out fast and without overlaps', () => {
  let s = 12345; const rnd = () => (s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const nodes: GraphDoc['nodes'] = {};
  for (let i = 0; i < 160; i++) {
    const inputs: Record<string, any> = { seed: i, text: 'a long prompt '.repeat(10) };
    const n = i === 0 ? 0 : 1 + Math.floor(rnd() * 3);
    for (let k = 0; k < n; k++) inputs[`in${k}`] = [String(Math.floor(rnd() * i)), 0];
    nodes[String(i)] = { class_type: `Class${i % 17}`, title: `Node ${i}`, inputs };
  }
  const t = Date.now();
  const l = layoutGraph({ nodes });
  assert.ok(Date.now() - t < 1500, 'layout took too long');
  assert.equal(l.list.length, 160);
  assert.deepEqual(overlaps(l), []);
  for (const e of l.edges) assert.ok(l.nodes.get(e.to)!.level > l.nodes.get(e.from)!.level);
});
