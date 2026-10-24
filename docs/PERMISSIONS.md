# Permission matrix

Generated from `backend/semechka/perms.py`. Evaluation: a permission is allowed iff some role/team/user rule grants it and **no** rule denies it. `*` grants every permission except those marked sensitive. Reading technical information (`tech.*`), operating infrastructure (`ops.*`) and administering (`admin.*`) are separate families.

## Role templates (starting points; every role is editable and renamable, ids are stable)

| Permission | Owner | Platform Admin | Organisation Admin | Model Operator | Queue Operator | Compliance Reviewer | Team Manager | Member | Guest |
|---|---|---|---|---|---|---|---|---|---|
| **Modes** | | | | | | | | | |
| `mode.chat` — Use chat | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| `mode.code` — Use code workspace | yes | yes | yes | – | – | – | yes | yes | – |
| `mode.image` — Generate images | yes | – | – | – | – | – | – | – | – |
| `mode.video` — Generate video | yes | – | – | – | – | – | – | – | – |
| `mode.audio` — Use audio (speech) | yes | – | – | – | – | – | – | – | – |
| `mode.knowledge` — Use knowledge search | yes | yes | yes | – | – | – | yes | yes | – |
| `mode.agents` — Use assistants (no tool execution) | yes | yes | yes | – | – | – | yes | yes | – |
| **Chat** | | | | | | | | | |
| `chat.ephemeral` — Start no-save conversations | yes | yes | yes | – | – | – | yes | – | – |
| `chat.attach` — Attach files to chat | yes | yes | yes | – | – | – | yes | yes | – |
| `chat.export` — Export own conversations | yes | yes | yes | – | – | – | yes | yes | – |
| **Technical visibility** | | | | | | | | | |
| `tech.model.id` — See real model ID | yes | yes | – | yes | – | – | – | – | – |
| `tech.model.family` — See model family / architecture | yes | yes | – | yes | – | – | – | – | – |
| `tech.model.params` — See parameter count | yes | yes | – | yes | – | – | – | – | – |
| `tech.model.quant` — See quantisation and file format | yes | yes | – | yes | – | – | – | – | – |
| `tech.model.context` — See context window | yes | yes | – | yes | – | – | – | – | – |
| `tech.model.license` — See model licence | yes | yes | – | yes | – | – | – | – | – |
| `tech.runtime.name` — See runtime / backend name | yes | yes | – | yes | – | – | – | – | – |
| `tech.runtime.version` — See runtime version | yes | yes | – | yes | – | – | – | – | – |
| `tech.gen.params` — See effective generation settings | yes | yes | – | yes | – | – | – | – | – |
| `tech.usage.tokens` — See token counts | yes | yes | – | yes | – | – | – | – | – |
| `tech.perf.ttft` — See time to first token | yes | yes | – | yes | yes | – | – | – | – |
| `tech.perf.tps` — See tokens per second | yes | yes | – | yes | yes | – | – | – | – |
| `tech.perf.duration` — See request duration | yes | yes | – | yes | yes | – | – | – | – |
| `tech.queue.position` — See queue position and state | yes | yes | – | yes | yes | – | – | – | – |
| `tech.node.name` — See which node served the request | yes | yes | – | yes | – | – | – | – | – |
| `tech.resources.gpu` — See GPU load, VRAM and temperature | yes | yes | – | yes | – | – | – | – | – |
| `tech.resources.host` — See CPU, memory and storage | yes | yes | – | yes | – | – | – | – | – |
| `tech.job.trace` — See trace identifiers | yes | yes | – | yes | – | – | – | – | – |
| **Operations** | | | | | | | | | |
| `ops.gen.edit_params` — Change generation parameters | yes | yes | – | – | – | – | – | – | – |
| `ops.model.select_actual` — Choose the underlying model/node | yes | yes | – | yes | – | – | – | – | – |
| `ops.model.load` — Load / unload models | yes | yes | – | yes | – | – | – | – | – |
| `ops.model.install` — Install / remove models | yes | yes | – | yes | – | – | – | – | – |
| `ops.jobs.cancel_own` — Cancel own jobs | yes | yes | yes | – | yes | – | yes | yes | – |
| `ops.jobs.cancel_others` — Cancel other people's jobs | yes | yes | – | – | yes | – | – | – | – |
| `ops.jobs.reprioritise` — Change queue priority | yes | yes | – | – | yes | – | – | – | – |
| `ops.node.manage` — Manage nodes | yes | yes | – | – | – | – | – | – | – |
| **Administration** | | | | | | | | | |
| `admin.overview` — Open the admin console | yes | yes | yes | yes | yes | yes | yes | – | – |
| `admin.branding` — Edit identity and branding | yes | yes | yes | – | – | – | – | – | – |
| `admin.navigation` — Edit layout and navigation | yes | yes | yes | – | – | – | – | – | – |
| `admin.users.view` — View people | yes | yes | yes | – | – | – | yes | – | – |
| `admin.users.manage` — Create, suspend and edit people | yes | yes | yes | – | – | – | – | – | – |
| `admin.users.invite` — Create invitations | yes | yes | yes | – | – | – | yes | – | – |
| `admin.teams.manage` — Manage teams | yes | yes | yes | – | – | – | yes | – | – |
| `admin.roles.manage` — Edit roles and permissions | yes | yes | – | – | – | – | – | – | – |
| `admin.models.manage` — Edit model catalogue, aliases and access | yes | yes | – | yes | – | – | – | – | – |
| `admin.backends.manage` — Manage inference backends | yes | yes | – | yes | – | – | – | – | – |
| `admin.policy.edit` — Edit privacy, retention and policy settings | yes | yes | yes | – | – | – | – | – | – |
| `admin.signup.edit` — Edit registration and sign-in settings | yes | yes | yes | – | – | – | – | – | – |
| `admin.quotas.edit` — Edit quotas | yes | yes | yes | – | – | – | – | – | – |
| `admin.settings.edit` — Edit any other setting | yes | yes | – | – | – | – | – | – | – |
| `admin.settings.view` — View settings and history | yes | yes | yes | – | – | yes | – | – | – |
| `admin.audit.view` — View audit log | yes | yes | yes | – | – | yes | – | – | – |
| `admin.system.view` — View system and node health | yes | yes | – | yes | yes | – | – | – | – |
| `admin.backup.manage` — Manage backups | yes | yes | – | – | – | – | – | – | – |
| `admin.preview_role` — Preview the product as another role | yes | yes | yes | – | – | – | – | – | – |
| `admin.sso.manage` — Configure single sign-on providers | yes | yes | – | – | – | – | – | – | – |
| `admin.content.read` — Read other people's conversations | – | – | – | – | – | – | – | – | – |

## Technical-visibility presets (editable shortcuts)

- **simple**: no technical fields
- **informed**: `tech.model.family`, `tech.perf.tps`, `tech.model.context`, `tech.queue.position`
- **technical**: `tech.model.id`, `tech.model.family`, `tech.model.params`, `tech.model.quant`, `tech.model.context`, `tech.runtime.name`, `tech.gen.params`, `tech.usage.tokens`, `tech.perf.ttft`, `tech.perf.tps`, `tech.perf.duration`, `tech.queue.position`
- **expert**: `tech.*`
