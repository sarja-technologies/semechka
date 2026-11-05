import { test } from 'node:test';
import assert from 'node:assert/strict';
import { fitView, zoomAt, centerOn, toScreen, visibleWorld, intersects, pinch, panBy, clampK, MIN_K, MAX_K } from './view.ts';
import { layoutGraph } from './layout.ts';
import { activeNodeId } from './logic.ts';
import { ScriptedRun, GRAPH, PRE_IDS } from '../../../../e2e_media/fixtures/inspector/run.ts';

const near = (a: number, b: number, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} vs ${b}`);

test('fit shows the whole graph inside the viewport, centred, never magnified past 1', () => {
  const size = { w: 900, h: 600 };
  const l = layoutGraph(GRAPH);
  const v = fitView({ w: l.width, h: l.height }, size);
  assert.ok(v.k < 1);
  for (const p of [toScreen(v, 0, 0), toScreen(v, l.width, l.height)]) { assert.ok(p.x >= 0 && p.x <= size.w); assert.ok(p.y >= 0 && p.y <= size.h); }
  near(toScreen(v, l.width / 2, l.height / 2).x, size.w / 2);
  near(toScreen(v, l.width / 2, l.height / 2).y, size.h / 2);
  assert.equal(fitView({ w: 100, h: 50 }, size).k, 1);
  assert.deepEqual(fitView({ w: 0, h: 0 }, size), { x: 0, y: 0, k: 1 });
});

test('zooming keeps the point under the cursor fixed and respects the limits', () => {
  const v0 = { x: 40, y: -20, k: 0.5 };
  const world = { x: (300 - v0.x) / v0.k, y: (200 - v0.y) / v0.k };
  const v1 = zoomAt(v0, 300, 200, 1.6);
  near(v1.k, 0.8);
  near(toScreen(v1, world.x, world.y).x, 300);
  near(toScreen(v1, world.x, world.y).y, 200);
  assert.equal(zoomAt(v0, 0, 0, 1e6).k, MAX_K);
  assert.equal(zoomAt(v0, 0, 0, 1e-9).k, MIN_K);
  assert.equal(clampK(1), 1);
  assert.deepEqual(panBy(v0, 5, -5), { x: 45, y: -25, k: 0.5 });
});

test('follow-active: centring puts the node in the middle of the viewport at every step of the run', () => {
  const size = { w: 800, h: 520 };
  const l = layoutGraph(GRAPH);
  const r = new ScriptedRun();
  let view = fitView({ w: l.width, h: l.height }, size);
  const seen = new Set<string>();
  for (const tag of [`pre:${PRE_IDS.find((i) => GRAPH.nodes[i].class_type === 'LoadImage')}:start`, 'scene:1:step:2', 'scene:2:step:5', 'scene:3:step:1']) {
    r.advanceTo(tag);
    const { events, ...d } = r.inspect(0); void events;
    const id = activeNodeId(d, GRAPH);
    assert.ok(id, tag);
    seen.add(id!);
    const p = l.nodes.get(id!)!;
    view = centerOn(view, p.x + p.w / 2, p.y + p.h / 2, size, 0.8);
    const s = toScreen(view, p.x + p.w / 2, p.y + p.h / 2);
    near(s.x, size.w / 2); near(s.y, size.h / 2);
    near(view.k, 0.8);
  }
  assert.ok(seen.size >= 1);
});

test('visible-world culling: a node on screen intersects, a node far away does not', () => {
  const size = { w: 800, h: 600 };
  const v = { x: -1000, y: -200, k: 0.5 };
  const vis = visibleWorld(v, size, 0);
  near(vis.x, 2000); near(vis.w, 1600);
  assert.equal(intersects(vis, { x: 2100, y: 500, w: 248, h: 100 }), true);
  assert.equal(intersects(vis, { x: 0, y: 0, w: 248, h: 100 }), false);
  assert.ok(visibleWorld(v, size, 100).w > vis.w);
});

test('pinch zooms around the midpoint and follows the fingers', () => {
  const v0 = { x: 0, y: 0, k: 1 };
  const spread = pinch(v0, { x: 100, y: 100 }, { x: 200, y: 100 }, { x: 50, y: 100 }, { x: 250, y: 100 });
  near(spread.k, 2);
  near(toScreen(spread, 150, 100).x, 150);
  const moved = pinch(v0, { x: 100, y: 100 }, { x: 200, y: 100 }, { x: 130, y: 120 }, { x: 230, y: 120 });
  near(moved.k, 1); near(moved.x, 30); near(moved.y, 20);
});
