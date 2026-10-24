import { get, post, setCsrf, ApiError } from './api';
import { applyTheme, type ThemeBundle } from './theme/theme';

export type Brand = Record<string, any>;
export const app = $state({
  ready: false,
  needsSetup: false,
  brand: {} as Brand,
  signup: { open: false, needs_invite: false, requires_approval: false, message: '' },
  privacyNotice: '',
  me: null as any,
  offline: false,
  themePref: 'light' as string,
  themeName: 'light' as string,
  themeVariant: 'light' as 'light' | 'dark',
  publicTheme: null as ThemeBundle | null
});

export const can = (perm: string) => !!app.me?.permissions?.includes(perm) || (!!app.me?.permissions && app.me.permissions.some((p: string) => p === '*'));
export const canAny = (...perms: string[]) => perms.some(can);

export async function loadPublic() {
  try {
    const p = await get('/api/public');
    app.needsSetup = p.needs_setup; app.brand = p.brand; app.signup = p.signup; app.privacyNotice = p.privacy_notice; app.offline = false;
    app.publicTheme = p.theme ?? null;
    applyBrand(app.brand); refreshTheme();
  } catch (e) { if (e instanceof ApiError && e.status === 0) app.offline = true; else throw e; }
}

export async function loadMe(): Promise<boolean> {
  try {
    const me = await get('/api/me');
    app.me = me; setCsrf(me.csrf);
    applyBrand({ ...app.brand, ...me.ui.brand }); refreshTheme();
    return true;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { app.me = null; return false; }
    if (e instanceof ApiError && e.status === 0) { app.offline = true; return false; }
    throw e;
  }
}

export async function logout() {
  // The SSO endpoint ends the local session for everyone and says where to go if the identity provider has its own sign-out.
  let redirect: string | null = null;
  try { redirect = (await post('/api/sso/logout'))?.redirect ?? null; } catch { /* already signed out */ }
  app.me = null; setCsrf('');
  if (redirect && typeof location !== 'undefined') location.assign(redirect);
}

/** Brand identity on the document: tab title and favicon. All values were validated by the server. */
export function applyBrand(b: Brand) {
  if (typeof document === 'undefined') return;
  if (b.tab_title) document.title = b.tab_title;
  // The administrator's favicon replaces the built-in one; removing it restores the built-in one.
  const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
  if (icon) {
    icon.dataset.builtin ??= icon.getAttribute('href') ?? '/favicon.svg';
    icon.removeAttribute('type');
    icon.setAttribute('href', b.favicon_asset ? `/api/assets/${b.favicon_asset}` : icon.dataset.builtin);
  }
}

/** The theme this person sees: their own choice when the organisation allows one, else the organisation's default. */
export function refreshTheme() {
  const t: ThemeBundle = app.me?.ui?.theme ?? app.publicTheme ?? {};
  const choice = app.me ? app.me.ui.theme.active : undefined;
  app.themePref = choice ?? t.default ?? 'light';
  // the server falls back to the default when the saved choice is no longer allowed, so only a match is personal
  const personal = !!choice && choice === app.me?.user?.prefs?.theme;
  const r = applyTheme(choice, t, app.me?.user?.prefs?.density || app.me?.ui?.density || t.density, personal);
  if (r) { app.themeName = r.name; app.themeVariant = r.variant; }
}
