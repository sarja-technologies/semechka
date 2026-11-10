# Work Mode on Goose: evidence of a real run (Spark, 2026-11-10)

Everything below was produced by `scripts/spark/goose_e2e.py` against **real components**: a dev Semechka instance (port 8794,
fresh data dir, two people `alice` and `bob`, started with `systemd-run --user` from this branch), the rootless Docker runtime
of account `semsbx` through `semechka-sandbox-relay`, the image `semechka-goose:1` (upstream Goose 1.58.1 binary, unmodified), and the
llama.cpp router with model `qwen3.6-35b-a3b` (verified `coding_agent_compatible`). Runs were wrapped in `~/spark-reset/gpu-llm`; the
model was unloaded by the router (idle) before the last check; nothing of this workstream stays loaded.
Raw material: `docs/evidence/work-goose/` (`RUN-LOG.txt` = the script's output, `events-*.jsonl` = the event stream the browser would
receive, `repo-diff.txt`, `repo-pytest-after.txt`, `audit-work-goose.json`).

| Requirement | Result | Evidence |
|---|---|---|
| Inspect a small repo in the workspace, modify a file, run its test, report | **Done.** Goose listed the tree, read the files, ran pytest (2 tests, 1 failing), found `round(subtotal + tax_rate, 2)`, fixed it, re-ran: `2 passed`. 90 s wall clock including model load. | `RUN-LOG.txt` (repo), `events-repo.jsonl`, `repo-diff.txt`: `-    return round(subtotal + tax_rate, 2)` / `+    return round(subtotal * (1 + tax_rate), 2)`; an independent `pytest` run inside the container afterwards: `2 passed` (`repo-pytest-after.txt`) |
| Approval flow, **approve** | 13 approvals answered `allow_once` over the HTTP API; Goose waited on each, then ran the tool (`approval_request` -> `approval_resolved` -> `tool_end`). Goose issued several permission questions in parallel (4 at once); all were relayed. | `events-repo.jsonl`, audit entries `work.goose.approval` |
| Approval flow, **deny** | The first `write` of `shop/pricing.py` was answered `deny`: `tool_end` `failed`, the file unchanged; the turn continued and Goose switched to `edit`, which was approved. | `RUN-LOG.txt` (`write ... -> deny`, then `edit ... -> allow_once`), audit `decision: deny, tool: write` |
| Turn-level file diff | `files_changed` event `[shop/pricing.py modified +1 -1]`, full unified diff from `GET .../changes/{turn_id}`; `edit` also carries `tool_end.diff`. | `events-repo.jsonl`, `repo-diff.txt` |
| **Cancel** | A running `sleep 300` shell tool: `POST .../cancel` -> `turn_end{cancelled}` 0.4 s later; the `sleep` process count in the container went 1 -> 0; session returned to `idle`. | `RUN-LOG.txt` (cancel), `events-cancel.jsonl` |
| **Resume after dev-instance restart** | `systemctl --user restart work-dev`; afterwards `GET .../session` showed the same `goose_session_id` (`20261110_1`) and `GET .../transcript` returned the earlier history (66 items). The next prompt started a new `goose acp` process which resumed the session (`session/load`; notice `resumed`) and the model answered from the earlier context with no tool call: "...changing `subtotal + tax_rate` to `subtotal * (1 + tax_rate)`". The router had unloaded the model meanwhile, so this run also shows the **model-loading state**: `state: loading_model`, `model_loading loading` -> `ready` after 14.2 s. | `RUN-LOG.txt` (restart), `events-restart.jsonl` |
| **MCP tool call** | Session created with allowlisted server `repo-stats`; Goose started it in the container (`/opt/semechka/mcp/repo_stats.py`, PID visible under `goose acp`) and called `repo-stats__repo_stats` (`extension: repo-stats`), then reported the Python line count (13 lines in 3 files). | `RUN-LOG.txt` (MCP), `events-mcp.jsonl` |
| **Skill applied** | A personal skill `pricing-review` was exported to `~/.agents/skills/pricing-review/SKILL.md`; Goose called its own `load_skill` tool (`name: pricing-review`) and followed it: the answer ended with the required sign-off line `PRICING-REVIEW-SIGNED-OFF`. | `RUN-LOG.txt` (skill), `events-skill.jsonl` |
| Audit | Session create/update/stop, prompt (chars, model; no text), approval (decision, tool, extension), cancel recorded in the hash-chained audit log. | `audit-work-goose.json` |

## Isolation proof (two people; probes from inside the containers and from the agent itself)

`RUN-LOG.txt` (isolation) in short:

* **HTTP level.** `bob` against `alice`'s conversation: `GET session`, `transcript`, `events`, `files`, `files/content` and `POST prompt` all answer **404**.
* **Containers.** Alice's container, as the user Goose's shell runs as (`uid=65534 nobody`): her own file is readable; Bob's file does not exist anywhere
  (`find / -name bob-secret.txt` empty, `cat /work/../gwork-<bob>/bob-secret.txt` -> No such file); Bob's container cannot see Alice's file either.
  `/home/spark` does not exist inside the container (`ls`, `cat .bash_history` -> No such file or directory; `/home/spark/semechka-deploy`, `/run/user` likewise).
* **Network.** Only `lo`. Connections to the router on the LAN IP (<spark-lan-ip>:8080), loopback 8080, loopback 22, 8.8.8.8:53 and Spark Control (8830) all fail
  (`OSError` / `ConnectionRefusedError`). The environment holds no router key (names only: no `*_KEY` except Goose's dummy and none with the real value).
* **Model gateway.** Through the only open path, the unix socket: `POST /v1/embeddings` -> 404; a request for `gpt-oss-120b` -> 403 "not available to this session";
  `GET /v1/models` lists only the session's model.
* **Container flags** (docker inspect): `NetworkMode=none readonly=true CapDrop=[ALL] pids=512 mem=4294967296 user=65534:65534 privileged=false`.
* **The agent itself** (Goose in `auto` mode, so it asked nothing) was told to run `ls -la /home/spark`, `cat /home/spark/.bash_history`, a filesystem-wide
  `find / -name bob-secret.txt` and `cat /work/../bob-secret.txt`. All failed or found nothing, and Goose reported exactly that.

## Findings that changed the code during the run

* A model/tool/skill change between prompts restarts the Goose process; the first version reported the planned exit as `goose_exited`. Fixed (`expected_close`) and covered by a unit test.
* Approvals can arrive in parallel; the session object carries a list and the contract was extended.
* A container needs `/opt/semechka` to be traversable by uid 65534 (`COPY --chmod` had made it 0644): fixed in the Dockerfile.
* `PrivateTmp=true` / `ProtectHome=read-only` in the production units conflict with the socket directory (see the deployment checklist in `WORK_MODE_GOOSE_SECURITY.md`).

## Not verified

* The browser UI (this workstream has no UI); the contract was exercised with a Python client over `format=json` long polling and, in unit tests, SSE.
* The other three verified models (`qwen3-coder-next`, `qwen3.8-27b`, `gpt-oss-120b`) were not run through Semechka (model quality was verified by the orchestrator with Goose directly).
* Roasted/Salted (production) were not touched; the dev instance used a fresh data directory, not a copy of staging data.
* A hard kill of the service while a prompt runs: covered by the startup recovery unit test (`turn_end{interrupted}`), not by a live `kill -9` on the Spark.
* Several people using several models at once, and router LRU eviction while a Work prompt is in flight (the router spares models with a stream in flight; a model that is loaded but idle between prompts can be evicted and is reloaded with the `loading_model` state).
