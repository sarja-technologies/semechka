// The workspace being viewed and the people/groups/roles of its organisation, loaded once and shared by its tabs.
import { get } from '$lib/api';

export type Cur = { ws: any; org: any; members: any[]; groups: any[]; roles: any[]; perms: any[]; loading: boolean; error: string };
export const cur = $state<Cur>({ ws: null, org: null, members: [], groups: [], roles: [], perms: [], loading: false, error: '' });

export const can = (p: string) => !!cur.ws?.my_permissions?.includes(p);
export const orgCan = (p: string) => !!cur.org?.my_permissions?.includes(p);
export const person = (id: string) => cur.members.find((m) => m.user_id === id)?.display_name ?? 'Former member';
export const groupName = (id: string) => cur.groups.find((g) => g.id === id)?.name ?? 'Deleted group';
export const roleLabel = (id: string) => cur.roles.find((r) => r.id === id)?.label ?? id;

export async function loadWorkspace(wid: string) {
  cur.loading = true; cur.error = '';
  try {
    const ws = await get(`/api/workspaces/${wid}`);
    const org = await get(`/api/orgs/${ws.org_id}`);
    cur.ws = ws; cur.org = org;
    // directory data is best-effort: a person who can see the workspace may not be allowed every list
    const [members, groups, roles] = await Promise.all([
      get(`/api/orgs/${ws.org_id}/members`).catch(() => []), get(`/api/orgs/${ws.org_id}/groups`).catch(() => []),
      get(`/api/orgs/${ws.org_id}/roles`).catch(() => ({ roles: [], permissions: [] }))]);
    cur.members = members; cur.groups = groups; cur.roles = roles.roles ?? []; cur.perms = roles.permissions ?? [];
  } catch (e: any) {
    cur.ws = null; cur.error = e?.status === 404 ? 'This workspace does not exist, or you do not have access to it.' : (e?.message ?? 'Could not load the workspace.');
  } finally { cur.loading = false; }
}

export async function refreshWorkspace() { if (cur.ws) await loadWorkspace(cur.ws.id); }
