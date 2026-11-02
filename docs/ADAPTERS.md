# Writing an adapter

An adapter subclasses `semechka.adapters.base.Adapter`, declares a `Manifest` (what it truly supports) and implements:

| Method | Required | Contract |
|---|---|---|
| `health()` | yes | `{ok, version, detail}`; never raises. |
| `discover()` | yes | list of `DiscoveredModel(real_id, capabilities, meta, external, loaded)`; **meta carries only fields the server reported** (`context`, `params`, `quantization`, `format`, `family`, `size_bytes`, `license`...). |
| `chat_stream(model, messages, params)` | yes | async generator of `{"type":"delta","content","reasoning"}` then one `{"type":"done","finish_reason","usage","timings"}`. Raise `AdapterError` with a user-safe message. Closing the generator must cancel the upstream request. |
| `system_info()` | if `system_telemetry` | raw dict of hardware/runtime data. |
| `load()` / `unload()` | if declared | else the base class raises `Unsupported`, surfaced as HTTP 501 with the reason. |

Register it in `adapters/__init__.py::REGISTRY`. Add a fake-server test (`tests/fakebackend.py`) and, if you can run the real thing, a skipped-by-default test in `tests/test_real_backends.py`.

## Shipped adapters and what is verified
| Adapter | Models/metadata | Chat stream | Load/unload | System telemetry | Verified here |
|---|---|---|---|---|---|
| `openai_compat` (llama.cpp, vLLM, LM Studio) | `/v1/models`; llama.cpp `meta` (ctx, params, size, quant) | yes, usage, llama.cpp timings | no | no | **llama.cpp b11606 (real)**; vLLM not run |
| `ollama` | `/api/tags` + `/api/show` (family, params, quant, ctx, capabilities) | yes, counts, durations | yes (keep-alive) | no | **not run (no Ollama installed)** — fake-free code, unverified |
| `sarness` | `/v1/agent/models` | yes via `/v1/agent/completions` | unload only for chat models (no load ahead of time: the engine switches its one chat model even mid-reply); "Stop worker" for pictures and video | `/v1/system` | **Sarness Engine 0.4.0 (real)**; `local_endpoints` and the Trinodal run record (0.5.8) against test doubles only |

Image, video, audio, embeddings and tool-call flows exist in the capability model but **no adapter implements them in this release**; those modes report "Not available in this release."

## Remote endpoints of a Sarness Engine, and which are local (0.5.8)
A model the engine reaches through a remote LLM endpoint (`endpoint:<id>:<model>`) is **external** by default, so the
privacy setting `privacy.external_egress` applies. If an endpoint actually runs on this machine or network (Trinodal AI
does), list its engine endpoint id in the Sarness backend's option `local_endpoints` (`PUT /api/admin/backends/{id}` with
`"local_endpoints": ["trinodal"]`). Models of exactly those ids are then not external (unless the backend itself is marked
external), and nothing else changes: every other endpoint of the same engine stays external. The rule is evaluated when a
model is used (`services.is_external`), so adding or removing an id takes effect at once, without a new sync. The option is
accepted for the `sarness` adapter only and every change is audited.

For those local endpoints, and only those, the adapter also passes on the endpoint's `usage.trinodal` object from the engine's
final `done` event as `done["trinodal"]` (cleaned to the known fields, bounded in size and type). It is run metadata and
never message content. Semechka stores it in the message's stats and filters it by technical-visibility permission on every
read; see [TRINODAL.md](TRINODAL.md).
