# Cloud workspaces: guide for people who run and use them

Cloud workspaces let several organisations (or one company with many departments) share files, knowledge and AI agents on
one Semechka server, with different people holding different rights. Everything in this guide is implemented and tested;
`docs/ENTERPRISE.md` records the evidence and `docs/THREAT_MODEL.md` the security reasoning.

## Switching it on (administrators)

Three switches in **Administration, Settings** (all start **off**; nothing changes for anyone until you turn them on):

| Setting | Effect |
|---|---|
| `cloud.workspaces.enabled` | Adds **Workspaces** to navigation for people who belong to an organisation |
| `cloud.agents.enabled` | Lets people run configured agents |
| `sandbox.enabled` | Lets agents run Python / command-line tools in the isolated sandbox. Needs the one-time host setup in `sandbox/setup/README.md` |

### Administrators first (`cloud.rollout`)

`cloud.rollout` decides who the switches apply to. **Everyone** (the default) is the behaviour above. **Administrators only**
keeps the whole cloud surface to people who may edit platform settings: the Workspaces entry is hidden for everyone else, every
workspace, file, context, organisation and agent endpoint answers an ordinary account `403 rollout` (even one that was added
to an organisation), and the service refuses to start an agent run for them. Unauthenticated callers still get `401`.
Set it to `admins` **before** turning a cloud switch on in production, test as an administrator, and set it back to `everyone`
only when you mean to open the feature to all; nothing is created, moved or lost by either change, and every change is audited.

Optional: `embeddings.url` + `embeddings.model` turn on search **by meaning** next to keyword search (see below).
**Administration, Cloud workspaces** shows the switches, queue, measured durations, memory and storage at a glance.

## The model in one picture

Platform → **Organisation** → departments/groups → **Workspace** (nestable) → folders and files, **contexts**, **agents**, tasks.

* An **organisation** has its own people, roles, policy, limits and activity log. Existing installs get one **Default
  organisation** containing everyone; creating a second one switches automatic joining off.
* Organisation roles (Owner, Administrator, Department Manager, Member) manage people, groups, roles, policy and quotas.
  **They do not open anyone's files.** Reading content is only ever granted by a *workspace* role or an explicit rule.
* Workspace roles: **Viewer** (read, download), **Contributor** (add files, run agents), **Editor** (change, delete, restore,
  edit contexts, memory), **Workspace Administrator** (members, policy, agents, restricted items, approvals). You can make
  your own roles. A workspace can be **Private** (invisible to non-members) or **Shared** (listed to the organisation,
  contents still limited to members). Sub-workspaces inherit their parent's members unless you turn that off.
* **Deny always wins**, wherever it comes from: a role, an explicit rule on a file/context, the workspace's policy ceiling,
  or the organisation's. "Restricted" folders, files and contexts open only to people with an explicit rule (and workspace
  administrators).

## Using a workspace

* **Files**: folders, upload (drag and drop), create and edit text documents, rename, move, version history with restore,
  trash with restore. New files are read in the background so they can be searched; each row says whether it is
  *Searchable*, *Indexing*, or *Not searchable* (pictures, scans and video have no text in this release). Search shows the
  exact file, version and location of every passage, and whether it matched by keyword, by meaning, or both.
* **Contexts**: named, versioned sets of files, such as "Brand guidelines" or "Client Alpha". Add files from the workspace,
  attach a context to other workspaces (this never widens who can read it), export/import, restore an earlier version,
  and try a question against it.
* **Agents**: give an agent instructions, a model, the tools it may use, the contexts it may search, whether it remembers
  between tasks, and when it must ask a person. Then give it a task. It keeps working if you close the page; its steps,
  tool calls, files it saved, sources and usage are all recorded and shown on the task page. **Tasks** lists everything
  running or finished. Agents queue behind people who are chatting.
  * An agent can never do more than the person who started it, its own tool list and the workspace rules allow, checked again
    on every step. Revoking someone's access stops their running agents at the next step.
  * Actions that run code, run commands or reach the internet **wait for approval** by default; you can require approval for
    every action, or (not recommended with code tools) for none.
  * What the agent says it did is checked: if the answer names a file the run did not create, a **Not verified** warning is
    shown beside it.
  * Memory holds only what was saved on purpose (by the agent, or typed in by a person). It is shown, editable and deletable,
    and can expire.
* **Access** (administrators): members and groups with roles, specific allow/deny rules, and **"Why can or can't someone do
  this?"** which explains any decision. **Settings**: visibility, what is switched off for everyone in the workspace, which
  models are allowed, storage limits, usage, archive.

## Organisation administration

Open **Organisation** from the Workspaces page: **People** (add, change role, suspend, remove), **Groups** (nested
departments; managers look after their own group), **Roles** (edit the shipped ones or create your own with a full
permission matrix; content permissions are marked and can only be granted by name), **Policy and limits** (switch
things off for the whole organisation, choose allowed models, storage/workspace/agent/daily-run limits, retention), **Usage**
(measured: storage, agent work, tokens, sandbox seconds) and the **Activity log** (who changed access, membership, policy,
agents; never content).

Retention (per organisation): deleted files are removed after `trash_days` (30), older versions beyond `keep_versions`,
finished background work after `job_days` (30), file-access records after `access_log_days` (365).

### Keeping files to their owner (`file_access`)

By default a person's access to a file comes from their workspace role, so a contributor can overwrite a colleague's file (every
change is versioned). Workspace administrators can instead choose **Only the person who added it** under *Settings, Who can
open a file* (the policy key `file_access: "owner"`; it applies to the workspace and to child workspaces that inherit its
members). Then, for read, download, write, delete and restore:

* the owner (the uploader) keeps what their own role grants;
* nobody else is reached by a role, a workspace or folder rule, restricted-access, or an organisation administrator role;
* only an **allow rule that names that very file** opens it, and only the permissions the rule names; a deny rule still wins;
* a file with no recorded owner belongs to nobody, so it is closed to everyone except through such a rule;
* listing, search, retrieval, contexts and agents all follow, because they use the same check; an agent never sees more than the
  person who started it.

Recovery is the policy itself: an administrator who holds `ws.policy.edit` can set it back (audited); that does not reveal anything
by itself, it only returns access to the roles.

## Platform administration (people who run the server)

* **Administration, Organisations**: create an organisation (naming its first owner), see size, suspend or reactivate
  (nothing is deleted; reactivating restores it exactly).
* **Operator CLI** (needs shell access; deliberately not in the web API):
  * `semechka org list`
  * `semechka org export <slug> [--no-history]` writes a passphrase-sealed archive of one organisation's decrypted data
  * `semechka org purge <slug> --confirm <slug>` permanently removes one organisation; others are untouched
  * `semechka backup` / `restore` now cover the database, key, gallery, chat attachments **and workspace files**, as an
    authenticated stream (a truncated or altered backup is refused); older backups still restore
* **Search by meaning** needs an embeddings endpoint. The reference setup runs Qwen3-Embedding-0.6B through llama.cpp on
  loopback (`semechka-embed.service`). When it is down or unset, search falls back to keyword and says so.
* **Behind a reverse proxy.** Semechka starts uvicorn with `proxy_headers=False` and never reads `X-Forwarded-For`, so a client
  cannot forge its address, but the address it records is the proxy's. Consequences: the sign-in limiter (60 a minute per
  address) is shared by **everyone behind the proxy**, and audit and session records show the proxy address. Put rate limiting for
  untrusted clients in the proxy itself, bind Semechka to loopback or a private interface the proxy alone can reach, and do not
  enable forwarded-header trust in front of an address anyone else can reach. (`X-Forwarded-Proto` is read only to decide the
  `Secure` cookie flag.)
* **Sandbox relay.** The service unit's mount-namespace hardening makes the sandbox account's ids unmapped for the service, so it
  cannot open the runtime's own socket. `sandbox/setup/3-service-proxy.sh` installs `relay.py` as a user service that listens
  on `/run/user/<uid>/semechka-sandbox.sock` (mode 0600, directory only that account can enter) and forwards bytes to the
  runtime, honouring half-close. Set `sandbox.docker_host` to that path. Never mount or hand that socket (or the real one) to a
  container, an agent or a tool, never expose it on TCP, and keep the sandbox account locked and sudo-less. The relay does not
  inspect traffic: its safety is who can reach it, so confirm after setup that the socket is `srw-------` and owned by the
  service account.
* **Sandbox**: see `sandbox/setup/README.md`. It runs as a locked, sudo-less account with rootless Docker, no network at
  all, read-only system, memory/process/CPU/time limits, thrown away after every call. `scripts/sandbox_probe.py` re-runs 18
  hostile checks against it.

## What it does not do (yet)

No OCR or audio/video transcription; images and scans are stored but not searchable. No automatic agent image/video
generation tool (use the Image and Video pages). Reranking is not implemented. The sandbox is namespace/seccomp/cgroup
isolation, not a virtual machine.
