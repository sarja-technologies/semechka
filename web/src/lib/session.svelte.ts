import { get, post, setCsrf, ApiError } from './api';

export type Brand = Record<string, any>;
export const app = $state({
  ready: false,
  needsSetup: false,
  brand: {} as Brand,
  signup: { open: false, needs_invite: false, requires_approval: false, message: '' },
  privacyNotice: '',
  me: null as any,
  offline: false,
  themePref: 'system' as string
});

export const can = (perm: string) => !!app.me?.permissions?.includes(perm) || (!!app.me?.permissions && app.me.permissions.some((p: string) => p === '*'));
export const canAny = (...perms: string[]) => perms.some(can);

export async function loadPublic() {
  try {
    const p = await get('/api/public');
    app.needsSetup = p.needs_setup; app.brand = p.brand; app.signup = p.signup; app.privacyNotice = p.privacy_notice; app.offline = false;
    applyBrand(app.brand);
  } catch (e) { if (e instanceof ApiError && e.status === 0) app.offline = true; else throw e; }
}

export async function loadMe(): Promise<boolean> {
  try {
    const me = await get('/api/me');
    app.me = me; setCsrf(me.csrf);
    applyBrand({ ...app.brand, ...me.ui.brand }, me.ui);
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { app.me = null; return false; }
    if (e instanceof ApiError && e.status === 0) { app.offline = true; return false; }
    throw e;
  }
}

export async function logout() { try { await post('/api/auth/logout'); } catch { /* already signed out */ } app.me = null; setCsrf(''); }

/** Apply organisation branding as CSS variables and document title. All values were validated by the server. */
export function applyBrand(b: Brand, ui?: any) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  const pref = (app.me?.user?.prefs?.theme) || ui?.theme || b.theme_default || 'paper';
  app.themePref = pref;
  const theme = pref === 'system' ? (matchMedia('(prefers-color-scheme: dark)').matches ? 'carbon' : 'paper') : pref;
  root.dataset.theme = theme;
  root.dataset.density = app.me?.user?.prefs?.density || ui?.density || b.density || 'comfortable';
  if (b.radius !== undefined) { root.style.setProperty('--radius', `${b.radius}px`); root.style.setProperty('--radius-sm', `${Math.max(2, Math.round(b.radius * 0.75))}px`); }
  // Admin accent only overrides the first-party accent; it was contrast-checked when saved.
  const dark = theme !== 'paper';
  const accent = dark ? b.accent_dark : b.accent_light;
  if (accent) { root.style.setProperty('--accent', accent); root.style.setProperty('--accent-soft', `color-mix(in srgb, ${accent} 14%, var(--surface))`); }
  if (b.tab_title) document.title = b.tab_title;
  // The administrator's favicon replaces the built-in one; removing it restores the built-in one.
  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (icon) {
    icon.dataset.builtin ??= icon.getAttribute('href') ?? '/favicon.svg';
    icon.removeAttribute('type');
    icon.setAttribute('href', b.favicon_asset ? `/api/assets/${b.favicon_asset}` : icon.dataset.builtin);
  }
}
