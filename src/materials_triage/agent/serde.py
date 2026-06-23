"""Checkpoint serializer that allowlists our domain models for msgpack.

LangGraph's checkpointer msgpack-encodes the pydantic models that flow through
the graph's channels. By default it deserializes any type with a warning
("…will be blocked in a future version"); once that default flips to *block*,
resume would break. The fix is to hand the serializer an explicit allowlist of
our domain types.

The allowlist is **auto-discovered** — every ``pydantic.BaseModel`` subclass
defined under the ``materials_triage`` package — rather than hand-listed, for one
load-bearing reason: a finite allowlist is *strict* (a tagged type NOT on it is
**blocked**, not warned), so an incomplete hand-list would silently break runs as
soon as a new or nested model is serialized. Discovery keeps the allowlist in
lockstep with the models by construction.
"""

import importlib
import pkgutil
from functools import lru_cache

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

import materials_triage


@lru_cache(maxsize=1)
def domain_model_keys() -> tuple[tuple[str, str], ...]:
    """Return ``(module, qualname)`` keys for every pydantic model in the package.

    Walks the whole ``materials_triage`` package and collects ``BaseModel``
    subclasses by their *defining* module. Cached: the walk imports every
    submodule, so it runs once per process. Modules that fail to import are
    skipped (none should, but discovery must never crash orchestrator setup)."""
    keys: set[tuple[str, str]] = set()
    for info in pkgutil.walk_packages(materials_triage.__path__, materials_triage.__name__ + "."):
        try:
            module = importlib.import_module(info.name)
        except Exception:  # noqa: BLE001 - discovery must not break setup
            continue
        for obj in vars(module).values():
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                keys.add((obj.__module__, obj.__name__))
    return tuple(sorted(keys))


def domain_serde() -> JsonPlusSerializer:
    """A checkpoint serializer that explicitly allows our domain models for msgpack,
    so checkpoint state round-trips without the unregistered-type warning and stays
    deserializable when LangGraph makes the allowlist mandatory."""
    return JsonPlusSerializer(allowed_msgpack_modules=list(domain_model_keys()))
