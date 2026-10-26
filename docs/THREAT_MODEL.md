# Threat model: cloud workspaces, agents and the code sandbox

Scope: the multi-organisation workspace layer, retrieval, cloud agents and the execution sandbox added in 0.4.2–0.5.0.
Each row names the control and the evidence that it holds. Evidence is an automated test or a probe run on the real
hardware; "not covered" is said where it is true. See also `docs/ENTERPRISE.md` (decisions, deployments, evidence log).

**Assets:** other organisations' documents and memory; the master key and database; the model engines and the machine's
shared memory; the availability of the service for everyone else.

**Actors considered:** a member of one organisation; an administrator of one organisation; the platform administrator; a
malicious or compromised model/agent; hostile document content; a hostile tenant running code in the sandbox; an outsider.

| # | Threat | Control | Evidence |
|---|--------|---------|----------|
| 1 | Cross-tenant disclosure | Every check starts with an *active membership of an active organisation* (`authz.Authz.check`); platform roles confer no membership; content encrypted under a per-organisation key (`org:<id>:content`/`files`/`vec`), so a wrongly-fetched row does not decrypt | `test_authz`, `test_orgs_http` (Scenario A), `test_files_http::two_companies…`, `test_semantic::vectors_are_encrypted_per_organisation`, `test_agents::other_organisations…` |
| 2 | Broken object-level authorisation by guessing ids | Anything the caller cannot see answers **404**, not 403, at both service and HTTP layers; ids from another organisation (roles, groups, principals, contexts) are rejected | probe tables in `test_files_http`, `test_agents`, `test_authz::service_calls_refuse_foreign_ids` |
| 3 | Administrators reading confidential content | Content permissions are `sensitive`: no wildcard grants them; organisation owners/admins hold none by name; platform administrators hold no membership; the operator-only CLI is the only export path | `test_authz::administrators_manage_people_not_content`, `wildcards_never_grant_content`, `admins_cannot_put_themselves_into_a_private_workspace`, `test_tenant_ops` |
| 4 | Retrieval returning chunks a user may not read | Filtering happens before decryption and ranking; the index cache is keyed by the exact permitted (file, version, passage, vector) set | `test_files_contexts::retrieval_cites…`, `cached_indexes_do_not_leak…`, `a_shared_index_would_let_hidden_passages_crowd_out…` (mutation-verified) |
| 5 | Stale access after revocation | Authority recomputed from the database on every request, every model turn and every tool call; queued jobs are re-authorised when they start; suspending an organisation takes effect immediately | `test_agents::revoking_access_while_queued…`, `…between_steps…` (mutation-verified), `test_cloud_ops::platform_admins_list_and_suspend…` |
| 6 | Agent privilege escalation | `AgentAuthz.check` = user check AND agent check (tools imply permissions; confined to its workspace; contexts allow-listed; no organisation administration); sensitive tools wait for a person; approval cannot grant what the person's role lacks | `test_agents::an_agent_never_exceeds…`, `agents_stay_in_their_workspace…`, `test_agent_tools::network_tool_needs_the_network_permission_and_approval`, mutation checks A–E |
| 7 | Prompt injection | Document text is framed as untrusted data; but the defence is structural: whatever the model says, tools outside its list are refused and every call passes the user∩agent gate | `test_agents::prompt_injection_cannot_widen_authority` |
| 8 | Model claims actions it did not take | Final answers are checked against what the run really created; unverified file claims are returned as `warnings`; truncated JSON is repaired only by adding closers, otherwise the model is told exactly why | `test_agents::claims_of_files_that_do_not_exist_are_flagged`, `truncated_json…`; found by the first real-model run |
| 9 | Path traversal | Blob paths come only from generated ids (regex + resolved-prefix check); user names are sanitised and never reach the filesystem | `test_files_contexts::names_never_reach_the_filesystem…` |
| 10 | Malicious uploads | Executables and extension/content mismatches refused; downloads are `attachment` with `nosniff` and a sandbox CSP; SVG/HTML never inline; per-file and per-workspace limits | `test_files_contexts`, `test_files_http::downloads_are_attachments_with_safe_headers` |
| 11 | Zip bombs / unsafe archives | Office files are zip-inspected (entry count, ratio, size, traversal, encryption) before any parser; archives are stored, never opened; backup restore refuses unsafe paths; context import accepts only names the manifest lists | `test_files_contexts::hostile_office_files…`, `test_backup::a_backup_with_unsafe_paths…` |
| 12 | SSRF via the network tool | Deny-by-default allow-list; every hop (including redirects) re-resolved and refused if private/loopback/link-local/metadata; response discarded if the connected peer was internal | `test_agent_tools::fetch_url_is_deny_by_default…` (mutation-verified). **Residual:** DNS-rebinding race can still deliver a blind GET to an internal host; the response is never returned |
| 13 | Unauthorised network from code | Sandbox containers have `--network none` and the daemon cannot create any bridge network | `scripts/sandbox_probe.py` (18/18 on the Spark): LAN, host loopback, Semechka, internet IP, metadata IP and DNS all fail; only `lo` exists |
| 14 | Sandbox escape / host tampering | Rootless daemon under a locked, sudo-less account; read-only root, non-root uid, zero capabilities, no-new-privileges, seccomp, no mounts/socket; memory/pids/CPU/time limits | same probe: read-only FS, CapEff 0, cannot mount/setuid/write sysctl, fork bomb capped, OOM killed, disk bounded. **Residual:** shared kernel (not a VM) |
| 15 | Resource exhaustion | Upload size and storage quotas; per-user active-run limit; per-organisation daily run quota; job concurrency per kind; sandbox concurrency and memory-aware admission; account-wide cgroup ceiling; agents queue behind chat | `test_files_contexts`, `test_cloud_ops`, probe, `systemctl show user-1001.slice` |
| 16 | Credential leakage | Engine tokens/keys never enter prompts, tool arguments or containers; sandbox env holds no secrets; audit drops sensitive keys; content never in logs/audit/jobs | `test_privacy_hygiene`, probe (`env` has no key/token/secret) |
| 17 | Unauthorised artifact download | Content routes re-check `file.download`; previews are inline only for safe types | `test_files_http`, `test_authz` |
| 18 | Data lost in backup/restore | Backups are streaming, authenticated per chunk (no silent truncation), include `workspaces/` and `attachments/`; restore verifies before replacing and keeps `.pre-restore` copies; old v1 backups still restore | `test_backup` (16 tests) |
| 19 | Tenant offboarding | `semechka org export` (sealed) and `org purge` (typed confirmation; blobs, rows, passages, vectors) leave other tenants intact | `test_tenant_ops` |

## Residual risks and limits (not hidden)

* **Kernel-level isolation.** Containers share the host kernel. A kernel or runtime vulnerability could cross the sandbox;
  the landing account holds no Semechka data, keys or credentials and is resource-capped. For hostile multi-tenant code add a
  VM boundary (gVisor, Kata, Firecracker): not available on this host today.
* **The operator is trusted.** Anyone with shell access to the server can read the master key and therefore all content.
  Encryption protects database copies, backups and disk images, not a compromised running host (as the base product states).
* **SSRF rebinding window** for `fetch_url` (row 12). Keep `agent.tool.network` off unless needed.
* **No OCR/transcription**: images, scans and video are stored but not searchable; the product says so.
* **Contributors may overwrite other people's files** in a workspace (no per-owner rule), though every change is versioned.
* **Approval is a gate, not a review of the model's reasoning.** A person approving `python` still has to read the code.
* **Side channels and denial of service by an authenticated tenant** within quotas are not otherwise modelled.
