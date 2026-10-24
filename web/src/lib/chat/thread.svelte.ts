// One open conversation: the visible branch (loaded in slices, newest first), the reply being written, and every way a
// turn can be taken (text, image, video, regenerate, edit, switch version). The view components only render this.
import { get, post, patch, stream, upload, qs, ApiError } from '$lib/api';
import { upsert, touch } from './store.svelte';

export type Attachment = { id: string; name: string; kind: 'image' | 'text' | string; mime: string; size: number;
  width?: number | null; height?: number | null; saved?: boolean; generated?: boolean; missing?: boolean };
export type Msg = {
  id: string; parent_id: string | null; role: 'user' | 'assistant' | string; kind: 'text' | 'image' | 'video' | string;
  content: string; status: string; created_at: number; model: string | null; model_id: string | null;
  meta: { trimmed?: number; operation?: string; source?: string; error?: string };
  tech: Record<string, any>; attachments?: Attachment[]; media?: any; sources?: any[];
  siblings?: { index: number; count: number; prev: string | null; next: string | null } | null;
  local?: boolean; // a no-save message held only in this tab
};
export type MsgActions = {
  copy: (m: Msg) => void; edit: (m: Msg, text: string) => void; regenerate: (m: Msg) => void; version: (id: string) => void;
  details?: (m: Msg) => void; useSource?: (a: any) => void; mediaChanged: (m: Msg, job: any) => void;
  icon: (modelId: string | null) => any;
};
export type Live = {
  mode: 'send' | 'regenerate' | 'edit'; anchor: string | null; userText: string; userAttachments: Attachment[];
  text: string; reasoning: string; status: 'sending' | 'streaming' | 'stopping'; error: string; model: string;
  queue: null | { position: number; ahead: number; waiting: number; running: number; limit: number };
  sources?: any[]; skipped?: number; trimmed?: number;
};
export type Turn = { content: string; attachments: Attachment[]; operation: 'text' | 'image' | 'video'; modelId: string;
  modelLabel: string; media?: Record<string, unknown>; sourceId?: string | null };

const SLICE = 40;
export const errText = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');

export class Thread {
  workspace: 'chat' | 'code';
  id = $state<string | null>(null);
  head = $state<any>(null);
  messages = $state<Msg[]>([]);
  total = $state(0);
  hasMore = $state(false);
  loading = $state(false);
  loadingOlder = $state(false);
  error = $state('');
  notFound = $state(false);
  ephemeral = $state(false);
  live = $state<Live | null>(null);
  outcome = $state('');
  sendError = $state('');
  /** Bumped whenever the person moves to another conversation. A request that finishes later must not touch the screen. */
  private epoch = 0;
  private ctrl: { abort: () => void; done: Promise<void> } | null = null;
  /** The id this tab just created, so the route change it causes does not reload what is already on screen. */
  skipLoad = '';

  constructor(workspace: 'chat' | 'code') { this.workspace = workspace; }

  get busy() { return this.live !== null; }
  get title() { return this.ephemeral ? 'No-save conversation' : this.head?.title ?? 'New conversation'; }
  get last(): Msg | undefined { return this.messages[this.messages.length - 1]; }

  reset(ephemeral = false) {
    this.epoch++; this.ctrl?.abort(); this.ctrl = null; this.live = null;
    this.id = null; this.head = null; this.messages = []; this.total = 0; this.hasMore = false;
    this.error = ''; this.notFound = false; this.sendError = ''; this.outcome = ''; this.ephemeral = ephemeral;
  }

  private take(t: any) {
    const { messages, leaf: _l, total, start: _s, has_more, ...head } = t;
    this.head = head; this.messages = messages; this.total = total; this.hasMore = has_more;
  }

  async open(id: string) {
    this.reset(false);
    const mine = this.epoch;
    this.id = id; this.loading = true;
    try {
      const t = await get(`/api/chat/conversations/${id}/thread${qs({ limit: SLICE })}`);
      if (mine !== this.epoch) return;
      this.take(t);
    } catch (e) {
      if (mine !== this.epoch) return;
      this.notFound = e instanceof ApiError && e.status === 404;
      this.error = this.notFound ? 'This conversation does not exist or is not yours.' : errText(e);
    } finally { if (mine === this.epoch) this.loading = false; }
  }

  /** Earlier messages of the same branch. Returns how many were added (the view keeps its scroll position). */
  async loadOlder(): Promise<number> {
    if (!this.id || !this.hasMore || this.loadingOlder || !this.messages.length) return 0;
    const mine = this.epoch; this.loadingOlder = true;
    try {
      const t = await get(`/api/chat/conversations/${this.id}/thread${qs({ limit: SLICE, before: this.messages[0].id })}`);
      if (mine !== this.epoch) return 0;
      this.messages = [...t.messages, ...this.messages];
      this.hasMore = t.has_more; this.total = t.total;
      return t.messages.length;
    } catch (e) { if (mine === this.epoch) this.sendError = errText(e); return 0; }
    finally { if (mine === this.epoch) this.loadingOlder = false; }
  }

  /** Show another version of a message (and everything that followed it in that version). */
  async showVersion(target: string) {
    if (!this.id || this.busy) return;
    const mine = this.epoch;
    try {
      const t = await post(`/api/chat/conversations/${this.id}/branch`, { message_id: target, limit: Math.max(SLICE, this.messages.length) });
      if (mine === this.epoch) this.take(t);
    } catch (e) { if (mine === this.epoch) this.sendError = errText(e); }
  }

  /** Replace one message in place (a media result finished, for example). */
  patchMessage(id: string, fields: Partial<Msg>) {
    const i = this.messages.findIndex((m) => m.id === id);
    if (i >= 0) this.messages[i] = { ...this.messages[i], ...fields };
  }

  async saveContext(assistantId: string | null, collectionIds: string[]) {
    if (!this.id || this.ephemeral) return; // a new conversation sends them with its first message
    await patch(`/api/chat/conversations/${this.id}`, { assistant_id: assistantId, collection_ids: collectionIds });
  }

  private async ensure(modelId: string, ctx: { assistantId: string | null; collectionIds: string[] }, onCreated: (id: string) => void) {
    if (this.id) return this.id;
    const c = await post('/api/chat/conversations', { model_id: modelId || null, mode: this.workspace, assistant_id: ctx.assistantId, collection_ids: ctx.collectionIds });
    this.id = c.id; this.head = { ...c, context: { assistant_id: ctx.assistantId, collection_ids: ctx.collectionIds } };
    this.skipLoad = c.id;
    upsert(c);
    onCreated(c.id);
    return c.id as string;
  }

  /** Upload a file before sending. No-save conversations keep it in server memory only. */
  async attach(file: File): Promise<Attachment> {
    return upload(`/api/chat/attachments${qs({ ephemeral: this.ephemeral || undefined })}`, file);
  }

  stop() {
    if (!this.live || !this.ctrl) return;
    this.live.status = 'stopping'; this.ctrl.abort();
  }

  // ---------------------------------------------------------------------------------------------------------------
  async send(turn: Turn, ctx: { assistantId: string | null; collectionIds: string[] }, onCreated: (id: string) => void) {
    if (this.busy) return;
    if (turn.operation !== 'text') return this.media('send', turn, null, ctx, onCreated);
    return this.text('send', turn, null, ctx, onCreated);
  }
  async regenerate(m: Msg, turn: Turn) {
    if (this.busy) return;
    const ctx = { assistantId: null, collectionIds: [] };
    if (m.kind === 'image' || m.kind === 'video') return this.media('regenerate', turn, m, ctx, () => {});
    return this.text('regenerate', turn, m, ctx, () => {});
  }
  async edit(m: Msg, turn: Turn) {
    if (this.busy) return;
    const ctx = { assistantId: null, collectionIds: [] };
    if (turn.operation !== 'text') return this.media('edit', turn, m, ctx, () => {});
    return this.text('edit', turn, m, ctx, () => {});
  }

  /** Messages before the one a regenerate or edit replaces. */
  private before(anchor: Msg | null) {
    if (!anchor) return this.messages;
    const i = this.messages.findIndex((x) => x.id === anchor.id);
    return i >= 0 ? this.messages.slice(0, i) : this.messages;
  }

  private async text(mode: Live['mode'], turn: Turn, anchor: Msg | null, ctx: { assistantId: string | null; collectionIds: string[] }, onCreated: (id: string) => void) {
    this.sendError = ''; this.outcome = '';
    const mine = this.epoch, noSave = this.ephemeral;
    const live: Live = { mode, anchor: anchor?.id ?? null, userText: turn.content, userAttachments: turn.attachments, text: '',
      reasoning: '', status: 'sending', error: '', model: turn.modelLabel, queue: null };
    this.live = live;
    const cur = this.live; // the proxied object: writes below update the view
    let final: any = null;
    try {
      let url: string, body: any;
      const atts = turn.attachments.map((a) => a.id);
      if (noSave) {
        const hist = this.before(mode === 'send' ? null : anchor).map((m) => ({
          role: m.role, content: m.kind === 'image' || m.kind === 'video' ? `[${m.kind === 'image' ? 'Picture' : 'Video'} generated from: ${m.content}]` : m.content,
          attachments: (m.attachments ?? []).map((a) => a.id) }));
        if (mode === 'regenerate') { while (hist.length && hist[hist.length - 1].role === 'assistant') hist.pop(); }
        else hist.push({ role: 'user', content: turn.content, attachments: atts });
        url = '/api/chat/ephemeral/generate';
        body = { messages: hist, model_id: turn.modelId, workspace: this.workspace, assistant_id: ctx.assistantId, collection_ids: ctx.collectionIds };
      } else {
        const id = await this.ensure(turn.modelId, ctx, onCreated);
        url = `/api/chat/conversations/${id}/generate`;
        body = mode === 'send' ? { mode, operation: 'text', content: turn.content, model_id: turn.modelId, parent_id: this.last?.id ?? null, attachments: atts }
          : mode === 'edit' ? { mode, operation: 'text', content: turn.content, parent_id: anchor!.id, model_id: turn.modelId, attachments: atts }
          : { mode, parent_id: anchor!.id, model_id: turn.modelId };
      }
      this.ctrl = stream(url, body, (ev) => {
        if (mine !== this.epoch) return;
        if (ev.type === 'start') cur.status = 'streaming';
        else if (ev.type === 'delta') { if (ev.content) cur.text += ev.content; if (ev.reasoning) cur.reasoning += ev.reasoning; }
        else if (ev.type === 'queue') cur.queue = ev.running === true ? null : ev;
        else if (ev.type === 'sources') { cur.sources = ev.sources; cur.skipped = ev.skipped; }
        else if (ev.type === 'trimmed') cur.trimmed = ev.dropped;
        else if (ev.type === 'error') cur.error = ev.message;
        else if (ev.type === 'done') final = ev;
      });
      await this.ctrl.done;
      if (mine !== this.epoch) return; // the person moved on; a saved reply is stored server-side regardless
      const kept = this.before(mode === 'send' ? null : anchor);
      if (noSave) {
        const now = Date.now() / 1000;
        const out: Msg[] = [...kept];
        if (mode !== 'regenerate') out.push(localMsg('user', turn.content, { attachments: turn.attachments }));
        const text = final?.content ?? cur.text;
        if (text || cur.error) out.push(localMsg('assistant', text, { status: cur.error ? 'error' : final?.status ?? 'stopped', model: turn.modelLabel,
          model_id: turn.modelId, tech: final?.tech ?? {}, sources: final?.sources, meta: final?.trimmed ? { trimmed: final.trimmed } : {}, created_at: now }));
        this.messages = out;
      } else if (final?.message) {
        // regenerate keeps the user message; the parent's other versions now include this one
        this.replaceTail(kept, mode === 'regenerate' ? [final.message] : [final.user_message, final.message].filter(Boolean));
      } else if (this.id) {
        // stopped from this tab: the server stores what was written so far; show exactly what it stored
        await new Promise((r) => setTimeout(r, 300));
        if (mine !== this.epoch) return;
        await this.reload();
      }
      if (cur.error) this.sendError = cur.error;
      this.outcome = cur.error ? `Reply failed. ${cur.error}` : cur.status === 'stopping' || final?.status === 'stopped' ? 'Reply stopped' : 'Reply complete';
      if (!noSave && mode === 'send' && final?.user_message && !final.user_message.parent_id) this.named(turn.content);
    } catch (e) {
      if (mine === this.epoch) { this.sendError = errText(e); this.outcome = `Reply failed. ${this.sendError}`; }
    } finally {
      if (mine === this.epoch) { this.live = null; this.ctrl = null; }
    }
  }

  private async media(mode: Live['mode'], turn: Turn, anchor: Msg | null, ctx: { assistantId: string | null; collectionIds: string[] }, onCreated: (id: string) => void) {
    this.sendError = ''; this.outcome = '';
    const mine = this.epoch;
    const kind = mode === 'regenerate' ? (anchor!.kind as 'image' | 'video') : (turn.operation as 'image' | 'video');
    this.live = { mode, anchor: anchor?.id ?? null, userText: mode === 'regenerate' ? '' : turn.content, userAttachments: turn.attachments,
      text: '', reasoning: '', status: 'sending', error: '', model: turn.modelLabel, queue: null };
    try {
      if (this.ephemeral) {
        // No-save: the job is not part of any stored conversation; its result is held briefly for viewing and download.
        if (turn.sourceId || turn.attachments.length) throw new ApiError(422, { message: 'Editing or animating a picture needs a saved conversation. Remove the picture or start a saved conversation.' });
        const prompt = mode === 'regenerate' ? anchor!.content : turn.content;
        const model = mode === 'regenerate' ? anchor!.model_id! : turn.modelId;
        const job = await post('/api/media/jobs', { kind, model_id: model, prompt, save: false, ...(turn.media ?? {}) });
        if (mine !== this.epoch) return;
        const kept = this.before(mode === 'send' ? null : anchor);
        const out = [...kept];
        if (mode !== 'regenerate') out.push(localMsg('user', prompt, { kind }));
        out.push(localMsg('assistant', prompt, { kind, status: 'pending', model: turn.modelLabel, model_id: model, media: job }));
        this.messages = out;
      } else {
        const id = await this.ensure(turn.modelId, ctx, onCreated);
        const body = mode === 'regenerate' ? { mode, parent_id: anchor!.id, model_id: turn.modelId || undefined, media: turn.media }
          : { mode: 'send', operation: kind, content: turn.content, model_id: turn.modelId, attachments: turn.attachments.map((a) => a.id),
              source_id: turn.sourceId || undefined, media: turn.media, parent_id: mode === 'edit' ? anchor!.parent_id : this.last?.id ?? null };
        const r = await post(`/api/chat/conversations/${id}/generate`, body);
        if (mine !== this.epoch) return;
        this.replaceTail(this.before(mode === 'send' ? null : anchor), mode === 'regenerate' ? [r.message] : [r.user_message, r.message].filter(Boolean));
        if (mode === 'send' && !r.user_message?.parent_id) this.named(turn.content);
      }
      this.outcome = kind === 'image' ? 'Picture requested' : 'Video requested';
    } catch (e) {
      if (mine === this.epoch) { this.sendError = errText(e); this.outcome = `Request failed. ${this.sendError}`; }
    } finally {
      if (mine === this.epoch) this.live = null;
    }
  }

  async reload() {
    if (!this.id) return;
    const mine = this.epoch;
    try {
      const t = await get(`/api/chat/conversations/${this.id}/thread${qs({ limit: Math.max(SLICE, this.messages.length + 2) })}`);
      if (mine === this.epoch) this.take(t);
    } catch (e) { if (mine === this.epoch) this.sendError = errText(e); }
  }

  /** Swap everything after `kept` for the messages the server just stored. */
  private replaceTail(kept: Msg[], add: Msg[]) {
    this.total = this.total - (this.messages.length - kept.length) + add.length;
    this.messages = [...kept, ...add];
    if (this.id) touch(this.id);
  }

  /** The server names a conversation after the first words of its first message (routers/chat.py); mirror it. */
  private named(content: string) {
    const title = content.split(/\s+/).filter(Boolean).slice(0, 7).join(' ').slice(0, 80) || 'Conversation';
    if (this.head) this.head.title = title;
    if (this.id) upsert({ id: this.id, title });
  }
}

let localSeq = 0;
function localMsg(role: string, content: string, f: Partial<Msg> = {}): Msg {
  return { id: `local_${++localSeq}`, parent_id: null, role, kind: 'text', content, status: 'complete', created_at: Date.now() / 1000,
    model: null, model_id: null, meta: {}, tech: {}, local: true, ...f };
}
