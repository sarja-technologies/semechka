// Thin fetch wrapper: same-origin cookies, CSRF header on unsafe methods, normalised errors, SSE reader.
export class ApiError extends Error {
  status: number; code: string; detail: any;
  constructor(status: number, detail: any) {
    super(detail?.message || `Request failed (${status})`);
    this.status = status; this.code = detail?.code || 'error'; this.detail = detail;
  }
}

let csrf = '';
export const setCsrf = (t: string) => { csrf = t; };
let setupToken = '';
export const setSetupToken = (t: string) => { setupToken = t; };

function headers(method: string, extra: Record<string, string> = {}) {
  const h: Record<string, string> = { ...extra };
  if (method !== 'GET' && csrf) h['x-csrf-token'] = csrf;
  if (setupToken) h['x-setup-token'] = setupToken;
  return h;
}

async function parse(r: Response) {
  const text = await r.text();
  let body: any = null;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  if (!r.ok) throw new ApiError(r.status, body?.detail && typeof body.detail === 'object' ? body.detail : { message: typeof body?.detail === 'string' ? body.detail : r.statusText });
  return body;
}

export async function api<T = any>(method: string, path: string, body?: unknown): Promise<T> {
  let r: Response;
  try {
    r = await fetch(path, { method, credentials: 'same-origin', headers: headers(method, body !== undefined ? { 'content-type': 'application/json' } : {}), body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, { code: 'offline', message: 'Cannot reach the Semechka server. Check your connection.' });
  }
  return parse(r);
}
export const get = <T = any>(p: string) => api<T>('GET', p);
export const post = <T = any>(p: string, b?: unknown) => api<T>('POST', p, b ?? {});
export const put = <T = any>(p: string, b?: unknown) => api<T>('PUT', p, b ?? {});
export const patch = <T = any>(p: string, b?: unknown) => api<T>('PATCH', p, b ?? {});
export const del = <T = any>(p: string) => api<T>('DELETE', p);

export async function upload<T = any>(path: string, file: File): Promise<T> {
  const fd = new FormData(); fd.append('file', file);
  const r = await fetch(path, { method: 'POST', credentials: 'same-origin', headers: headers('POST'), body: fd });
  return parse(r);
}

export const qs = (o: Record<string, unknown>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== '') p.set(k, String(v));
  const s = p.toString(); return s ? `?${s}` : '';
};

/** POST and read a text/event-stream. Calls onEvent for each JSON event. Abort with the returned controller. */
export function stream(path: string, body: unknown, onEvent: (e: any) => void): { done: Promise<void>; abort: () => void } {
  const ctrl = new AbortController();
  const done = (async () => {
    let r: Response;
    try {
      r = await fetch(path, { method: 'POST', credentials: 'same-origin', signal: ctrl.signal, headers: headers('POST', { 'content-type': 'application/json' }), body: JSON.stringify(body) });
    } catch (e: any) {
      if (e?.name === 'AbortError') return;
      throw new ApiError(0, { code: 'offline', message: 'Cannot reach the Semechka server. Check your connection.' });
    }
    if (!r.ok) { await parse(r); return; }
    const reader = r.body!.getReader(); const dec = new TextDecoder(); let buf = '';
    try {
      for (;;) {
        const { value, done: d } = await reader.read();
        if (d) break;
        buf += dec.decode(value, { stream: true });
        let i: number;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
          for (const line of chunk.split('\n')) if (line.startsWith('data: ')) { try { onEvent(JSON.parse(line.slice(6))); } catch { /* ignore malformed */ } }
        }
      }
    } catch (e: any) { if (e?.name !== 'AbortError') throw e; }
  })();
  return { done, abort: () => ctrl.abort() };
}
