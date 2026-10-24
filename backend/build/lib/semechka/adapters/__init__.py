from .base import Adapter, AdapterError, DiscoveredModel, Manifest, Unsupported
from .ollama import OllamaAdapter
from .openai_compat import OpenAICompatAdapter
from .sarness import SarnessAdapter

REGISTRY: dict[str, type[Adapter]] = {
    a.manifest.id: a for a in (SarnessAdapter, OllamaAdapter, OpenAICompatAdapter)
}


def make(adapter_id: str, base_url: str, api_key: str | None = None, options: dict | None = None) -> Adapter:
    try:
        return REGISTRY[adapter_id](base_url, api_key, options)
    except KeyError:
        raise AdapterError(f"Unknown adapter '{adapter_id}'.", status=400) from None


__all__ = ["Adapter", "AdapterError", "DiscoveredModel", "Manifest", "Unsupported", "REGISTRY", "make"]
