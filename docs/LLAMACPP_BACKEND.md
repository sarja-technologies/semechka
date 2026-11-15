# llama.cpp router backend (the Spark's single LLM API)

Semechka's chat, agents and embeddings can run on the Spark's llama.cpp router (`spark-llm.service`, `http://<spark>:8080/v1`)
instead of the Sarness Engine. The adapter id is `llamacpp` (`backend/semechka/adapters/llamacpp.py`). Sign-in, accounts,
conversations, grants and settings are untouched by the move.

## What the adapter does

| Need | How |
|---|---|
| Model list | `GET /v1/models`: every preset model with `status.value` loaded / unloaded / loading. Server paths in `status.args` are never copied. |
| Capabilities | Router first (`architecture.input_modalities` gives vision, `meta` gives the per-slot window once loaded), then the operator's two files in the spark-llm directory (below). Nothing is guessed: an absent value stays absent. |
| Streaming | `reasoning_content` goes to the thinking panel, `content` to the answer; usage and timings are passed on. |
| Tools | Native `tools` / `tool_choice` are passed **only** to a model whose `tool_roundtrip` is `true` in `models.verified.json`; any other model gets a clear refusal (and is not loaded for it). The adapter yields `{"type": "tool_calls", "calls": [...]}` before `done`. Semechka's own agents use a text protocol and work on every model. The "tools" capability badge means "verified". |
| Vision | The chat engine sends `image_url` parts to models with the `vision` capability; the adapter forwards them unchanged. |
| Thinking switch | `params.thinking` (model gen limits / request) becomes `chat_template_kwargs.enable_thinking` (Qwen templates) or `reasoning_effort: "low"` for gpt-oss when off. |
| Cold start | A model that is not loaded takes 25-90 s. The stream first yields `status` events (about every 2 s); the chat route turns them into SSE `{"type": "engine", "state": "loading", "elapsed": s}` and the interface shows "Loading the model (N s)" instead of going blank. Two replies for one cold model start one load. Timeout: `load_timeout_s` (300). |
| Memory admission | Before **loading** a model the adapter reads `MemAvailable` (this process runs on the Spark) and compares it with the model's measured `loaded_memory_mib` (else `est_mem_mb` from models.json) plus a headroom (8 GiB). Too little: the request fails with a plain message naming the need, the free memory and the models loaded now; nothing is loaded and **nothing is evicted by Semechka**. The router's own LRU eviction (`--models-max 3`) is not counted as free memory. |
| Embeddings | The router's `qwen3-embedding-0.6b` (1024 dimensions, same as the old `semechka-embed`). `embeddings.url` = router address **without** `/v1`, `embeddings.model` = the model id. The router key is taken from the instance's `llamacpp` backend row (same address), never from a setting. The token-aware overlong-input recovery (`embed_svc`, from `fix/embed-overlong-inputs`) is kept: `/props?model=` and `/tokenize` are called with the model name the router requires. |
| Lifecycle | Admin load/unload per model through `/models/load`, `/models/unload` (`multi_model` manifest flag). Load is refused for lack of memory, unload while this server is writing a reply from the model. Models of other programs cannot be seen: unloading cuts their replies. |

## Configuration

Backend row (`backends`): adapter `llamacpp`, base URL `http://127.0.0.1:8080/v1` (with or without `/v1`), API key (stored encrypted
with the instance's own key, purpose `backend`). Optional `options` on the row:

| Key | Default | Meaning |
|---|---|---|
| `spark_llm_dir` | `$SEMECHKA_SPARK_LLM_DIR` or `~/spark-llm` | directory with `models.json` and `models.verified.json` |
| `models_json`, `verified_json` | in that directory | explicit paths |
| `mem_headroom_mib` | 8192 | memory that must stay free after a load |
| `meminfo_path` | `$SEMECHKA_MEMINFO_PATH` or `/proc/meminfo` | where `MemAvailable` is read (a dev instance elsewhere can mirror the Spark's file) |
| `admission` | true | `false` switches the memory check off |
| `load_timeout_s` | 300 | longest wait for a load |
| `embedding_models` | ids containing "embed" | ids that are embedding models, not chat models |

Queue: set `max_concurrency` on the backend row (or `queue.backend_concurrency`) to what the router's slots allow; the llamacpp
adapter is not `exclusive_chat_model`, so replies for different models run at the same time.

### How capabilities are filled

* `models.json` (operator): `vision`, `reasoning`, `ctx` / `parallel` (the window one request gets is `ctx / parallel`, stored as
  `meta.slot_ctx` / `meta.context`, and used by the context-fitting code as the window), `context_max` (trained window), `est_mem_mb`, `legacy_id`.
* `models.verified.json` (measured): `loaded_memory_mib`, `tool_roundtrip`, `context_retention`, `coding_agent_compatible`, `note`; all of
  these appear in the model's `meta` (the notes show in the admin view).
* Models in neither file are listed with what the router reports (vision from the input modalities, no tool claim, no memory figure, so no
  admission check for them).
* Run **Sync** on the backend (Admin > Models) after the files or the preset change. New models arrive disabled with no grants.

## Migrating an instance

`scripts/spark/migrate_to_llamacpp.py <instance-data-dir> [--apply] [--rollback FILE]` (run it with the instance's own Python so
`semechka` imports):

```
# 1. stop the instance's service (SQLite tolerates a running one, a running process keeps stale model lists)
# 2. dry run: prints the plan, writes nothing
~/semechka-deploy/<name>/app/venv/bin/python -I migrate_to_llamacpp.py ~/semechka-deploy/<name>/data \
    --router-url http://127.0.0.1:8080/v1 --api-key-file ~/spark-llm/api.key --spark-llm-dir ~/spark-llm
# 3. apply: backup, new backend, model remap, embedding settings, rollback file
... same command with --apply
# 4. start the service, open Admin > Models > Sync once, check a conversation
```

What it does: online backup `semechka.db.pre-llamacpp-<time>` (mode 0600); adds the `llamacpp` backend (key read inside the script,
encrypted with the instance's key, never printed or stored elsewhere); for each `local:<engine id>` model finds the router id from
`legacy_id` (`local:qwen3.8-27b@UD-Q6_K` becomes `qwen3.8-27b`), keeps the model ROW (id, alias, enabled, pinned, sort, hidden, tags,
system prompt, gen limits, defaults, profile, grants, group membership) and only refreshes `backend_id`, `real_id`, `capabilities`,
`meta`, `available`; sets `embeddings.url` and `embeddings.model`; writes `migrate-llamacpp-<time>.rollback.json` with every original value.
Image/video/`endpoint:` models and any model the router has no equivalent for stay exactly where they are (the script lists them).
A second `--apply` changes nothing. Capabilities change only by gaining `tools` where verified.

Rollback: `migrate_to_llamacpp.py <data-dir> --rollback <file>`: restores every changed model field and setting row, removes the router
backend row when no model uses it (keeps it, and says so, when a later Sync added models to it). Restart the service afterwards. Full
rollback of everything is also possible by restoring the pre-migration backup file.

`scripts/spark/prove_llamacpp_migration.py <dir with semechka.db + master.key> --work <scratch> --spark-llm-dir ... --api-key-file ...`
rehearses the whole cycle on a copy and checks: counts of users/conversations/messages/media_assets/media_jobs/model_grants/usage unchanged,
`password_hash` and `sessions` byte-identical, every table other than backends/models/setting_values/audit identical, dry run
byte-identical, idempotence, and rollback restoring `models`, `backends`, `setting_values` exactly.

## Known limits

* The memory check uses the instant value of `MemAvailable`; two processes that start loads together can still race. The router's LRU
  eviction can unload a model another user is about to use (it spares models with a stream in flight).
* Unloading from Semechka cannot see replies other programs are streaming from the router.
* `models.json` is the source of the per-slot window; if the preset changes without Sync the old value is used until the router answers
  with a context error (the engine's message is then learned automatically, as before).
* A model that stops responding while loading is reported after 8 s of `unloaded` state following the load request.
* Knowledge collections (Knowledge page) use keyword search; semantic search with embeddings is for workspace files and agents
  (existing behaviour).
* `endpoint:` models (Trinodal) and image/video still use the Sarness Engine until their own workstreams move them; the Sarness backend
  row must stay for those.
* Native tool calls are available to adapter callers; the chat route itself does not offer tools to models.
