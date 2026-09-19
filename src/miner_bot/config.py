"""Bot settings: config/miner.yaml and config/models.yaml, with environment overrides."""
import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .models_registry import ModelSpec, load_models

# Environment variable -> miner.yaml key. Secrets (database/redis URLs) only come from here.
ENV_OVERRIDES = {
    "SN64_HOTKEY": "hotkey",  # old name, still read; BT_HOTKEY below wins when both are set
    "BT_HOTKEY": "hotkey",
    "BT_NETWORK": "network",
    "MINER_NAMESPACE": "namespace",
    "DATABASE_URL": "database_url",
    "REDIS_URL": "redis_url",
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SchedulerConfig:
    reserve_gpus: int = 0
    default_gpu_memory_gb: float = 24
    node_gpu_memory_gb: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AutohealConfig:
    not_ready_grace_seconds: int = 600
    unschedulable_grace_seconds: int = 120
    max_restarts: int = 3
    restart_window_seconds: int = 3600


@dataclass(frozen=True)
class Config:
    netuid: int = 64
    network: str = "finney"
    hotkey: str = ""
    namespace: str = "sn64"
    reconcile_interval_seconds: int = 30
    hf_cache_path: str = "/var/lib/miner-bot/hf-cache"
    runtime_class: str = "nvidia"
    database_url: str = ""
    redis_url: str = ""
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    autoheal: AutohealConfig = field(default_factory=AutohealConfig)
    models: tuple[ModelSpec, ...] = ()


def _build(cls, values: dict, where: str):
    known = {f.name for f in fields(cls)}
    unknown = sorted(set(values) - known)
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s): {', '.join(unknown)}")
    return cls(**values)


def load(config_dir=None, env=None) -> Config:
    env = os.environ if env is None else env
    base = Path(config_dir or env.get("MINER_BOT_CONFIG_DIR", "config"))
    try:
        raw = yaml.safe_load((base / "miner.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read {base / 'miner.yaml'}: {exc}") from None
    scheduler = _build(SchedulerConfig, raw.pop("scheduler", None) or {}, "miner.yaml scheduler")
    autoheal = _build(AutohealConfig, raw.pop("autoheal", None) or {}, "miner.yaml autoheal")
    for var, key in ENV_OVERRIDES.items():
        if env.get(var):
            raw[key] = env[var]
    try:
        models = tuple(load_models(base / "models.yaml"))
    except OSError as exc:
        raise ConfigError(f"cannot read {base / 'models.yaml'}: {exc}") from None
    return _build(Config, {**raw, "scheduler": scheduler, "autoheal": autoheal, "models": models}, "miner.yaml")
