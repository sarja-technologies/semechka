// The live state of one conversation's Work session: availability, the session, the folded timeline and every action a person can take on it.
// It only ever shows what the server reported (status, session, events); nothing is simulated.
import { ApiError } from '../api';
import * as W from './client';
import { apply, emptyFold, fromTranscript, type Folded } from './fold';
import type { Decision, GooseSession, GooseStatus } from './types';

const msg = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');

export class WorkRun {
  /** loading: asking the server; unavailable: it cannot be used (see `why`); empty: usable, no session yet; ready: a session exists */
  phase = $state<'loading' | 'unavailable' | 'empty' | 'ready'>('loading');
  why = $state<W.Unavailable | null>(null);
  status = $state<GooseStatus | null>(null);
  session = $state<GooseSession | null>(null);
  fold = $state.raw<Folded>(emptyFold());
  conn = $state<'off' | 'live' | 'retrying'>('off');
  error = $state('');
  sending = $state(false);
  /** what the next session will be created with (chosen before the first prompt, then changed through PATCH) */
  model = $state('');
  autonomy = $state('');

  cid: string | null = null;
  private stop: (() => void) | null = null;
  private buf: any[] = [];
  private raf = 0;
  private epoch = 0;

  get state() { return this.fold.state; }
  get busy() { return this.sending || ['starting', 'loading_model', 'running', 'awaiting_approval', 'cancelling'].includes(this.fold.state); }

  /** Open for a conversation (null = a conversation that has not been saved yet: nothing to load but the options). */
  async open(cid: string | null) {
    this.close();
    const mine = ++this.epoch;
    this.cid = cid; this.phase = 'loading'; this.error = ''; this.session = null; this.fold = emptyFold(); this.why = null;
    try {
      const st = await W.gooseStatus();
      if (mine !== this.epoch) return;
      this.status = st;
      if (!st.enabled) { this.phase = 'unavailable'; this.why = { kind: 'disabled', message: 'Work Mode is turned off on this server.' }; return; }
      this.model ||= st.models.find((m) => m.default)?.id ?? st.models[0]?.id ?? '';
      this.autonomy ||= st.autonomy.find((a) => a.default)?.id ?? st.autonomy[0]?.id ?? '';
    } catch (e) { if (mine === this.epoch) { this.phase = 'unavailable'; this.why = W.unavailableFrom(e); } return; }
    if (!cid) { this.phase = 'empty'; return; }
    try {
      const s = await W.getSession(cid);
      if (mine !== this.epoch) return;
      await this.adopt(s, mine);
    } catch (e) {
      if (mine !== this.epoch) return;
      if (e instanceof ApiError && e.status === 404) { this.phase = 'empty'; return; }
      this.phase = 'unavailable'; this.why = W.unavailableFrom(e);
    }
  }

  private async adopt(s: GooseSession, mine: number) {
    const t = await W.transcript(s.conversation_id).catch(() => ({ items: [], last_seq: 0 }));
    if (mine !== this.epoch) return;
    this.session = s; this.model = s.model; this.autonomy = s.autonomy;
    const f = fromTranscript(t.items, t.last_seq);
    // questions still waiting must be on screen even if the history did not list them (several can be open at once)
    for (const a of s.pending_approvals ?? []) if (!f.items.some((i) => i.k === 'approval' && i.approval.id === a.id)) f.items.push({ key: `p${a.id}`, k: 'approval', approval: a });
    this.fold = { ...f, state: s.state, turn: s.turn?.id ?? null };
    this.phase = 'ready';
    this.follow(s.conversation_id, t.last_seq);
  }

  private follow(cid: string, since: number) {
    this.stop?.();
    this.stop = W.followEvents(cid, since, (e) => this.queue(e), {
      onState: (c) => (this.conn = c),
      onGiveUp: (e) => { this.conn = 'off'; this.error = e.status === 404 ? 'This Work session no longer exists.' : e.message; }
    });
  }

  private queue(e: any) {
    this.buf.push(e);
    if (this.raf) return;
    const flush = () => { this.raf = 0; const b = this.buf; this.buf = []; if (b.length) this.fold = apply(this.fold, b); };
    this.raf = typeof requestAnimationFrame === 'function' ? requestAnimationFrame(flush) : (setTimeout(flush, 50) as unknown as number);
  }

  close() {
    this.epoch++; this.stop?.(); this.stop = null; this.conn = 'off';
    if (this.raf && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(this.raf);
    this.raf = 0; this.buf = [];
  }

  /** Send a prompt. A session is created first when there is none; `ensure` saves the conversation when it is still new. */
  async send(text: string, ensure: () => Promise<string>): Promise<boolean> {
    if (this.busy || !text.trim()) return false;
    this.error = ''; this.sending = true;
    try {
      const cid = this.cid ?? (await ensure());
      if (!this.session) {
        const s = await W.createSession(cid, { ...(this.model ? { model: this.model } : {}), ...(this.autonomy ? { autonomy: this.autonomy } : {}) });
        this.cid = cid; this.session = s; this.phase = 'ready';
        this.follow(cid, 0);
      }
      await W.prompt(cid, text.trim());
      return true;
    } catch (e) { this.error = msg(e); return false; }
    finally { this.sending = false; }
  }

  /** The model list says which models are loaded; it changes while the agent works, so it is read again when a turn ends. */
  async refreshStatus() { try { this.status = await W.gooseStatus(); } catch { /* keep what was shown */ } }

  async cancel() {
    if (!this.cid) return;
    try { await W.cancelTurn(this.cid); } catch (e) { this.error = msg(e); }
  }

  async decide(approvalId: string, d: Decision) {
    if (!this.cid) return;
    try { await W.answer(this.cid, approvalId, d); } catch (e) {
      // someone (another tab, the timeout) already closed it: the event will say how, so this is not an error to show
      if (e instanceof ApiError && e.status === 409) return;
      this.error = msg(e);
    }
  }

  async change(b: { model?: string; autonomy?: string }) {
    if (b.model) this.model = b.model;
    if (b.autonomy) this.autonomy = b.autonomy;
    if (!this.cid || !this.session) return;
    try { this.session = await W.patchSession(this.cid, b); } catch (e) { this.error = msg(e); if (this.session) { this.model = this.session.model; this.autonomy = this.session.autonomy; } }
  }

  async stopProcess() {
    if (!this.cid) return;
    try { await W.stopSession(this.cid); } catch (e) { this.error = msg(e); }
  }
  async destroy() {
    if (!this.cid) return;
    try { await W.deleteSession(this.cid); this.stop?.(); this.stop = null; this.session = null; this.fold = emptyFold(); this.phase = 'empty'; this.conn = 'off'; } catch (e) { this.error = msg(e); }
  }
}
