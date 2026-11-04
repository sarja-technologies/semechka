// Uploading pictures and videos. One request per file, the raw bytes as the body, sent with XMLHttpRequest so the browser reports real
// progress and the person can cancel (aborting leaves nothing on the server). The file is never read into JavaScript memory.
import { apiHeaders } from '$lib/api';
import type { Asset } from './assets';

export type UploadStatus = 'queued' | 'uploading' | 'processing' | 'ready' | 'error' | 'cancelled';
let seq = 0;
// At most this many files are sent at once (the server allows three per person); the rest wait their turn.
const PARALLEL = 3;
let running = 0;
const waiting: (() => void)[] = [];
const next = () => { while (running < PARALLEL && waiting.length) waiting.shift()!(); };

export class Upload {
  readonly key = `up_${++seq}_${Math.random().toString(36).slice(2, 7)}`;
  file: File;
  ephemeral: boolean;
  status = $state<UploadStatus>('queued');
  loaded = $state(0);
  error = $state('');
  asset = $state<Asset | null>(null);
  /** A local preview of the file itself, shown while it uploads; released as soon as the server's own preview is ready. */
  local = $state<string | null>(null);
  private xhr: XMLHttpRequest | null = null;

  constructor(file: File, ephemeral = false) {
    this.file = file;
    this.ephemeral = ephemeral;
    if (/^(image|video)\//.test(file.type)) { try { this.local = URL.createObjectURL(file); } catch { this.local = null; } }
  }

  get fraction() { return this.file.size ? Math.min(1, this.loaded / this.file.size) : 0; }
  get active() { return this.status === 'queued' || this.status === 'uploading' || this.status === 'processing'; }

  private resolveQueued: ((a: Asset | null) => void) | null = null;
  private turn: (() => void) | null = null;

  start(): Promise<Asset | null> {
    this.status = 'queued'; this.error = ''; this.loaded = 0;
    return new Promise((resolve) => {
      const go = () => {
        this.turn = null; this.resolveQueued = null;
        if (this.status === 'cancelled') { resolve(null); return; }
        running++;
        this.send().then((a) => { running--; next(); resolve(a); });
      };
      if (running < PARALLEL) go();
      else { this.turn = go; this.resolveQueued = resolve; waiting.push(go); }
    });
  }

  private send(attempt = 0): Promise<Asset | null> {
    this.status = 'uploading'; this.loaded = 0;
    return new Promise((resolve) => {
      const x = (this.xhr = new XMLHttpRequest());
      x.open('POST', `/api/media/uploads?name=${encodeURIComponent(this.file.name)}${this.ephemeral ? '&ephemeral=true' : ''}`);
      x.withCredentials = true;
      for (const [k, v] of Object.entries(apiHeaders('POST'))) x.setRequestHeader(k, v);
      x.setRequestHeader('content-type', 'application/octet-stream');
      x.upload.onprogress = (e) => { if (e.lengthComputable) this.loaded = e.loaded; };
      x.upload.onload = () => { this.loaded = this.file.size; if (this.status === 'uploading') this.status = 'processing'; };
      x.onload = () => {
        let body: any = null;
        try { body = JSON.parse(x.responseText); } catch { /* not JSON */ }
        if (x.status >= 200 && x.status < 300 && body?.id) { this.asset = body; this.status = 'ready'; this.release(); resolve(body); }
        else if (x.status === 429 && body?.detail?.code === 'busy' && attempt < 6) { this.status = 'queued'; setTimeout(() => (this.status === 'cancelled' ? resolve(null) : this.send(attempt + 1).then(resolve)), 1500); }
        else { this.fail(body?.detail?.message || (x.status === 413 ? 'This file is larger than the server allows.' : x.status === 0 ? 'Cannot reach the server.' : `The upload failed (${x.status}).`)); resolve(null); }
      };
      x.onerror = () => { this.fail('The connection was lost during the upload.'); resolve(null); };
      x.ontimeout = () => { this.fail('The upload timed out.'); resolve(null); };
      x.onabort = () => { if (this.status !== 'error') this.status = 'cancelled'; resolve(null); };
      x.send(this.file);
    });
  }

  cancel() {
    if (this.status === 'ready') return;
    this.status = 'cancelled';
    if (this.turn) { const i = waiting.indexOf(this.turn); if (i >= 0) waiting.splice(i, 1); this.turn = null; this.resolveQueued?.(null); this.resolveQueued = null; }
    this.xhr?.abort();
    this.release();
  }

  retry() { this.release(false); return this.start(); }

  private fail(message: string) { this.status = 'error'; this.error = message; }

  /** Free the local preview. Keep it while the upload could still be retried. */
  release(final = true) { if (final && this.local) { URL.revokeObjectURL(this.local); this.local = null; } }
}

/** Server limits as the interface knows them, for a quick refusal before any bytes are sent. */
export function precheck(file: File, maxMb: number): string | null {
  if (!file.size) return `${file.name} is empty.`;
  if (file.size > maxMb * 1048576) return `${file.name} is larger than the ${maxMb} MB this server accepts.`;
  return null;
}

export const isMedia = (f: File) => /^(image|video)\//.test(f.type) || /\.(png|jpe?g|webp|gif|mp4|m4v|mov|webm|mkv|avi)$/i.test(f.name);
