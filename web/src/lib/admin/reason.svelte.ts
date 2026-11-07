// The stated reason for administrator access, kept in memory for this browsing session only (never in storage). The dialog that asks for it is
// components/admin/ReasonDialog.svelte, mounted once by the explorer page.
import { ReasonRegister, reasonOk } from './explorer';

const register = new ReasonRegister();
export const reasonUi = $state<{ open: boolean; title: string; detail: string; value: string; resolve: ((r: string | null) => void) | null }>({
  open: false, title: '', detail: '', value: '', resolve: null
});

/** The reason to send for this access, asking first when needed. Resolves to null when the person cancels (nothing is then requested or recorded). */
export function askReason(kind: 'browse' | 'item', key: string, title: string, detail: string): Promise<string | null> {
  if (!register.needs(kind, key)) return Promise.resolve(register.last);
  reasonUi.resolve?.(null);
  return new Promise((resolve) => {
    reasonUi.title = title; reasonUi.detail = detail; reasonUi.value = register.last;
    reasonUi.resolve = (r) => { if (r !== null && reasonOk(r)) register.confirm(key, r); resolve(r); };
    reasonUi.open = true;
  });
}
export function answerReason(r: string | null) {
  const f = reasonUi.resolve; reasonUi.resolve = null; reasonUi.open = false; f?.(r);
}
/** The session reason, for a follow-up in a context already confirmed (a file inside an item that was just opened). */
export const currentReason = () => register.last;
export const forgetReason = () => register.clear();
