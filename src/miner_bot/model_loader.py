"""The models in config/models.yaml, and which of them fit this machine's GPUs.

    python -m miner_bot.model_loader --list

Without nvidia-smi (no GPU here) the fit columns are null. What's actually running in the
cluster is `warm_pool_status`.
"""
import argparse
import sys

from . import ops
from .local_serve import choose, local_gpus


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="model_loader", description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list models as JSON (the only mode)")
    parser.parse_args(argv)
    cfg = ops.settings()
    try:
        memory = local_gpus()
    except RuntimeError as exc:
        ops.note(f"note: {exc}; skipping the fit check")
        memory = []
    selected, rejected = choose(cfg.models, len(memory), min(memory)) if memory else (None, {})
    return ops.emit({
        "local_gpus": len(memory),
        "gb_per_gpu": min(memory) if memory else None,
        "selected": selected.name if selected else None,
        "models": [{
            "name": m.name, "model": m.model, "gpus": m.gpus, "min_gpu_memory_gb": m.min_gpu_memory_gb,
            "priority": m.priority, "replicas": m.replicas,
            "fits_here": not rejected.get(m.name, "").startswith(("needs", "replicas")) if memory else None,
            "note": rejected.get(m.name),
        } for m in sorted(cfg.models, key=lambda m: (-m.priority, m.name))],
    })


if __name__ == "__main__":
    sys.exit(main())
