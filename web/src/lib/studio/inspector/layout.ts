// Deterministic left-to-right layered layout of a ComfyUI API graph. No dependency: longest-path layering (loaders pulled
// next to their first consumer), barycentre ordering to reduce crossings, then vertical placement that aligns each node
// with its neighbours without ever letting two cards overlap. Same input, same output, every time.
import type { GraphDoc } from './types.ts';
import { isLink, shortValue } from './logic.ts';

export const CARD_W = 248;
export const GAP_X = 84;
export const GAP_Y = 26;
const HEAD = 50, ROW = 18, FOOT = 28, MAX_ROWS = 4;

export interface Edge { from: string; to: string; fromSlot: number; input: string }
export interface Placed { id: string; level: number; x: number; y: number; w: number; h: number; rows: { name: string; value: string }[]; moreInputs: number }
export interface Layout { nodes: Map<string, Placed>; list: Placed[]; edges: Edge[]; levels: number; width: number; height: number }

export function keyInputs(inputs: Record<string, unknown>): { rows: { name: string; value: string }[]; more: number } {
  const all = Object.entries(inputs).filter(([, v]) => !isLink(v));
  return { rows: all.slice(0, MAX_ROWS).map(([name, v]) => ({ name, value: shortValue(v, 34) })), more: Math.max(0, all.length - MAX_ROWS) };
}

export function graphEdges(g: GraphDoc): Edge[] {
  const edges: Edge[] = [];
  for (const [to, n] of Object.entries(g.nodes)) {
    for (const [input, v] of Object.entries(n.inputs ?? {})) {
      if (isLink(v) && v[0] !== to && g.nodes[v[0]]) edges.push({ from: v[0], to, fromSlot: v[1], input });
    }
  }
  return edges;
}

export function layoutGraph(g: GraphDoc): Layout {
  const ids = g.order?.length ? [...g.order.filter((i) => g.nodes[i]), ...Object.keys(g.nodes).filter((i) => !g.order!.includes(i))] : Object.keys(g.nodes);
  const idx = new Map(ids.map((id, i) => [id, i]));
  const edges = graphEdges(g);
  const preds = new Map<string, string[]>(), succs = new Map<string, string[]>();
  for (const id of ids) { preds.set(id, []); succs.set(id, []); }
  for (const e of edges) {
    if (!preds.get(e.to)!.includes(e.from)) { preds.get(e.to)!.push(e.from); succs.get(e.from)!.push(e.to); }
  }

  // 1. longest-path levels (Kahn; leftovers of a cycle, which a ComfyUI graph never has, are placed after their placed predecessors)
  const level = new Map<string, number>();
  const indeg = new Map(ids.map((id) => [id, preds.get(id)!.length]));
  const queue = ids.filter((id) => indeg.get(id) === 0);
  for (const q of queue) level.set(q, 0);
  const topo: string[] = [];
  while (queue.length) {
    const n = queue.shift()!; topo.push(n);
    for (const s of succs.get(n)!) {
      level.set(s, Math.max(level.get(s) ?? 0, level.get(n)! + 1));
      indeg.set(s, indeg.get(s)! - 1);
      if (indeg.get(s) === 0) queue.push(s);
    }
  }
  for (const id of ids) if (!level.has(id)) { level.set(id, Math.max(0, ...preds.get(id)!.filter((p) => level.has(p)).map((p) => level.get(p)! + 1))); topo.push(id); }

  // 2. pull sources (loaders, primitives) right, next to their first consumer, so their edges stay short
  for (let i = topo.length - 1; i >= 0; i--) {
    const n = topo[i];
    if (preds.get(n)!.length === 0 && succs.get(n)!.length > 0) level.set(n, Math.min(...succs.get(n)!.map((s) => level.get(s)!)) - 1);
  }
  const minL = Math.min(0, ...level.values());
  if (minL < 0) for (const id of ids) level.set(id, level.get(id)! - minL);
  const levels = ids.length ? Math.max(...level.values()) + 1 : 0;

  // 3. order inside each column: barycentre sweeps
  const cols: string[][] = Array.from({ length: levels }, () => []);
  for (const id of ids) cols[level.get(id)!].push(id);
  const pos = new Map<string, number>();
  const refresh = () => { for (const c of cols) c.forEach((id, i) => pos.set(id, (i + 0.5) / c.length)); };
  refresh();
  const sortBy = (col: string[], nb: Map<string, string[]>) => {
    const key = new Map<string, number>();
    for (const id of col) { const ns = nb.get(id)!; key.set(id, ns.length ? ns.reduce((a, n) => a + pos.get(n)!, 0) / ns.length : pos.get(id)!); }
    col.sort((a, b) => key.get(a)! - key.get(b)! || idx.get(a)! - idx.get(b)!);
  };
  for (let it = 0; it < 6; it++) {
    for (let l = 1; l < levels; l++) { sortBy(cols[l], preds); refresh(); }
    for (let l = levels - 2; l >= 0; l--) { sortBy(cols[l], succs); refresh(); }
  }

  // 4. geometry
  const placed = new Map<string, Placed>();
  for (const id of ids) {
    const { rows, more } = keyInputs(g.nodes[id].inputs ?? {});
    const h = HEAD + (rows.length ? rows.length * ROW + 8 : 0) + (more ? ROW : 0) + FOOT;
    placed.set(id, { id, level: level.get(id)!, x: level.get(id)! * (CARD_W + GAP_X), y: 0, w: CARD_W, h, rows, moreInputs: more });
  }
  // initial stack, each column centred on y = 0
  for (const c of cols) {
    const total = c.reduce((a, id) => a + placed.get(id)!.h, 0) + GAP_Y * Math.max(0, c.length - 1);
    let y = -total / 2;
    for (const id of c) { const p = placed.get(id)!; p.y = y; y += p.h + GAP_Y; }
  }
  // align with neighbours: the column order is kept, so crossings stay as the sweeps left them
  const place = (col: string[], nb: Map<string, string[]>) => {
    if (!col.length) return;
    const desired = col.map((id) => { const ns = nb.get(id)!; const p = placed.get(id)!; return ns.length ? ns.reduce((a, n) => a + placed.get(n)!.y + placed.get(n)!.h / 2, 0) / ns.length : p.y + p.h / 2; });
    const ys: number[] = [];
    let prevBottom = -Infinity;
    col.forEach((id, i) => { const p = placed.get(id)!; const y = Math.max(desired[i] - p.h / 2, prevBottom + GAP_Y); ys.push(y); prevBottom = y + p.h; });
    let shift = 0;
    col.forEach((id, i) => { const p = placed.get(id)!; shift += ys[i] + p.h / 2 - desired[i]; });
    shift /= col.length;
    col.forEach((id, i) => { placed.get(id)!.y = ys[i] - shift; });
  };
  for (let it = 0; it < 4; it++) {
    for (let l = 1; l < levels; l++) place(cols[l], preds);
    for (let l = levels - 2; l >= 0; l--) place(cols[l], succs);
  }
  let minY = Infinity, maxY = -Infinity;
  for (const p of placed.values()) { minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y + p.h); }
  if (!placed.size) { minY = 0; maxY = 0; }
  for (const p of placed.values()) p.y -= minY;
  const list = ids.map((id) => placed.get(id)!);
  return { nodes: placed, list, edges, levels, width: levels ? levels * CARD_W + (levels - 1) * GAP_X : 0, height: maxY - minY };
}

/** SVG path of an edge from the right side of `a` to the left side of `b` (a smooth horizontal S-curve). */
export function edgePath(a: Placed, b: Placed): string {
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x, y2 = b.y + b.h / 2;
  const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
  return `M${x1.toFixed(1)} ${y1.toFixed(1)}C${(x1 + dx).toFixed(1)} ${y1.toFixed(1)} ${(x2 - dx).toFixed(1)} ${y2.toFixed(1)} ${x2.toFixed(1)} ${y2.toFixed(1)}`;
}
