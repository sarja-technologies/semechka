// Folds the ordered events of a Work session (docs/WORK_MODE_GOOSE.md section 5) into the items the screen draws. Pure: no network, no DOM.
// Events are applied by `seq`; one seen twice or out of order is ignored, so reconnecting can never repeat or reorder anything.
import type { Item, SessionState, WorkEvent } from './types.ts';

export type Folded = {
  items: Item[]; state: SessionState; turn: string | null; usage: { used: number; size: number } | null; title: string; lastSeq: number;
  /** model-load progress while the router loads the model */
  loading: { model: string; since: number } | null;
};
export const emptyFold = (): Folded => ({ items: [], state: 'idle', turn: null, usage: null, title: '', lastSeq: 0, loading: null });

const OUTPUT_CAP = 64 * 1024;
const ends = (s: string, cap: number) => (s.length > cap ? '…' + s.slice(s.length - cap) : s);

/** Apply a batch of events. Returns a new Folded; `items` is a new array only when something changed. */
export function apply(prev: Folded, events: WorkEvent[]): Folded {
  let f = prev, items = prev.items, copied = false;
  const own = () => { if (!copied) { items = items.slice(); copied = true; } };
  const find = (pred: (i: Item) => boolean) => { for (let n = items.length - 1; n >= 0; n--) if (pred(items[n])) return n; return -1; };
  const put = (i: Item) => { own(); items.push(i); };
  const set = (n: number, i: Item) => { own(); items[n] = i; };
  for (const ev of events) {
    if (typeof ev?.seq !== 'number' || ev.seq <= f.lastSeq) continue;
    f = { ...f, lastSeq: ev.seq };
    const key = `e${ev.seq}`;
    switch (ev.type) {
      case 'state': f = { ...f, state: ev.state, ...(ev.state !== 'loading_model' ? { loading: null } : {}) }; break;
      case 'model_loading': {
        const n = find((i) => i.k === 'model');
        const item: Item = { key: n >= 0 ? items[n].key : key, k: 'model', model: String(ev.model ?? ''), phase: ev.phase, message: ev.message };
        if (n >= 0) set(n, item); else put(item);
        f = { ...f, loading: ev.phase === 'loading' ? { model: String(ev.model ?? ''), since: f.loading?.since ?? (ev.ts ?? Date.now() / 1000) } : null };
        break;
      }
      case 'turn_start': put({ key, k: 'user', turn: ev.turn_id, text: String(ev.text ?? '') }); f = { ...f, turn: ev.turn_id ?? null }; break;
      case 'assistant_delta':
      case 'reasoning_delta': {
        const id = String(ev.message_id ?? ev.turn_id ?? 'a');
        const n = find((i) => i.k === 'assistant' && i.id === id);
        const text = String(ev.text ?? '');
        if (n < 0) put({ key, k: 'assistant', id, turn: ev.turn_id, text: ev.type === 'assistant_delta' ? text : '', reasoning: ev.type === 'reasoning_delta' ? text : '' });
        else { const a = items[n] as Extract<Item, { k: 'assistant' }>; set(n, ev.type === 'assistant_delta' ? { ...a, text: a.text + text } : { ...a, reasoning: a.reasoning + text }); }
        break;
      }
      case 'tool_start':
        put({ key, k: 'tool', id: String(ev.tool_call_id), turn: ev.turn_id, tool: String(ev.tool ?? 'tool'), extension: ev.extension, title: String(ev.title ?? ev.tool ?? ''), kind: ev.kind, input: ev.input, status: 'running', output: '' });
        break;
      case 'tool_output': {
        const n = find((i) => i.k === 'tool' && i.id === String(ev.tool_call_id));
        if (n >= 0) { const t = items[n] as Extract<Item, { k: 'tool' }>; set(n, { ...t, output: ends(t.output + String(ev.text ?? ''), OUTPUT_CAP) }); }
        break;
      }
      case 'tool_end': {
        const n = find((i) => i.k === 'tool' && i.id === String(ev.tool_call_id));
        const patch = { status: ev.status as Extract<Item, { k: 'tool' }>['status'], exit_code: ev.exit_code ?? null, diff: ev.diff ?? null };
        if (n >= 0) { const t = items[n] as Extract<Item, { k: 'tool' }>; set(n, { ...t, ...patch, output: typeof ev.output === 'string' && ev.output.length ? ends(ev.output, OUTPUT_CAP) : t.output }); }
        else put({ key, k: 'tool', id: String(ev.tool_call_id), turn: ev.turn_id, tool: String(ev.tool ?? 'tool'), title: String(ev.title ?? ev.tool ?? 'tool'), output: String(ev.output ?? ''), ...patch });
        break;
      }
      case 'approval_request':
        if (ev.approval) put({ key, k: 'approval', approval: ev.approval });
        break;
      case 'approval_resolved': {
        const n = find((i) => i.k === 'approval' && i.approval.id === ev.approval_id);
        if (n >= 0) set(n, { ...(items[n] as Extract<Item, { k: 'approval' }>), decision: ev.decision, by: ev.by });
        break;
      }
      case 'files_changed': put({ key, k: 'files', turn: String(ev.turn_id ?? ''), files: Array.isArray(ev.files) ? ev.files : [], truncated: !!ev.truncated }); break;
      case 'usage': f = { ...f, usage: { used: Number(ev.used) || 0, size: Number(ev.size) || 0 } }; break;
      case 'title': f = { ...f, title: String(ev.title ?? '') }; break;
      case 'turn_end': {
        // anything still marked running when the turn ends did not finish
        const stuck = ev.stop_reason === 'cancelled' || ev.stop_reason === 'interrupted' || ev.stop_reason === 'error';
        if (stuck) items.forEach((i, n) => { if (i.k === 'tool' && i.status === 'running') set(n, { ...i, status: 'cancelled' }); });
        if (ev.stop_reason && ev.stop_reason !== 'end_turn') put({ key, k: 'end', turn: ev.turn_id, reason: String(ev.stop_reason) });
        f = { ...f, turn: null };
        break;
      }
      case 'error': put({ key, k: 'error', code: ev.code, message: String(ev.message ?? 'An error occurred.'), retryable: !!ev.retryable }); break;
      case 'notice': put({ key, k: 'notice', code: ev.code, message: String(ev.message ?? '') }); break;
      default: break; // an event type this build does not know is skipped, never guessed at
    }
  }
  return items === prev.items && f === prev ? prev : { ...f, items };
}

/** The folded history the server returns for a reload (section 6) as items. */
export function fromTranscript(rows: any[], lastSeq: number): Folded {
  const items: Item[] = [];
  rows.forEach((r, n) => {
    const key = `t${n}`;
    switch (r.kind) {
      case 'user': items.push({ key, k: 'user', turn: r.turn_id, text: String(r.text ?? '') }); break;
      case 'assistant': items.push({ key, k: 'assistant', id: String(r.message_id ?? key), turn: r.turn_id, text: String(r.text ?? ''), reasoning: String(r.reasoning ?? '') }); break;
      case 'tool': items.push({ key, k: 'tool', id: String(r.tool_call_id ?? key), turn: r.turn_id, tool: String(r.tool ?? 'tool'), title: String(r.title ?? r.tool ?? ''), input: r.input, status: r.status ?? 'completed', output: String(r.output ?? ''), diff: r.diff ?? null }); break;
      case 'approval': { const { kind: _k, decision, by, ...a } = r; items.push({ key, k: 'approval', approval: a as any, decision, by }); break; }
      case 'files': items.push({ key, k: 'files', turn: String(r.turn_id ?? ''), files: r.files ?? [], truncated: !!r.truncated }); break;
      case 'error': items.push({ key, k: 'error', code: r.code, message: String(r.message ?? ''), retryable: !!r.retryable }); break;
      case 'notice': items.push({ key, k: 'notice', code: r.code, message: String(r.message ?? '') }); break;
      default: break;
    }
  });
  return { ...emptyFold(), items, lastSeq };
}

/** A unified diff as lines with what each line is, for drawing (the marker characters stay in the text, so colour is never the only cue). */
export function diffLines(unified: string): { t: 'add' | 'del' | 'hunk' | 'meta' | 'ctx'; s: string }[] {
  return unified.split('\n').map((s) => ({ t: s.startsWith('+++') || s.startsWith('---') || s.startsWith('diff ') || s.startsWith('index ') ? 'meta' : s.startsWith('@@') ? 'hunk' : s.startsWith('+') ? 'add' : s.startsWith('-') ? 'del' : 'ctx', s }));
}

export const isBusy = (s: SessionState) => s === 'starting' || s === 'loading_model' || s === 'running' || s === 'awaiting_approval' || s === 'cancelling';
