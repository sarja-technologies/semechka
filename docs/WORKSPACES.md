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
* **Sandbox**: see `sandbox/setup/README.md`. It runs as a locked, sudo-less account with rootless Docker, no network at
  all, read-only system, memory/process/CPU/time limits, thrown away after every call. `scripts/sandbox_probe.py` re-runs 18
  hostile checks against it.

## What it does not do (yet)

No OCR or audio/video transcription; images and scans are stored but not searchable. No automatic agent image/video
generation tool (use the Image and Video pages). Reranking is not implemented. The sandbox is namespace/seccomp/cgroup
isolation, not a virtual machine.
