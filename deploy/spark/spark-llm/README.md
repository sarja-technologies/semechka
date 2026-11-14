# spark-llm — the Spark's single LLM inference API

`spark-llm.service` (user unit) = the upstream **llama.cpp** `llama-server` in **router mode**, built from source for the GB10 (`sm_121a`, CUDA 13)
and kept in `~/spark-llm/llama/llama-v0.7.0/` (own copy; sha256 `4ccfcc61…`, commit `620d222`).

* Endpoint: `http://<spark-lan-ip>:8080/v1` (OpenAI-compatible; also `127.0.0.1:8080`). Reachable only from the trusted networks (ufw).
* Models: `~/spark-llm/models.ini` (router preset; the copy here writes the service user's home as `/home/spark`, so set the real path) / `models.json` (catalog) / `models.verified.json` (MEASURED capabilities). The GGUF files stay where they
  are (`~/.local/share/sarness-engine/models/llm/...`, shared with nothing else now); there are no copies.
* `--models-max 3`: up to three models stay loaded; a fourth evicts the least-recently-used one that is not generating (measured).
* Keys (`~/spark-llm/keys.txt` = the three below; never print them): `api.key` (Semechka chat + Spark Control), `clients.key` (Goose and other
  external clients), `work.key` (Work Mode gateway). Revoke one audience by removing its line and restarting the (idle) router.
* Embeddings: model `qwen3-embedding-0.6b` (1024 dims) through `/v1/embeddings`.

API cheat sheet (Bearer key): `GET /v1/models` (status loaded/unloaded/loading) · `POST /v1/chat/completions` (stream, tools, vision `image_url`) ·
`POST /models/load {"model":id}` · `POST /models/unload {"model":id}` (the child process exits; weights are never touched) · `GET /slots?model=id` (is_processing).

## Measured (2026-11-10, `llmtest.py`): all pass streaming, context, tool round-trip unless noted
| model | memory when loaded | notes |
|---|---|---|
| qwen3-coder-next | 50.1 GB | **Goose coding task verified**, 54 tok/s |
| qwen3.6-35b-a3b | 25.3 GB | **Goose verified**, 66 tok/s, vision |
| qwen3.8-27b | 28.0 GB | **Goose verified**, vision |
| gpt-oss-120b | 64.6 GB | **Goose verified** |
| hermes-4-70b | 63.1 GB | tools ok, not agent-tested |
| huihui-qwen3.8-27b-abliterated / huihui-qwen3.5-27b-abliterated / qwen3.8-27b-uncensored-hauhaucs | 28.7 / 29.0 / 32.1 GB | tools ok, vision, not agent-tested |
| qwen3-4b-instruct-2507 | 8.1 GB | tools ok |
| qwen3.5-0.8b | 2.5 GB | fails the multi-turn context check: not for real use |
| dolphin3.0-r1-mistral-24b | 30.0 GB | **never emitted a tool call**: chat only |
| qwen3.8-flash-next (+ huihui abliterated) | 93.7 GB | works, but leaves ~22 GB: effectively exclusive |

A model is only called coding-agent compatible after a real Goose task passed (the four in bold).

## Operate
* Status: `systemctl --user status spark-llm` · logs `journalctl --user -u spark-llm`. Restart only when no reply is streaming (it unloads everything).
* Add/change a model: edit `models.ini` (section name = model id), restart the router, run `python3 ~/spark-llm/llmtest.py <id>` and add its result to `models.verified.json`.
* Tests: `~/spark-llm/llmtest.py MODEL` (stream, context, tool round trip, unload + memory), `conc_test.py`, `evict_live.py`, `vis.py`.
* GPU etiquette for scripted work: `~/spark-reset/gpu-llm <cmd>` (shared lock) / `~/spark-reset/gpu-media <cmd>` (exclusive, unloads router models first).
* Clients: `~/spark-llm/clients/` (Goose provider JSON, Linux/macOS wrapper, Windows PowerShell, README).
