// Pure logic for the Work Mode timeline: merging polled events without duplicates, the resume cursor, and folding the
// raw event stream (a tool call and its result, many progress updates of one job) into the items the screen shows.
import type { WorkEvent, ToolCallData, ToolResultData, JobData, DelegateData, ApprovalData, ArtifactData, MessageData, StateData, ErrorData } from './types';

/** Merge `incoming` into `current` (both ordered by id). Duplicates by id are dropped (the later copy wins, so a
 *  corrected event replaces the old one). Returns the same array when nothing changed. */
export function mergeEvents(current: WorkEvent[], incoming: WorkEvent[]): WorkEvent[] {
  if (!incoming.length) return current;
  const byId = new Map<number, WorkEvent>();
  for (const e of current) byId.set(e.id, e);
  let changed = false;
  for (const e of incoming) {
    if (typeof e?.id !== 'number' || !Number.isFinite(e.id)) continue;
    const old = byId.get(e.id);
    if (!old || JSON.stringify(old) !== JSON.stringify(e)) { byId.set(e.id, e); changed = true; }
  }
  if (!changed) return current;
  return [...byId.values()].sort((a, b) => a.id - b.id);
}

/** The `since` value for the next poll: the highest id seen, or 0. */
export const cursorOf = (events: WorkEvent[], server?: number): number => {
  let m = 0;
  for (const e of events) if (e.id > m) m = e.id;
  return typeof server === 'number' && Number.isFinite(server) && server > m ? server : m;
};

export type Item =
  | { key: string; type: 'message'; id: number; at: string; data: MessageData }
  | { key: string; type: 'tool'; id: number; at: string; call: ToolCallData; result?: ToolResultData; summary?: string; step?: number | null; endedAt?: string }
  | { key: string; type: 'job'; id: number; at: string; data: JobData; updated: string }
  | { key: string; type: 'delegate'; id: number; at: string; data: DelegateData }
  | { key: string; type: 'artifact'; id: number; at: string; data: ArtifactData }
  | { key: string; type: 'approval'; id: number; at: string; data: ApprovalData; summary?: string; stepKey: string }
  | { key: string; type: 'state'; id: number; at: string; data: StateData }
  | { key: string; type: 'error'; id: number; at: string; data: ErrorData };

/** Fold ordered events into display items. A tool_result joins its tool_call (by call_id); a result with no call is
 *  shown on its own; job, delegate and approval events update one item in place, keeping the position of the first. */
export function buildItems(events: WorkEvent[]): Item[] {
  const items: Item[] = [];
  const index = new Map<string, number>();
  const put = (key: string, make: (prev?: Item) => Item) => {
    const i = index.get(key);
    if (i === undefined) { index.set(key, items.length); items.push(make()); }
    else items[i] = make(items[i]);
  };
  for (const e of events) {
    const d = (e.data ?? {}) as any;
    switch (e.kind) {
      case 'message':
        items.push({ key: `m${e.id}`, type: 'message', id: e.id, at: e.created_at, data: { role: d.role ?? 'assistant', content: String(d.content ?? e.summary ?? '') } });
        break;
      case 'tool_call': {
        const cid = String(d.call_id ?? `e${e.id}`);
        put(`t:${cid}`, (prev) => {
          const p = prev?.type === 'tool' ? prev : undefined;
          return { key: `t:${cid}`, type: 'tool', id: p?.id ?? e.id, at: p?.at ?? e.created_at, call: { ...d, call_id: cid, tool: String(d.tool ?? 'tool') }, result: p?.result, summary: e.summary, step: e.step_n, endedAt: p?.endedAt };
        });
        break;
      }
      case 'tool_result': {
        const cid = String(d.call_id ?? `e${e.id}`);
        put(`t:${cid}`, (prev) => {
          const p = prev?.type === 'tool' ? prev : undefined;
          return { key: `t:${cid}`, type: 'tool', id: p?.id ?? e.id, at: p?.at ?? e.created_at, call: p?.call ?? { call_id: cid, tool: String(d.tool ?? 'tool') }, result: { ...d, call_id: cid }, summary: p?.summary ?? e.summary, step: p?.step ?? e.step_n, endedAt: e.created_at };
        });
        break;
      }
      case 'job': {
        const jid = String(d.job_id ?? `e${e.id}`);
        put(`j:${jid}`, (prev) => {
          const p = prev?.type === 'job' ? prev : undefined;
          return { key: `j:${jid}`, type: 'job', id: p?.id ?? e.id, at: p?.at ?? e.created_at, data: { ...(p?.data ?? {}), ...d, job_id: jid }, updated: e.created_at };
        });
        break;
      }
      case 'delegate': {
        const did = String(d.delegate_id ?? `e${e.id}`);
        put(`d:${did}`, (prev) => {
          const p = prev?.type === 'delegate' ? prev : undefined;
          return { key: `d:${did}`, type: 'delegate', id: p?.id ?? e.id, at: p?.at ?? e.created_at, data: { ...(p?.data ?? {}), ...d, delegate_id: did } };
        });
        break;
      }
      case 'artifact': {
        const aid = String(d.artifact_id ?? `e${e.id}`);
        put(`a:${aid}`, (prev) => ({ key: `a:${aid}`, type: 'artifact', id: prev?.id ?? e.id, at: prev?.at ?? e.created_at, data: { ...d, artifact_id: aid } }));
        break;
      }
      case 'approval': {
        const sk = String(d.step ?? e.step_n ?? e.id);
        put(`p:${sk}`, (prev) => {
          const p = prev?.type === 'approval' ? prev : undefined;
          return { key: `p:${sk}`, type: 'approval', id: p?.id ?? e.id, at: p?.at ?? e.created_at, data: { ...(p?.data ?? {}), ...d, step: d.step ?? e.step_n ?? sk, status: d.status ?? 'pending' }, summary: e.summary ?? p?.summary, stepKey: sk };
        });
        break;
      }
      case 'state':
        items.push({ key: `s${e.id}`, type: 'state', id: e.id, at: e.created_at, data: { status: d.status, note: d.note ?? e.summary } });
        break;
      case 'error':
        items.push({ key: `x${e.id}`, type: 'error', id: e.id, at: e.created_at, data: { code: d.code, message: String(d.message ?? e.summary ?? 'An error occurred.') } });
        break;
      default:
        break; // an event kind this build does not know is skipped, never guessed at
    }
  }
  return items;
}

export const pendingApprovals = (items: Item[]) => items.filter((i): i is Extract<Item, { type: 'approval' }> => i.type === 'approval' && i.data.status === 'pending');

/** True when a tool item has not produced its result yet. */
export const toolRunning = (i: Extract<Item, { type: 'tool' }>) => !i.result;
/** A tool ended badly: the server said ok=false, an error text came back, or the command exited non-zero. */
export const toolFailed = (r: ToolResultData | undefined) => !!r && (r.ok === false || !!r.error || (typeof r.exit_code === 'number' && r.exit_code !== 0));

/** The text a person recognises a tool call by: the command, else the first string argument, else the tool's summary. */
export function toolLabel(i: Extract<Item, { type: 'tool' }>): string {
  if (i.call.command) return i.call.command;
  const a = i.call.args ?? {};
  for (const k of ['cmd', 'command', 'path', 'pattern', 'code', 'file']) if (typeof a[k] === 'string' && a[k]) return a[k] as string;
  return i.summary ?? '';
}
