// Web search for models whose runtime identity offers it (Yaob systems, contract yaob-origin/1). The choice is the
// person's own preference, saved on the account (PATCH /api/me), so it follows them to every conversation and device.
// The server sends it only to models that report web_search; every other model never sees it.
import { patch } from '$lib/api';
import { app } from '$lib/session.svelte';
import { asMode, type WebMode } from './web';

export { WEB_MODES, activityText, type WebMode } from './web';

export const webMode = (): WebMode => asMode(app.me?.user?.prefs?.web_search);

/** Saves the choice; the screen follows at once and goes back if the server refuses. */
export async function setWebMode(v: WebMode) {
  const prefs = app.me?.user?.prefs;
  if (!prefs) return;
  const was = prefs.web_search;
  prefs.web_search = v;
  try { await patch('/api/me', { web_search: v }); }
  catch (e) { prefs.web_search = was; throw e; }
}

/** The mode to send with a turn for this catalogue row, or undefined when the model does not offer web search. */
export const webFor = (row: any): WebMode | undefined => (row?.web_search ? webMode() : undefined);
