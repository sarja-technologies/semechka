// The organisation being administered: its people, groups and roles, loaded once and shared by its tabs.
import { get } from '$lib/api';

export const org = $state<{ o: any; members: any[]; groups: any[]; roles: any[]; perms: any[]; loading: boolean; error: string }>({ o: null, members: [], groups: [], roles: [], perms: [], loading: false, error: '' });
export const ocan = (p: string) => !!org.o?.my_permissions?.includes(p);
export const pname = (id: string) => org.members.find((m) => m.user_id === id)?.display_name ?? (id ? 'Former member' : 'System');
export const rname = (id: string) => org.roles.find((r) => r.id === id)?.label ?? id;

export async function loadOrg(oid: string) {
  org.loading = true; org.error = '';
  try {
    org.o = await get(`/api/orgs/${oid}`);
    const [members, groups, roles] = await Promise.all([get(`/api/orgs/${oid}/members`), get(`/api/orgs/${oid}/groups`), get(`/api/orgs/${oid}/roles`)]);
    org.members = members; org.groups = groups; org.roles = roles.roles; org.perms = roles.permissions;
  } catch (e: any) { org.o = null; org.error = e?.status === 404 ? 'This organisation does not exist, or you are not a member of it.' : (e?.message ?? 'Could not load the organisation.'); }
  finally { org.loading = false; }
}
export const reloadOrg = () => (org.o ? loadOrg(org.o.id) : Promise.resolve());
