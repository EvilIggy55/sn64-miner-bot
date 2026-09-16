"""Decide which models run, how many replicas, and on which nodes. Pure: no API calls."""
from dataclasses import dataclass, field

from .gpu_manager import NodeGPU
from .models_registry import ModelSpec


@dataclass(frozen=True)
class Placement:
    model: ModelSpec
    nodes: tuple[str, ...]  # one entry per replica; a node repeats when it hosts several

    @property
    def replicas(self) -> int:
        return len(self.nodes)


@dataclass
class Plan:
    placements: dict[str, Placement] = field(default_factory=dict)  # model name -> placement (>= 1 replica)
    unplaced: dict[str, str] = field(default_factory=dict)  # model name -> why replicas are missing
    free: dict[str, int] = field(default_factory=dict)  # GPUs left per node after the plan


def make_plan(models, nodes: list[NodeGPU], running=None, reserve_gpus: int = 0, skip=()) -> Plan:
    """Greedy placement: highest priority first, each replica on one node with enough free GPUs
    of enough VRAM. Replicas stay on nodes they already run on, otherwise go to the tightest fit."""
    running = running or {}
    free = {n.name: n.available for n in nodes}
    memory = {n.name: n.gpu_memory_gb for n in nodes}
    budget = sum(free.values()) - reserve_gpus
    plan = Plan()

    # Ties broken by name so the plan is stable between reconciles.
    for spec in sorted(models, key=lambda m: (-m.priority, m.name)):
        if spec.name in skip:
            plan.unplaced[spec.name] = "failed: autoheal gave up (run `miner-bot reset` to retry)"
            continue
        if spec.replicas == 0:
            continue
        chosen = []
        for _ in range(spec.replicas):
            fits = [n for n in free if free[n] >= spec.gpus and memory[n] >= spec.min_gpu_memory_gb]
            if budget < spec.gpus or not fits:
                break
            node = min(fits, key=lambda n: (n not in running.get(spec.name, ()), free[n], n))
            free[node] -= spec.gpus
            budget -= spec.gpus
            chosen.append(node)
        if chosen:
            plan.placements[spec.name] = Placement(spec, tuple(sorted(chosen)))
        if len(chosen) < spec.replicas:
            plan.unplaced[spec.name] = _why(spec, nodes, len(chosen), budget < spec.gpus)
    plan.free = free
    return plan


def _why(spec: ModelSpec, nodes: list[NodeGPU], placed: int, over_budget: bool) -> str:
    need = f"{spec.gpus} GPU(s) with >= {spec.min_gpu_memory_gb:g} GB"
    if not any(n.gpus >= spec.gpus and n.gpu_memory_gb >= spec.min_gpu_memory_gb for n in nodes):
        return f"no node has {need}"
    reason = "reserve_gpus leaves too few GPUs" if over_budget else f"not enough free nodes with {need}"
    return f"placed {placed} of {spec.replicas} replicas: {reason}"
