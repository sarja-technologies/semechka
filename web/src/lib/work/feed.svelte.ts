// The live timeline of one session. It long-polls GET /sessions/{id}/events?since=<last id>&wait=25 in a loop.
// Refreshing the page, losing the network or switching tabs never loses or repeats an event: every poll resumes from
// the highest id already held, and events are merged by id.
import { pollEvents, problem } from './client';
import { mergeEvents, cursorOf } from './events';
import type { WorkEvent } from './types';

export type FeedPhase = 'loading' | 'live' | 'retrying' | 'offline' | 'denied' | 'notfound' | 'error';

export class Feed {
  events = $state.raw<WorkEvent[]>([]);
  phase = $state<FeedPhase>('loading');
  message = $state('');
  fails = $state(0);
  /** Called with the events that were new to this feed (not the ones already held). */
  onnew?: (fresh: WorkEvent[]) => void;

  private gen = 0;
  private cursor = 0;
  private ctrl: AbortController | null = null;
  private wake: (() => void) | null = null;
  private id = '';
  private fast = true;

  start(id: string) {
    this.stop();
    this.id = id; this.events = []; this.cursor = 0; this.phase = 'loading'; this.message = ''; this.fails = 0; this.fast = true;
    const gen = ++this.gen;
    void this.run(gen);
  }

  stop() { this.gen++; this.ctrl?.abort(); this.wake?.(); this.ctrl = null; }

  /** Ask for events now: used when the tab becomes visible again or the network returns. */
  poke() { this.fast = true; this.ctrl?.abort(); this.wake?.(); }

  private sleep(ms: number) { return new Promise<void>((res) => { const t = setTimeout(res, ms); this.wake = () => { clearTimeout(t); res(); }; }); }

  private async run(gen: number) {
    while (gen === this.gen) {
      const ctrl = (this.ctrl = new AbortController());
      const wait = this.fast ? 0 : 25;
      this.fast = false;
      try {
        const r = await pollEvents(this.id, this.cursor, wait, ctrl.signal);
        if (gen !== this.gen) return;
        const incoming = Array.isArray(r) ? r : (r?.events ?? []);
        const known = new Set(this.events.map((e) => e.id));
        const fresh = incoming.filter((e) => typeof e?.id === 'number' && !known.has(e.id));
        this.events = mergeEvents(this.events, incoming);
        this.cursor = cursorOf(this.events, Array.isArray(r) ? undefined : r?.last_id);
        this.fails = 0; this.phase = 'live'; this.message = '';
        if (fresh.length) this.onnew?.(fresh);
      } catch (e: any) {
        if (gen !== this.gen) return;
        if (e?.name === 'AbortError') continue; // poke(): go round again straight away
        const p = problem(e);
        if (p.kind === 'denied' || p.kind === 'notfound') { this.phase = p.kind; this.message = p.message; return; }
        this.fails++;
        this.phase = p.kind === 'offline' ? 'offline' : 'retrying';
        this.message = p.message;
        await this.sleep(Math.min(15000, 1000 * 2 ** (this.fails - 1)));
        this.fast = true;
      }
    }
  }
}
