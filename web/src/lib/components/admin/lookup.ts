import { get } from '$lib/api';
export type Opt = { id: string; label: string };
let roles: Promise<Opt[]> | null = null, teams: Promise<Opt[]> | null = null;
export const loadRoles = (fresh = false) => (roles = !fresh && roles ? roles : get('/api/admin/roles').then((r: any[]) => r.map((x) => ({ id: x.id, label: x.label }))));
export const loadTeams = (fresh = false) => (teams = !fresh && teams ? teams : get('/api/admin/teams').then((r: any[]) => r.map((x) => ({ id: x.id, label: x.name }))));
export const loadUsers = () => get('/api/admin/users?per_page=200&sort=email&order=asc').then((r) => r.items.map((u: any) => ({ id: u.id, label: `${u.display_name} <${u.email}>` })) as Opt[]);
/** A model access rule as the server stores it: modes null means every mode. */
export type Rule = { subject_type: 'everyone' | 'role' | 'team' | 'user'; subject_id: string; effect: 'allow' | 'deny'; modes: string[] | null };
