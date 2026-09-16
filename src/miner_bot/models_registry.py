"""The models the scheduler may deploy (config/models.yaml).

A "chute" here is one model-server Deployment (vLLM's OpenAI-compatible server by default) that
the bot creates and owns. These are not Chutes-network chutes.
"""
import re
from dataclasses import dataclass

import yaml

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
MAX_NAME = 40  # "chute-" + name must fit a Service name (63 chars)


class ModelsError(ValueError):
    pass


@dataclass(frozen=True)
class ModelSpec:
    name: str
    model: str
    image: str
    gpus: int = 1
    min_gpu_memory_gb: float = 0
    priority: int = 0
    replicas: int = 1
    port: int = 8000
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()


def parse_models(data: dict) -> list[ModelSpec]:
    defaults = data.get("defaults") or {}
    models, seen = [], set()
    for i, raw in enumerate(data.get("models") or []):
        entry = {**defaults, **raw}
        name = str(entry.get("name", ""))
        where = f"models[{i}] ({name or 'unnamed'})"
        if not NAME_RE.match(name) or len(name) > MAX_NAME:
            raise ModelsError(f"{where}: name must be lowercase letters, digits and '-', at most {MAX_NAME} chars")
        if name in seen:
            raise ModelsError(f"{where}: duplicate name")
        seen.add(name)
        for key in ("model", "image"):
            if not entry.get(key):
                raise ModelsError(f"{where}: '{key}' is required (per model or under defaults)")
        try:
            spec = ModelSpec(
                name=name,
                model=str(entry["model"]),
                image=str(entry["image"]),
                gpus=int(entry.get("gpus", 1)),
                min_gpu_memory_gb=float(entry.get("min_gpu_memory_gb", 0)),
                priority=int(entry.get("priority", 0)),
                replicas=int(entry.get("replicas", 1)),
                port=int(entry.get("port", 8000)),
                args=tuple(str(a) for a in entry.get("args") or ()),
                env=tuple(sorted((str(k), str(v)) for k, v in (entry.get("env") or {}).items())),
            )
        except (TypeError, ValueError) as exc:
            raise ModelsError(f"{where}: {exc}") from None
        if spec.gpus < 1 or spec.replicas < 0:
            raise ModelsError(f"{where}: gpus must be >= 1 and replicas >= 0")
        models.append(spec)
    return models


def load_models(path) -> list[ModelSpec]:
    with open(path, encoding="utf-8") as f:
        return parse_models(yaml.safe_load(f) or {})
