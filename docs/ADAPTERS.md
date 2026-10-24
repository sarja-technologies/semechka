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
| `sarness` | `/v1/agent/models` | yes via `/v1/agent/completions` | yes | `/v1/system` | **Sarness Engine 0.4.0 (real)** |

Image, video, audio, embeddings and tool-call flows exist in the capability model but **no adapter implements them in this release**; those modes report "Not available in this release."
