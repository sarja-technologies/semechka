// Typed wrappers over /api/work/goose. Every call goes through the shared api() helper: same cookies, CSRF header and ApiError as the rest of the app.
import { get, post, patch, del, qs, sendForm, ApiError } from '../api';
import type { Decision, FileChange, GooseSession, GooseStatus, WorkEvent } from './types';

const B = '/api/work/goose';
const C = (cid: string) => `${B}/conversations/${encodeURIComponent(cid)}`;

export const gooseStatus = () => get<GooseStatus>(`${B}/status`);
export const getSession = (cid: string) => get<GooseSession>(`${C(cid)}/session`);
export const createSession = (cid: string, b: { model?: string; autonomy?: string; mcp?: string[]; skills?: string[] } = {}) => post<GooseSession>(`${C(cid)}/session`, b);
export const patchSession = (cid: string, b: { model?: string; autonomy?: string; mcp?: string[]; skills?: string[] }) => patch<GooseSession>(`${C(cid)}/session`, b);
export const stopSession = (cid: string) => post(`${C(cid)}/session/stop`);
export const deleteSession = (cid: string) => del(`${C(cid)}/session`);
export const prompt = (cid: string, text: string, attachments?: string[]) => post<{ turn_id: string; seq: number }>(`${C(cid)}/prompt`, { text, ...(attachments?.length ? { attachments } : {}) });
export const cancelTurn = (cid: string) => post<GooseSession>(`${C(cid)}/cancel`);
export const answer = (cid: string, approvalId: string, decision: Decision) => post(`${C(cid)}/approvals/${encodeURIComponent(approvalId)}`, { decision });
export const transcript = (cid: string) => get<{ items: any[]; last_seq: number }>(`${C(cid)}/transcript`);
export const changes = (cid: string, turn: string) => get<{ turn_id: string; files: FileChange[]; diff: string; truncated?: boolean }>(`${C(cid)}/changes/${encodeURIComponent(turn)}`);
export const listFiles = (cid: string, path = '') => get<{ path: string; entries: { name: string; path: string; type: 'file' | 'dir'; size?: number; modified?: number }[] }>(`${C(cid)}/files${qs({ path })}`);
/** Put a file into the workspace; `path` is the file's own path inside it. */
export const uploadFile = (cid: string, path: string, file: File) => { const fd = new FormData(); fd.append('file', file, file.name); return sendForm<{ path: string; size: number }>('PUT', `${C(cid)}/files${qs({ path })}`, fd); };
export const fileUrl = (cid: string, path: string, download = false) => `${C(cid)}/files/content${qs({ path, download: download ? 1 : 0 })}`;

/** Why Work Mode cannot be used, sorted so the screen can say it plainly. `none` = the server has no such API at all. */
export type Unavailable = { kind: 'none' | 'disabled' | 'denied' | 'not_ready' | 'offline' | 'error'; message: string; problems?: string[] };
export function unavailableFrom(e: unknown): Unavailable {
  if (e instanceof ApiError) {
    if (e.status === 0) return { kind: 'offline', message: e.message };
    if (e.status === 404 || e.status === 405) return { kind: 'none', message: 'Work Mode is not available on this server.' };
    if (e.status === 403) return { kind: e.code === 'disabled' ? 'disabled' : 'denied', message: e.message && !/^Request failed/.test(e.message) ? e.message : 'Work Mode is not available to you.' };
    if (e.status === 503) return { kind: 'not_ready', message: e.message };
    return { kind: 'error', message: e.message };
  }
  return { kind: 'error', message: 'Something unexpected happened. Try again.' };
}

/** Read the events of a conversation's Work session as server-sent events, resuming after `since`. Calls onEvent for each; reconnects with the last
 *  seq it saw (backing off) until stopped. A 4xx ends it and reports through onGiveUp, because retrying would not help. */
export function followEvents(cid: string, since: number, onEvent: (e: WorkEvent) => void, o: { onState?: (s: 'live' | 'retrying') => void; onGiveUp?: (e: ApiError) => void } = {}): () => void {
  let stopped = false, ctrl: AbortController | null = null, last = since, fails = 0;
  const run = async () => {
    while (!stopped) {
      ctrl = new AbortController();
      try {
        const r = await fetch(`${C(cid)}/events${qs({ since: last })}`, { credentials: 'same-origin', signal: ctrl.signal, headers: { accept: 'text/event-stream' } });
        if (!r.ok) {
          const body = await r.json().catch(() => null);
          const err = new ApiError(r.status, body?.detail && typeof body.detail === 'object' ? body.detail : { message: r.statusText });
          if (r.status >= 400 && r.status < 500 && r.status !== 408 && r.status !== 429) { o.onGiveUp?.(err); return; }
          throw err;
        }
        fails = 0; o.onState?.('live');
        const reader = r.body!.getReader(), dec = new TextDecoder();
        let buf = '';
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i: number;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, i); buf = buf.slice(i + 2);
            const data = frame.split('\n').filter((l) => l.startsWith('data:')).map((l) => l.slice(5).trimStart()).join('\n');
            if (!data) continue;
            try { const ev = JSON.parse(data) as WorkEvent; if (typeof ev.seq === 'number' && ev.seq > last) { last = ev.seq; onEvent(ev); } } catch { /* a malformed frame is skipped, never guessed at */ }
          }
        }
      } catch (e: any) {
        if (stopped || e?.name === 'AbortError') return;
      }
      if (stopped) return;
      fails++; o.onState?.('retrying');
      await new Promise((res) => setTimeout(res, Math.min(10000, 500 * 2 ** Math.min(fails, 5))));
    }
  };
  void run();
  return () => { stopped = true; ctrl?.abort(); };
}

