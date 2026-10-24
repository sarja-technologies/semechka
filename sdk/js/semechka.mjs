// Semechka client for replacement frontends. One file, no dependencies, plain ES module: copy it next to your
// page and `import { Semechka } from './semechka.mjs'`. Documented in docs/API.md and docs/CUSTOM_FRONTEND.md.
//
// The server decides everything: which models a person may use, which kinds of turn they may take, what is stored.
// This client only carries requests; hiding a control in your interface never grants or removes a permission.
//
// Requests are same-origin and cookie-authenticated. Every request that changes something carries the session's
// CSRF token, which this client reads from GET /api/me after sign-in.

export const API_VERSION = 'semechka/0.3';

/**
 * @typedef {{ status: number, code: string, message: string }} ErrorInfo
 * @typedef {'text' | 'code' | 'vision' | 'image' | 'video'} Operation
 * @typedef {{ id: string, title: string, updated_at: number, created_at: number, model_id: string | null,
 *   expires_at: number | null, pinned: boolean, folder_id: string | null, mode: 'chat' | 'code', archived: boolean }} Conversation
 * @typedef {{ id: string, name: string, kind: string, mime: string, size: number, width?: number | null,
 *   height?: number | null, saved?: boolean, generated?: boolean, missing?: boolean }} Attachment
 * @typedef {{ id: string, parent_id: string | null, role: 'user' | 'assistant', kind: 'text' | 'image' | 'video',
 *   content: string, status: string, created_at: number, model: string | null, model_id: string | null,
 *   meta: Record<string, any>, tech: Record<string, any>, attachments?: Attachment[], media?: any, sources?: any[],
 *   siblings?: { index: number, count: number, prev: string | null, next: string | null } | null }} Message
 *   (siblings.index is zero-based: show index + 1 of count)
 * @typedef {Conversation & { leaf: string | null, total: number, start: number, has_more: boolean, messages: Message[] }} Thread
 * @typedef {{ id: string, alias: string, pinned: boolean, capabilities: string[], external: boolean,
 *   profile: Record<string, any>, media_modes: string[], loaded?: boolean | null }} CatalogModel
 * @typedef {{ operation: Operation, models: CatalogModel[], groups: { id: string, name: string, description: string,
 *   collapsed: boolean, models: string[] }[], ungrouped: string[], pinned: string[], default: string | null,
 *   show_names: boolean }} Catalog
 * @typedef {{ type: 'start', assistant_id: string, user_message_id?: string }
 *   | { type: 'delta', content: string, reasoning: string }
 *   | { type: 'queue', position: number, running?: boolean, ahead?: number, waiting?: number, limit?: number }
 *   | { type: 'sources', sources: any[], skipped: number }
 *   | { type: 'trimmed', dropped: number }
 *   | { type: 'error', message: string, assistant_id?: string }
 *   | { type: 'done', assistant_id: string, status: string, content: string, reasoning: string, tech: Record<string, any>,
 *       sources: any[] | null, trimmed: number | null, message?: Message, user_message?: Message }} StreamEvent
 */

export class SemechkaError extends Error {
  /** @param {number} status @param {any} detail */
  constructor(status, detail) {
    const d = typeof detail === 'object' && detail ? detail : { message: String(detail ?? '') };
    super(d.message || `Request failed (${status})`);
    this.name = 'SemechkaError';
    /** HTTP status; 0 when the server could not be reached. */
    this.status = status;
    /** Stable machine-readable code, e.g. 'mfa_required', 'csrf', 'forbidden', 'not_found', 'no_model'. */
    this.code = d.code || (status === 401 ? 'unauthenticated' : status === 403 ? 'forbidden' : 'error');
  }
}

export class Semechka {
  /** @param {{ base?: string }} [opts] base is '' for the page's own origin (the only supported setup). */
  constructor(opts = {}) {
    this.base = opts.base ?? '';
    /** @type {string} */
    this.csrf = '';
  }

  // ---- transport -------------------------------------------------------------------------------------------------
  /** @param {string} method @param {Record<string, string>} [extra] */
  headers(method, extra = {}) {
    const h = { accept: 'application/json', ...extra };
    if (method !== 'GET' && this.csrf) h['x-csrf-token'] = this.csrf;
    return h;
  }

  /** @param {Response} r */
  async fail(r) {
    let detail = null;
    try { const j = await r.json(); detail = j.detail ?? j; } catch { /* not JSON */ }
    throw new SemechkaError(r.status, detail);
  }

  /**
   * JSON request. Throws SemechkaError on any non-2xx answer.
   * @param {string} method @param {string} path @param {any} [body]
   */
  async request(method, path, body) {
    let r;
    try {
      r = await fetch(this.base + path, { method, credentials: 'same-origin',
        headers: this.headers(method, body === undefined ? {} : { 'content-type': 'application/json' }),
        body: body === undefined ? undefined : JSON.stringify(body) });
    } catch { throw new SemechkaError(0, { code: 'offline', message: 'Cannot reach the server.' }); }
    if (!r.ok) await this.fail(r);
    return r.status === 204 ? null : r.json();
  }
  get(path) { return this.request('GET', path); }
  post(path, body = {}) { return this.request('POST', path, body); }
  patch(path, body) { return this.request('PATCH', path, body); }
  del(path) { return this.request('DELETE', path); }

  /**
   * POST that answers with Server-Sent Events (one JSON object per `data:` line). Resolves with the 'done' event,
   * or null when stopped. Calling `stop()` (or aborting `signal`) ends the request; the server then stores the reply
   * so far, marked as stopped.
   * @param {string} path @param {any} body @param {{ onEvent?: (e: StreamEvent) => void, signal?: AbortSignal }} [opts]
   */
  stream(path, body, opts = {}) {
    const ctrl = new AbortController();
    opts.signal?.addEventListener('abort', () => ctrl.abort(), { once: true });
    const done = (async () => {
      let r;
      try {
        r = await fetch(this.base + path, { method: 'POST', credentials: 'same-origin', signal: ctrl.signal,
          headers: this.headers('POST', { 'content-type': 'application/json', accept: 'text/event-stream' }), body: JSON.stringify(body) });
      } catch (e) {
        if (e?.name === 'AbortError') return null;
        throw new SemechkaError(0, { code: 'offline', message: 'Cannot reach the server.' });
      }
      if (!r.ok) await this.fail(r);
      const reader = r.body.getReader(), dec = new TextDecoder();
      let buf = '', final = null;
      try {
        for (;;) {
          const { value, done: end } = await reader.read();
          if (end) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            for (const line of chunk.split('\n')) {
              if (!line.startsWith('data: ')) continue;
              let ev; try { ev = JSON.parse(line.slice(6)); } catch { continue; }
              if (ev.type === 'done') final = ev;
              opts.onEvent?.(ev);
            }
          }
        }
      } catch (e) { if (e?.name !== 'AbortError') throw e; }
      return final;
    })();
    return { done, stop: () => ctrl.abort() };
  }

  // ---- session ---------------------------------------------------------------------------------------------------
  /** Branding and sign-in options for a signed-out visitor. */
  public() { return this.get('/api/public'); }

  /**
   * Sign in. When the account uses two-step sign-in, the first attempt fails with code 'mfa_required'; repeat it
   * with `totp` (the 6-digit code) or `recoveryCode`.
   * @param {{ email: string, password: string, totp?: string, recoveryCode?: string }} c
   */
  async login(c) {
    await this.post('/api/auth/login', { email: c.email, password: c.password, totp: c.totp || null, recovery_code: c.recoveryCode || null });
    return this.me();
  }
  async logout() { try { await this.post('/api/auth/logout'); } finally { this.csrf = ''; } }

  /** The signed-in person, their interface configuration (`ui`) and the CSRF token. Throws 401 when signed out. */
  async me() { const m = await this.get('/api/me'); this.csrf = m.csrf || ''; return m; }
  /** What this server offers and what this person may do. Prefer this over assuming. */
  capabilities() { return this.get('/api/capabilities'); }
  /** The design-token contract: token ids, the Light/Dark/Graphite presets and the contrast pairs. */
  themeTokens() { return this.get('/api/theme/tokens'); }

  // ---- models ----------------------------------------------------------------------------------------------------
  /** Models this person may use for one kind of turn, in the administrator's order and groups. @param {Operation} [operation] @returns {Promise<Catalog>} */
  catalog(operation = 'text') { return this.get(`/api/models/catalog?operation=${encodeURIComponent(operation)}`); }

  // ---- conversations ---------------------------------------------------------------------------------------------
  /**
   * @param {{ q?: string, archived?: boolean, mode?: 'chat' | 'code', limit?: number, before?: number, folderId?: string }} [f]
   * @returns {Promise<Conversation[]>}
   */
  conversations(f = {}) {
    const p = new URLSearchParams();
    if (f.q) p.set('q', f.q);
    if (f.archived) p.set('archived', 'true');
    if (f.mode) p.set('mode', f.mode);
    if (f.limit) p.set('limit', String(f.limit));
    if (f.before !== undefined) p.set('before', String(f.before));
    if (f.folderId) p.set('folder_id', f.folderId);
    const s = p.toString();
    return this.get('/api/chat/conversations' + (s ? `?${s}` : ''));
  }
  /** @param {{ modelId?: string | null, mode?: 'chat' | 'code', assistantId?: string | null, collectionIds?: string[] }} [o] @returns {Promise<Conversation>} */
  createConversation(o = {}) {
    return this.post('/api/chat/conversations', { model_id: o.modelId ?? null, mode: o.mode ?? 'chat', assistant_id: o.assistantId ?? null, collection_ids: o.collectionIds ?? [] });
  }
  /** The visible branch, newest `limit` messages. @param {string} id @param {{ limit?: number, before?: string }} [o] @returns {Promise<Thread>} */
  thread(id, o = {}) {
    const p = new URLSearchParams({ limit: String(o.limit ?? 40) });
    if (o.before) p.set('before', o.before);
    return this.get(`/api/chat/conversations/${id}/thread?${p}`);
  }
  /** Show another version: the thread continues to the newest reply under `messageId`. @returns {Promise<Thread>} */
  showBranch(id, messageId, limit = 40) { return this.post(`/api/chat/conversations/${id}/branch`, { message_id: messageId, limit }); }
  /** Rename, pin, archive, move to a folder (`folder_id: ''` takes it out). @param {string} id @param {Record<string, any>} changes */
  updateConversation(id, changes) { return this.patch(`/api/chat/conversations/${id}`, changes); }
  deleteConversation(id) { return this.del(`/api/chat/conversations/${id}`); }
  /** Markdown of every message (needs the export permission). @returns {Promise<string>} */
  async exportMarkdown(id) {
    const r = await fetch(`${this.base}/api/chat/conversations/${id}/export?format=md`, { credentials: 'same-origin' });
    if (!r.ok) await this.fail(r);
    return r.text();
  }

  // ---- turns -----------------------------------------------------------------------------------------------------
  /**
   * Send a message and stream the reply. Pass `parentId` = the last visible message's id (null for the first).
   * @param {string} conversationId
   * @param {{ content: string, modelId: string, parentId: string | null, attachments?: string[], params?: Record<string, any> }} t
   * @param {{ onEvent?: (e: StreamEvent) => void, signal?: AbortSignal }} [opts]
   */
  send(conversationId, t, opts) {
    return this.stream(`/api/chat/conversations/${conversationId}/generate`,
      { mode: 'send', operation: 'text', content: t.content, model_id: t.modelId, parent_id: t.parentId, attachments: t.attachments ?? [], params: t.params ?? null }, opts);
  }
  /** A new version of an assistant reply; the old one stays reachable through `siblings`. */
  regenerate(conversationId, assistantMessageId, modelId, opts) {
    return this.stream(`/api/chat/conversations/${conversationId}/generate`, { mode: 'regenerate', parent_id: assistantMessageId, model_id: modelId }, opts);
  }
  /** Replace one of your messages with new text: a new branch with a new reply. */
  edit(conversationId, userMessageId, content, modelId, opts) {
    return this.stream(`/api/chat/conversations/${conversationId}/generate`, { mode: 'edit', operation: 'text', content, parent_id: userMessageId, model_id: modelId }, opts);
  }
  /**
   * Make a picture or video in the conversation. Answers at once with the new messages; the job then runs on the
   * engine. `message.media` is the job: poll job(message.media.id) until its status is 'completed', 'failed' or
   * 'cancelled', then show each of its `assets` from mediaUrl(asset.id).
   * @param {string} conversationId
   * @param {{ operation: 'image' | 'video', prompt: string, modelId: string, parentId: string | null, sourceId?: string,
   *   attachments?: string[], media?: Record<string, any> }} t
   * @returns {Promise<{ message: Message, user_message: Message | null }>}
   */
  makeMedia(conversationId, t) {
    return this.post(`/api/chat/conversations/${conversationId}/generate`, { mode: 'send', operation: t.operation, content: t.prompt,
      model_id: t.modelId, parent_id: t.parentId, source_id: t.sourceId ?? null, attachments: t.attachments ?? [], media: t.media ?? null });
  }
  /**
   * A no-save turn: you hold the transcript and send it each time; nothing is written to the conversation tables.
   * @param {{ messages: { role: 'user' | 'assistant', content: string, attachments?: string[] }[], modelId: string }} t
   * @param {{ onEvent?: (e: StreamEvent) => void, signal?: AbortSignal }} [opts]
   */
  sendNoSave(t, opts) { return this.stream('/api/chat/ephemeral/generate', { messages: t.messages, model_id: t.modelId }, opts); }

  // ---- files and media -------------------------------------------------------------------------------------------
  /** Upload before sending; pass the returned id in `attachments`. @param {File | Blob} file @param {{ noSave?: boolean, name?: string }} [o] @returns {Promise<Attachment>} */
  async upload(file, o = {}) {
    const fd = new FormData();
    fd.append('file', file, o.name ?? /** @type {any} */ (file).name ?? 'upload');
    const r = await fetch(`${this.base}/api/chat/attachments${o.noSave ? '?ephemeral=true' : ''}`,
      { method: 'POST', credentials: 'same-origin', headers: this.headers('POST'), body: fd });
    if (!r.ok) await this.fail(r);
    return r.json();
  }
  attachmentUrl(id) { return `${this.base}/api/chat/attachments/${id}/content`; }
  job(id) { return this.get(`/api/media/jobs/${id}`); }
  cancelJob(id) { return this.post(`/api/media/jobs/${id}/cancel`); }
  mediaUrl(assetId) { return `${this.base}/api/media/assets/${assetId}/content`; }
  /** Branding images and model icons uploaded by administrators. */
  assetUrl(name) { return `${this.base}/api/assets/${name}`; }

  // ---- theme -----------------------------------------------------------------------------------------------------
  /**
   * The token values to paint, from me() and themeTokens(). A person's own Light or Dark is kept as chosen; 'Match
   * device', the organisation default (when the organisation asks) and Custom's two versions follow the device. These
   * are the same rules as the built-in interface.
   * @param {any} me the result of me() @param {any} spec themeTokens()
   * @returns {{ name: string, variant: 'light' | 'dark', values: Record<string, string> }}
   */
  resolveTheme(me, spec, prefersDark = typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches) {
    const b = me.ui?.theme ?? {};
    // the server falls back to the default when a saved choice is no longer allowed, so only a match is personal
    const personal = !!b.active && b.active === me.user?.prefs?.theme;
    let name = b.active || b.default || 'light';
    const follow = name === 'system' || (!!b.follow_system && (name === 'custom' || (!personal && name === (b.default || 'light'))));
    if (name === 'system') name = prefersDark ? 'dark' : 'light';
    if (name === 'custom' && !b.custom) name = 'light';
    if (follow && (name === 'light' || name === 'dark')) name = prefersDark ? 'dark' : 'light';
    const variant = name === 'custom' ? (follow && prefersDark ? 'dark' : 'light') : name === 'light' ? 'light' : 'dark';
    const values = name === 'custom' ? { ...spec.presets[variant].values, ...(b.custom?.[variant] ?? {}) } : spec.presets[name].values;
    return { name, variant, values };
  }
  /** Write token values as CSS custom properties (`--bg`, `--fg`, ...) on an element, usually document.documentElement. */
  paint(el, values, variant) {
    for (const [k, v] of Object.entries(values)) el.style.setProperty(`--${k}`, v);
    el.style.setProperty('color-scheme', variant);
  }
}
