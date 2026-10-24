export const toasts = $state<{ id: number; kind: 'ok' | 'bad' | 'info'; text: string }[]>([]);
let n = 0;
export function toast(text: string, kind: 'ok' | 'bad' | 'info' = 'ok', ms = 4500) {
  const id = ++n; toasts.push({ id, kind, text });
  setTimeout(() => { const i = toasts.findIndex((t) => t.id === id); if (i >= 0) toasts.splice(i, 1); }, ms);
}
