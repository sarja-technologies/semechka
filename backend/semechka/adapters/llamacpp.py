"""The Spark's llama.cpp router (`spark-llm.service`): the single LLM API of the machine.

Audited against the router of llama.cpp b1-620d222 as run on the Spark (`llama-server --models-preset models.ini --models-max 3`):
  GET  /health                 no auth
  GET  /v1/models              every model of the preset: status.value loaded|unloaded|loading, architecture.input_modalities
                               (text, image, video), context_length (the TRAINED window), and, once loaded, `meta` (n_ctx = the window
                               ONE slot gets: ctx-size / parallel; ftype; n_params; size). status.args holds server file paths and
                               is never copied.
  POST /models/load  {model}   returns at once (the load runs in the background); 400 "model is already running" when it is loaded or
                               loading; 404 for an unknown model
  POST /models/unload {model}  returns {"success": true} at once; the state follows a moment later
  POST /v1/chat/completions    OpenAI chat; streams `reasoning_content` and `content`, native `tool_calls`, `image_url` parts on vision
                               models. A request for an unloaded model loads it first (and the router evicts its own least recently
                               used model beyond --models-max, sparing models with a stream in flight)
  POST /v1/embeddings          OpenAI embeddings (model qwen3-embedding-0.6b, 1024 dimensions)

What this adapter adds on top of the plain OpenAI-compatible one (openai_compat.py):
  * capabilities from the router AND from the operator's two files in the spark-llm directory: `models.json` (vision, reasoning,
    trained window, estimated memory, per-slot window = ctx / parallel, `legacy_id`) and `models.verified.json` (what was measured:
    loaded_memory_mib, tool_roundtrip, context_retention, coding_agent_compatible). Native tools are passed ONLY to a model whose tool
    round trip was verified. A model the files do not describe still works for chat; nothing about it is guessed.
  * cold start: a model that is not loaded takes 25-90 s to load. The stream first yields {"type": "status", "state": "loading", ...}
    events (about every two seconds) so the interface can say so, then the reply. Never a silent wait, never a timeout shorter than the load.
  * memory admission before a load: this process runs on the machine that holds the models, so /proc/meminfo MemAvailable is compared
    with the model's measured memory plus a headroom. Too little -> a refusal in plain words (kind "memory"); nothing is evicted by us.
    The router's own LRU eviction is the router's business and is not counted as free memory.

Options of the backend row (`backends.options`), all optional:
  spark_llm_dir     directory holding models.json / models.verified.json (default $SEMECHKA_SPARK_LLM_DIR or ~/spark-llm)
  models_json, verified_json   explicit paths for the two files
  mem_headroom_mib  memory that must stay free after a load (default 8192)
  meminfo_path      where MemAvailable is read (default $SEMECHKA_MEMINFO_PATH or /proc/meminfo)
  admission         false turns the memory check off (default true)
  load_timeout_s    longest wait for a load (default 300)
  embedding_models  ids that are embedding models and not chat models (default: ids containing "embed")
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest
from .openai_compat import _headers, _scrub, _short

_SAMPLING = ("temperature", "top_p", "max_tokens", "seed", "presence_penalty", "frequency_penalty", "stop")
_EXTRA = ("top_k", "min_p", "repeat_penalty")
MIB = 1024 * 1024
DEFAULT_HEADROOM_MIB = 8192
DEFAULT_LOAD_TIMEOUT = 300.0
POLL_SECONDS = 1.0
STATUS_EVERY = 2.0
DIED_AFTER = 8.0  # a model still `unloaded` this long after its load was requested has failed to start

_FILE_CACHE: dict[str, tuple[float, object]] = {}
_LOAD_LOCKS: dict[tuple[str, str], asyncio.Lock] = {}


def spark_llm_dir(options: dict | None = None) -> Path:
    o = options or {}
    return Path(o.get("spark_llm_dir") or os.environ.get("SEMECHKA_SPARK_LLM_DIR") or "~/spark-llm").expanduser()


def _read_json(path: Path):
    """The parsed file, cached by mtime; None when it is missing or unreadable (the adapter then describes models from the router alone)."""
    try:
        mt = path.stat().st_mtime
    except OSError:
        return None
    hit = _FILE_CACHE.get(str(path))
    if hit and hit[0] == mt:
        return hit[1]
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    _FILE_CACHE[str(path)] = (mt, data)
    return data


def models_table(options: dict | None = None) -> dict[str, dict]:
    """id -> models.json entry (file paths removed)."""
    o = options or {}
    data = _read_json(Path(o["models_json"]).expanduser() if o.get("models_json") else spark_llm_dir(o) / "models.json")
    out = {}
    for e in data if isinstance(data, list) else []:
        if isinstance(e, dict) and isinstance(e.get("id"), str):
            out[e["id"]] = {k: v for k, v in e.items() if k not in ("file", "mmproj")}
    return out


def verified_table(options: dict | None = None) -> dict[str, dict]:
    """id -> measured facts from models.verified.json."""
    o = options or {}
    data = _read_json(Path(o["verified_json"]).expanduser() if o.get("verified_json") else spark_llm_dir(o) / "models.verified.json")
    models = data.get("models") if isinstance(data, dict) else None
    return {k: v for k, v in (models or {}).items() if isinstance(v, dict)}


def legacy_map(options: dict | None = None) -> dict[str, str]:
    """Old Sarness engine id (`qwen3.8-27b@UD-Q6_K`) -> router id (`qwen3.8-27b`), from models.json `legacy_id`."""
    return {e["legacy_id"]: i for i, e in models_table(options).items() if isinstance(e.get("legacy_id"), str)}


def mem_available_mib(path: str | os.PathLike = "/proc/meminfo") -> int | None:
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def mem_total_mib(path: str | os.PathLike = "/proc/meminfo") -> int | None:
    try:
        with open(path) as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def is_embedding(model_id: str, options: dict | None = None) -> bool:
    listed = (options or {}).get("embedding_models")
    if isinstance(listed, list):
        return model_id in listed
    return "embed" in model_id.lower()


def need_mib(model_id: str, options: dict | None = None) -> tuple[int | None, str]:
    """(memory a load needs in MiB, where the figure comes from): measured first, then the operator's estimate."""
    v = verified_table(options).get(model_id) or {}
    if isinstance(v.get("loaded_memory_mib"), (int, float)) and v["loaded_memory_mib"] > 0:
        return int(v["loaded_memory_mib"]), "measured"
    e = models_table(options).get(model_id) or {}
    if isinstance(e.get("est_mem_mb"), (int, float)) and e["est_mem_mb"] > 0:
        return int(e["est_mem_mb"] * 0.954), "estimated"  # est_mem_mb is MB; the memory check works in MiB
    return None, ""


def gib(mib: float) -> str:
    return f"{mib / 1024:.1f} GiB"


def tools_verified(model_id: str, options: dict | None = None) -> bool:
    return (verified_table(options).get(model_id) or {}).get("tool_roundtrip") is True


def _state(m: dict) -> str:
    s = m.get("status")
    v = s.get("value") if isinstance(s, dict) else s
    return v if v in ("loaded", "unloaded", "loading") else "unloaded"


class LlamaCppAdapter(Adapter):
    manifest = Manifest(
        id="llamacpp", name="llama.cpp router (Spark)", model_load=True, model_unload=True, multi_model=True,
        reports_usage_tokens=True, reports_timings=True,
        model_metadata=["engine_name", "context", "context_trained", "slot_ctx", "vram_estimate_mb", "loaded_memory_mib", "quantization",
                        "params", "size_bytes", "reasoning", "tool_roundtrip", "coding_agent_compatible", "runtime", "loaded"],
        notes="The Spark's single LLM API. Several chat models can be loaded at once (the router keeps at most --models-max and "
              "evicts its own least recently used one). A model that is not loaded is loaded when someone first uses it, which takes "
              "25-90 seconds; Semechka says so while it waits and refuses, in words, when the machine lacks the memory.")

    # ---- plumbing -------------------------------------------------------------------------------------------------
    @property
    def root(self) -> str:
        """The router's address without /v1: /models/load and /health live there."""
        return self.base_url.removesuffix("/v1")

    @property
    def v1(self) -> str:
        return self.root + "/v1"

    @property
    def meminfo(self) -> str:
        return self.options.get("meminfo_path") or os.environ.get("SEMECHKA_MEMINFO_PATH") or "/proc/meminfo"

    async def _models_raw(self, timeout: float = 10) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.get(self.v1 + "/models", headers=_headers(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"The model service did not answer: {_short(e)}", kind="unreachable") from e
        if r.status_code in (401, 403):
            raise AdapterError("The model service rejected the API key.", kind="auth")
        if r.status_code >= 400:
            raise AdapterError(f"The model service answered {r.status_code} to the model list.")
        try:
            return [m for m in r.json().get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
        except ValueError as e:
            raise AdapterError("The model service returned an unreadable model list.") from e

    # ---- health / discovery ---------------------------------------------------------------------------------------
    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                h = await c.get(self.root + "/health")
                if h.status_code >= 400:
                    return {"ok": False, "version": None, "detail": f"The model service answered {h.status_code}."}
                r = await c.get(self.v1 + "/models", headers=_headers(self.api_key))
            if r.status_code in (401, 403):
                return {"ok": False, "version": None, "detail": "The model service rejected the API key."}
            r.raise_for_status()
            return {"ok": True, "version": "llama.cpp router", "detail": "reachable"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "version": None, "detail": _short(e)}

    def describe(self, m: dict, table: dict, verified: dict) -> DiscoveredModel:
        mid = m["id"]
        if is_embedding(mid, self.options):
            meta = {"engine_name": mid, "runtime": "llama.cpp", "embedding": True, "router": True}
            if isinstance(m.get("meta"), dict) and m["meta"].get("n_embd"):
                meta["dimensions"] = int(m["meta"]["n_embd"])
            return DiscoveredModel(mid, ["embeddings"], meta, loaded=_state(m) == "loaded")
        e, v = table.get(mid) or {}, verified.get(mid) or {}
        rm = m.get("meta") if isinstance(m.get("meta"), dict) else {}
        modalities = ((m.get("architecture") or {}).get("input_modalities")) or []
        vision = bool(e.get("vision")) if "vision" in e else "image" in modalities
        caps = ["chat"] + (["vision"] if vision else []) + (["tools"] if v.get("tool_roundtrip") is True else [])
        slot = rm.get("n_ctx")
        if not slot and e.get("ctx"):
            slot = int(e["ctx"]) // max(1, int(e.get("parallel") or 1))
        trained = e.get("context_max") or rm.get("n_ctx_train") or m.get("context_length")
        meta: dict = {
            "engine_name": mid, "runtime": "llama.cpp", "router": True,
            "context": int(slot) if slot else (int(trained) if trained else None),  # what one request can use
            "slot_ctx": int(slot) if slot else None,
            "context_trained": int(trained) if trained else None,
            "parallel": e.get("parallel"),
            "vram_estimate_mb": v.get("loaded_memory_mib") or e.get("est_mem_mb"),
            "loaded_memory_mib": v.get("loaded_memory_mib"),
            "memory_source": "measured" if v.get("loaded_memory_mib") else ("estimated" if e.get("est_mem_mb") else None),
            "reasoning": e.get("reasoning") if isinstance(e.get("reasoning"), bool) else None,
            "quantization": (str(e["legacy_id"]).split("@", 1)[1] if "@" in str(e.get("legacy_id") or "") else None) or rm.get("ftype"),
            "params": int(rm["n_params"]) if rm.get("n_params") else None,
            "size_bytes": int(rm["size"]) if rm.get("size") else None,
            "format": "GGUF",
            "tool_roundtrip": v.get("tool_roundtrip") if isinstance(v.get("tool_roundtrip"), bool) else None,
            "context_retention": v.get("context_retention") if isinstance(v.get("context_retention"), bool) else None,
            "coding_agent_compatible": v.get("coding_agent_compatible") if isinstance(v.get("coding_agent_compatible"), bool) else None,
            "verified_note": (v.get("note") or None),
            "engine_tags": e.get("tags") or None,
            "legacy_id": e.get("legacy_id"),
        }
        return DiscoveredModel(mid, caps, {k: x for k, x in meta.items() if x is not None},
                               external=bool(self.options.get("external")), loaded=_state(m) == "loaded")

    async def discover(self) -> list[DiscoveredModel]:
        rows = await self._models_raw(15)
        table, verified = models_table(self.options), verified_table(self.options)
        return [self.describe(m, table, verified) for m in rows]

    # ---- status / load / unload -------------------------------------------------------------------------------------
    async def status(self) -> dict | None:
        try:
            rows = await self._models_raw(8)
        except AdapterError:
            return None
        loaded = [m["id"] for m in rows if _state(m) == "loaded"]
        loading = [m["id"] for m in rows if _state(m) == "loading"]
        avail, total = mem_available_mib(self.meminfo), mem_total_mib(self.meminfo)
        return {
            "engine": {"version": "llama.cpp router"},
            "memory": {k: v for k, v in {"total_mb": total, "available_mb": avail,
                                         "used_mb": (total - avail) if total and avail is not None else None, "unified": True}.items()
                       if v is not None},
            "llm": {"state": "loading" if loading else ("ready" if loaded else "stopped"), "model_id": (loading or loaded or [None])[0],
                    "active_streams": 0, "ctx": None, "loaded": loaded, "loading": loading},
            "worker": {"alive": None, "busy": None, "loaded": []},
            "jobs": {},
            "health": "ok",
        }

    async def load(self, model: str) -> None:
        await self._post_model("/models/load", model, ok_400=("already running",))

    async def unload(self, model: str) -> None:
        await self._post_model("/models/unload", model)

    async def _post_model(self, path: str, model: str, ok_400: tuple[str, ...] = ()) -> None:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10)) as c:
                r = await c.post(self.root + path, json={"model": model}, headers=_headers(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"The model service did not answer: {_short(e)}", kind="unreachable") from e
        if r.status_code in (401, 403):
            raise AdapterError("The model service rejected the API key.", kind="auth")
        if r.status_code == 404:
            raise AdapterError(f"The model service does not know a model called {model}.", status=404, kind="not_found")
        if r.status_code >= 400:
            text = _scrub(r.text)
            if any(s in text for s in ok_400):
                return
            raise AdapterError(f"The model service refused: {text[:160]}")

    # ---- admission ---------------------------------------------------------------------------------------------------
    def admission(self, model: str, loaded: list[str]) -> str | None:
        """None when a load may go ahead, else the refusal in plain words. Reads MemAvailable now."""
        if self.options.get("admission") is False:
            return None
        need, src = need_mib(model, self.options)
        if need is None:
            return None  # nothing is known about this model's size, so nothing can be claimed
        avail = mem_available_mib(self.meminfo)
        if avail is None:
            return None
        head = int(self.options.get("mem_headroom_mib") or DEFAULT_HEADROOM_MIB)
        if avail >= need + head:
            return None
        others = ", ".join(loaded) if loaded else "none"
        return (f"Not enough free memory to start this model right now. It needs about {gib(need)} ({src}) plus {gib(head)} kept free, "
                f"and {gib(avail)} is free. Models loaded at the moment: {others}. Ask an administrator to unload one of them, or "
                "pick a smaller model. Nothing was stopped.")

    async def ensure_loaded(self, model: str):
        """Async generator of {"type": "status", ...} events while the model loads; returns when it is loaded.
        Raises AdapterError (kind "memory" / "load_failed" / "timeout") otherwise."""
        key = (self.root, model)
        t0 = time.monotonic()
        timeout = float(self.options.get("load_timeout_s") or DEFAULT_LOAD_TIMEOUT)
        rows = await self._models_raw()
        by = {m["id"]: m for m in rows}
        if model not in by:
            raise AdapterError(f"The model service does not list a model called {model}.", status=404, kind="not_found")
        if _state(by[model]) == "loaded":
            return
        lock = _LOAD_LOCKS.setdefault(key, asyncio.Lock())
        async with lock:  # two replies for the same cold model start one load
            rows = await self._models_raw()
            by = {m["id"]: m for m in rows}
            st = _state(by.get(model, {}))
            if st == "loaded":
                return
            if st == "unloaded":
                refusal = self.admission(model, [i for i, m in by.items() if _state(m) == "loaded"])
                if refusal:
                    raise AdapterError(refusal, status=503, kind="memory")
                yield {"type": "status", "state": "loading", "model": model, "elapsed": 0.0}
                await self.load(model)
            last = 0.0
            while True:
                await asyncio.sleep(POLL_SECONDS)
                elapsed = time.monotonic() - t0
                st = _state({m["id"]: m for m in await self._models_raw()}.get(model, {}))
                if st == "loaded":
                    return
                if st == "unloaded" and elapsed > DIED_AFTER:
                    raise AdapterError("The model stopped while it was loading. Try again in a moment; if it keeps happening an "
                                       "administrator should look at the model service.", kind="load_failed")
                if elapsed > timeout:
                    raise AdapterError(f"The model did not finish loading within {int(timeout)} seconds.", status=504, kind="timeout")
                if elapsed - last >= STATUS_EVERY:
                    last = elapsed
                    yield {"type": "status", "state": "loading", "model": model, "elapsed": round(elapsed, 1)}

    # ---- chat ----------------------------------------------------------------------------------------------------------
    async def chat_stream(self, model, messages, params):
        body = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        for k in _SAMPLING + _EXTRA:
            if params.get(k) is not None:
                body[k] = params[k]
        if isinstance(params.get("thinking"), bool):
            # Reasoning on/off. Qwen-family templates read `enable_thinking` (whatever models.json says: the 0.8B model thinks by
            # default although it is not listed as a reasoning model); gpt-oss has effort levels instead; other templates ignore it.
            if model.startswith("gpt-oss"):
                if not params["thinking"]:
                    body["chat_template_kwargs"] = {"reasoning_effort": "low"}
            else:
                body["chat_template_kwargs"] = {"enable_thinking": params["thinking"]}
        tools = params.get("tools")
        if tools:
            if not tools_verified(model, self.options):
                raise AdapterError("This model has not passed the tool-use check, so tools are not offered to it. Choose a model "
                                   "marked as tool capable.", status=422, kind="unsupported")
            body["tools"] = tools
            if params.get("tool_choice"):
                body["tool_choice"] = params["tool_choice"]
        async for ev in self.ensure_loaded(model):  # only now: a request that will be refused must not load a model
            yield ev
        usage = timings = finish = None
        calls: dict[int, dict] = {}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as c:
                async with c.stream("POST", self.v1 + "/chat/completions", json=body, headers=_headers(self.api_key)) as r:
                    if r.status_code >= 400:
                        raise AdapterError(_error_text(r.status_code, (await r.aread()).decode(errors="replace")))
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            ch = json.loads(payload)
                        except ValueError:
                            continue
                        if isinstance(ch.get("error"), dict):
                            raise AdapterError(_scrub(str(ch["error"].get("message") or "The model failed."))[:200])
                        for choice in ch.get("choices") or []:
                            d = choice.get("delta") or {}
                            c_, r_ = d.get("content") or "", d.get("reasoning_content") or d.get("reasoning") or ""
                            if c_ or r_:
                                yield {"type": "delta", "content": c_, "reasoning": r_}
                            for tc in d.get("tool_calls") or []:
                                slot = calls.setdefault(int(tc.get("index", len(calls))), {"id": None, "name": "", "arguments": ""})
                                fn = tc.get("function") or {}
                                slot["id"] = tc.get("id") or slot["id"]
                                slot["name"] += fn.get("name") or ""
                                slot["arguments"] += fn.get("arguments") or ""
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
                        if ch.get("usage"):
                            usage = {"prompt_tokens": ch["usage"].get("prompt_tokens"),
                                     "completion_tokens": ch["usage"].get("completion_tokens")}
                        if ch.get("timings"):
                            t = ch["timings"]
                            timings = {"prompt_per_second": t.get("prompt_per_second"),
                                       "predicted_per_second": t.get("predicted_per_second")}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"The model service stopped answering: {_short(e)}") from e
        if calls:
            yield {"type": "tool_calls", "calls": [calls[i] for i in sorted(calls)]}
        yield {"type": "done", "finish_reason": finish, "usage": usage, "timings": timings}


_MSG = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _error_text(status: int, text: str) -> str:
    """The router's own message (kept whole enough for the context-size learner to read), else the cleaned body."""
    msg = None
    try:
        e = json.loads(text).get("error")
        msg = e.get("message") if isinstance(e, dict) else None
    except ValueError:
        m = _MSG.search(text)
        msg = m.group(1) if m else None
    return f"Backend returned {status}: {_scrub(msg or text)[:240]}"
