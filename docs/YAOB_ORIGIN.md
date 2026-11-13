# Yaob Origin in Semechka (0.12.4)

Yaob Origin is Fylume's hybrid system: a Qwen3.5-4B neural core plus the "Yaob Origin" Modular Persistent Network
(node network, designation N119M at 119,176,878 nodes). This release lets Semechka show it under the name the server
reports at run time, offer its live web search in the chat, and keep the documents a reply cites. The interface
contract is `yaob-origin/1` (Yaob repository, `docs/origin/CONTRACT.md`, sections 1 to 5). Every other model and
server behaves exactly as before. Schema unchanged (11): no migration.

## What Semechka consumes

| Where | What the Yaob server sends | What Semechka does |
| --- | --- | --- |
| `/v1/models` entry | `name`, `yaob` (identity record) | `meta.display_name`, `meta.identity` (core, network, capabilities; validated and bounded), `meta.capabilities` |
| model sync | the same, on every sync | a new row is named from `name` (unless another row on that backend already shows that name); an existing row whose alias equals the previous runtime name follows the new one (N119M -> N120M); an alias an administrator typed is kept |
| stream chunk `{"yaob": {"status": ...}}` | `searching_web` (+`query`), `reading_sources` (+`count`), `verifying` | SSE `{"type": "activity", "state", "query"?, "count"?}` to the browser |
| stream chunk `{"yaob": {"sources": [...]}}` | cited documents | http(s) links only, no credentials in URLs, control and bidi characters removed, titles 300, snippets 500, at most 20; SSE `sources` (merged after any knowledge passages), stored encrypted in `message_sources` |
| stream chunk `{"yaob": {"provenance": {"id"}}}` | the server's record id | `messages.meta.provenance`; shown as "Provenance ID" in the technical drawer to holders of `tech.job.trace` |
| `route`, `validation`, `timing` chunks | debugging | ignored (as before) |

Malformed `yaob` values (strings, lists, unknown states, bad URLs, non-JSON lines) are ignored; the reply continues.

## What Semechka sends

`web_search: "auto" | "on" | "off"` in the chat completions body, **only** for models whose `meta.capabilities`
contains `web_search`, and only from the chat router (`_web_mode`). Default `auto`. Models without the capability
never receive the key, whatever the browser sends.

Browser -> Semechka: `POST /api/chat/conversations/{id}/generate` and `POST /api/chat/ephemeral/generate` accept
`"web_search": "auto" | "on" | "off"` (anything else: 422). The composer sends it only for models whose catalogue row
has `"web_search": true`; the person's choice is the account preference `PATCH /api/me {"web_search": ...}`
(null clears it), so it follows them across conversations and devices.

## Interface

- **Composer**: one control next to the attach button, only for models that offer web search: "Web search: Auto"
  (phones: the globe and "Auto"). It opens a three-item menu (Auto, On, Off, each with one line saying what it does).
- **While the reply has not started**: the reply header says "Searching the web…", then "Reading 3 sources…"
  ("Checking the answer…" for `verifying`), in the same quiet state line the queue and model loading use.
- **Under the answer**: "3 sources example.org, example.com, docs.example.net", collapsed; expanded, a numbered list
  of titles (links, new tab, `rel="noopener noreferrer nofollow"`) with site and publication date. Knowledge
  passages keep their existing place above the answer.
- **Inline markers**: `[n]` becomes a small link to source n only when source n exists; any other marker, and
  anything in code or an existing link, is left as text. Built with DOM nodes, http(s) only.
- **Names**: the picker label is the model's alias, which follows the runtime display name (see sync above).
  With technical visibility the picker's detailed view says "Core: Qwen3.5-4B · Network: Yaob Origin N119M", and the
  technical drawer lists Core and Network as separate rows. Node counts are never shown as parameters.
- The source list and inline links follow the existing "Source and tool indicators" setting (`ui.tool_indicators`).
- Exports (Markdown and JSON) carry the cited sources.

## Tests and evidence

- Backend: `backend/tests/test_yaob_origin.py` (17 tests) against `backend/tests/fake_yaob.py`, a double that sends
  exactly the contract's chunks (also runnable on its own: `cd backend && ../.venv/bin/python -m tests.fake_yaob --port 8899`).
- Web: `web/src/lib/chat/web.test.ts` (`pnpm test`).
- Browser: `web/shots-yaob-origin.mjs` and `web/shots-yaob-details.mjs` against a dev instance; screenshots in
  `docs/evidence/yaob-origin/` (1440 x 900 and 390 x 844, light and dark).

Development setup used for the evidence:

```
cd backend && ../.venv/bin/python -m tests.fake_yaob --port 8899 --delay 1.6 &
../.venv/bin/python -m uvicorn fakebackend:app --app-dir tests --port 8898 &
SEMECHKA_DATA_DIR=/tmp/sem-yaob ../.venv/bin/python -c "from semechka.cli import main; main()" serve --port 8780 &
cd ../web && SEMECHKA_API=http://127.0.0.1:8780 pnpm dev
```

## Deploying to Salted (prepared, not run)

Order matters: the Yaob Origin server must already answer on `127.0.0.1:8843` (with `/api/identity` and the legacy
id `yaob-c4b-mpn14b` in `/v1/models`) before step 4. Steps 1 to 3 are safe while 0.2.1 is still running: Semechka
0.12.4 treats a server without the extension exactly like 0.12.3 did.

```
export SPARK_SSH="ssh spark" SPARK_SCP="scp" SPARK_HOST_IP=192.0.2.10
```

**1. Build on the workstation** (the Spark has no Node): from this worktree, on branch `feature/yaob-origin-web`

```
./scripts/package.sh                       # web build + wheel -> dist/semechka-0.12.4.tar.gz and .sha256
( cd dist && sha256sum -c semechka-0.12.4.tar.gz.sha256 )
```

**2. Back up Salted** (data only read; program files copied)

```
$SPARK_SSH bash -s -- salted 0.12.4-yaob-origin < scripts/spark/backup_instance.sh     # prints RECOVERY POINT ... integrity=ok
$SPARK_SSH 'cp -a ~/semechka-deploy/salted/app ~/semechka-deploy/backups/salted-app-pre-0.12.4-$(date +%Y%m%d-%H%M%S)'
$SPARK_SSH 'ls ~/semechka-deploy/salted/app/releases | sort -V | tail -2'               # 0.12.3 must be listed (the rollback target)
```

The installer also takes its own snapshot in `salted/data/pre-update/<time>-from-0.12.3/`.

**3. Install on Salted only** (uploads and verifies the tarball, installs, restarts `semechka-salted` only)

```
./scripts/deploy_spark.sh dist/semechka-0.12.4.tar.gz salted
$SPARK_SSH '~/semechka-deploy/salted/app/venv/bin/semechka --version'                   # semechka 0.12.4
curl -fsS http://$SPARK_HOST_IP:8788/api/health
```

**4. Rename the Yaob row from the runtime identity** (script in the Yaob repository,
`deploy/spark/semechka_register_origin.py`; uses Semechka's own write session, model sync, vault and audit log;
keeps the model id and its grants; never prints secrets)

```
$SPARK_SCP /mnt/development/yaob-origin/deploy/spark/semechka_register_origin.py spark:semechka-deploy/stage-scripts/
$SPARK_SSH 'SEMECHKA_DATA_DIR=~/semechka-deploy/salted/data ~/semechka-deploy/salted/app/venv/bin/python -I \
  ~/semechka-deploy/stage-scripts/semechka_register_origin.py --dry-run'
#   shows: backend "Yaob C4B + MPN-14B 0.2.1" -> "<display_name> (Qwen3.5-4B core)", model alias "Yaob C4B + MPN-14B" -> "<display_name>",
#          the identity it read, and which ids the sync would add (disabled) or mark unavailable
$SPARK_SSH 'SEMECHKA_DATA_DIR=~/semechka-deploy/salted/data ~/semechka-deploy/salted/app/venv/bin/python -I \
  ~/semechka-deploy/stage-scripts/semechka_register_origin.py'
```

It refuses to run when the backend row does not point at `http://127.0.0.1:8843/v1`, when the model row is not
`yaob-c4b-mpn14b` (`m_79a6a103e89a485ab105`) on that backend, when the server reports no display name, or when the
sync stored no identity record. Before changing anything it writes the previous names (and the model's previous
metadata) to `salted/data/yaob-origin-registration.json` (0600). A disabled row on the same backend that shows the
same name (the newly discovered `yaob-origin`) is set back to its identifier so the picker never lists two entries
with one name. Other Yaob backends ("Yaob N10K (MPN Studio)", "Yaob C4B-N10K Next 0.2.0") are not touched.

**5. Check**

```
$SPARK_SSH 'sqlite3 "file:$HOME/semechka-deploy/salted/data/semechka.db?mode=ro" \
  "select m.id, m.alias, m.enabled, b.name from models m join backends b on b.id=m.backend_id where m.id=\"m_79a6a103e89a485ab105\""'
```

Then in a browser at `http://$SPARK_HOST_IP:8788`: the picker shows the runtime name, the composer shows
"Web search: Auto" for it (and nothing for other models), a time-sensitive question shows "Searching the web…",
sources appear under the answer with working `[n]` links, and an older Yaob conversation still opens.

**Roasted** is the disposable sandbox; repeat steps 2 to 5 with `roasted` only if it should run 0.12.4. Its model
ids differ: look them up read-only first and pass `--model-id`/`--backend-name` to the script.

## Rollback

| What | Command (on the Spark) |
| --- | --- |
| Names only | `SEMECHKA_DATA_DIR=~/semechka-deploy/salted/data ~/semechka-deploy/salted/app/venv/bin/python -I ~/semechka-deploy/stage-scripts/semechka_register_origin.py --rollback` (add `--dry-run` to preview). Restores the recorded backend name, alias and model metadata; deletes nothing (rows added by the sync stay, disabled). |
| Program | `~/semechka-deploy/releases/semechka-0.12.4/install.sh --name salted --prefix ~/semechka-deploy/salted/app --data ~/semechka-deploy/salted/data --rollback` reinstalls the recorded 0.12.3 wheel and restarts Salted. The schema is unchanged, so no `--restore-data` is needed; 0.12.3 ignores the new metadata keys. |
| Data (last resort) | the installer's `salted/data/pre-update/<time>-from-0.12.3/` snapshot (`--restore-data`), or the recovery point from step 2. Either loses replies written after the update. |

Do the name rollback first if both are wanted (the script needs the 0.12.4 program).

## Known limits

- Inline links appear on the finished reply; while a reply is still streaming its `[n]` markers are plain text.
- Web sources and knowledge passages are numbered independently; inline markers link to web sources.
- No staging deployment was made: staging (port 8790) has no running service, and the Origin server was not yet
  answering with an identity record on the Spark.
