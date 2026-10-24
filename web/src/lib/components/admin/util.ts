import { ApiError } from '$lib/api';
export const errMsg = (e: unknown) => (e instanceof ApiError ? e.message : e instanceof Error ? e.message : String(e));
export const fmtTime = (t?: number | null) => (t ? new Date(t * 1000).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—');
export const fmtBytes = (n?: number | null) => (n == null ? '—' : n > 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GiB` : `${(n / 2 ** 20).toFixed(0)} MiB`);
export const fmtParams = (n?: number | null) => (n == null ? '—' : n >= 1e9 ? `${(n / 1e9).toFixed(1)}B` : `${(n / 1e6).toFixed(0)}M`);
export const ADMIN_PAGES = [
  { href: '/admin', label: 'Overview', icon: 'list', perm: 'admin.overview', exact: true },
  { href: '/admin/branding', label: 'Identity & Branding', icon: 'palette', perm: 'admin.branding' },
  { href: '/admin/theme', label: 'Theme', icon: 'palette', perm: 'admin.branding' },
  { href: '/admin/navigation', label: 'Layout & Navigation', icon: 'nav', perm: 'admin.navigation' },
  { href: '/admin/people', label: 'People & Accounts', icon: 'users', perm: 'admin.users.view' },
  { href: '/admin/invites', label: 'Invites & Enrolment', icon: 'mail', perm: 'admin.users.invite' },
  { href: '/admin/teams', label: 'Teams', icon: 'users', perm: 'admin.users.view' },
  { href: '/admin/roles', label: 'Roles & Permissions', icon: 'key', perm: 'admin.users.view' },
  { href: '/admin/visibility', label: 'Technical Visibility', icon: 'eye', perm: 'admin.users.view' },
  { href: '/admin/models', label: 'Models', icon: 'cpu', perm: 'admin.models.manage' },
  { href: '/admin/backends', label: 'Backends', icon: 'server', perm: 'admin.backends.manage' },
  { href: '/admin/sso', label: 'Single sign-on', icon: 'key', perm: 'admin.sso.manage' },
  { href: '/admin/system', label: 'System & Nodes', icon: 'server', perm: 'admin.system.view' },
  { href: '/admin/queue', label: 'Queue', icon: 'list', perm: 'admin.system.view' },
  { href: '/admin/registration', label: 'Registration & Sign-in', icon: 'lock', perm: 'admin.settings.view', cat: 'Registration & Sign-in' },
  { href: '/admin/privacy', label: 'Privacy & Retention', icon: 'shield', perm: 'admin.settings.view', cat: 'Privacy & Retention' },
  { href: '/admin/quotas', label: 'Quotas', icon: 'usage', perm: 'admin.settings.view', cat: 'Quotas & Scheduling' },
  { href: '/admin/email', label: 'Email', icon: 'mail', perm: 'admin.settings.view', cat: 'Email' },
  { href: '/admin/access', label: 'Access & System', icon: 'settings', perm: 'admin.settings.view', cat: 'System' },
  { href: '/admin/settings', label: 'All settings', icon: 'search', perm: 'admin.settings.view' },
  { href: '/admin/history', label: 'History & Rollback', icon: 'history', perm: 'admin.settings.view' },
  { href: '/admin/transfer', label: 'Import / Export', icon: 'download', perm: 'admin.settings.view' },
  { href: '/admin/audit', label: 'Activity / Audit', icon: 'log', perm: 'admin.audit.view' }
];
