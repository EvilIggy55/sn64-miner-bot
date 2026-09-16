"""Cluster GPU inventory, read from the Kubernetes API."""
from dataclasses import dataclass

GPU = "nvidia.com/gpu"
MANAGED_BY = ("app.kubernetes.io/managed-by", "miner-bot")
CHUTE_LABEL = "miner-bot/model"


@dataclass(frozen=True)
class NodeGPU:
    name: str
    gpus: int  # allocatable nvidia.com/gpu
    used_by_others: int  # requested by pods the bot doesn't manage
    gpu_memory_gb: float
    memory_source: str  # gfd | label | config | default
    product: str | None = None

    @property
    def available(self) -> int:
        """GPUs the bot may plan onto. Its own chutes' GPUs count as available: the plan is rebuilt each time."""
        return max(0, self.gpus - self.used_by_others)


def is_managed(obj) -> bool:
    return (obj.metadata.labels or {}).get(MANAGED_BY[0]) == MANAGED_BY[1]


def _gpu_request(pod) -> int:
    total = 0
    for c in pod.spec.containers or []:
        res = c.resources
        if res is not None:
            # Extended resources can't be overcommitted, so limits and requests match when both are set.
            amount = (res.limits or {}).get(GPU) or (res.requests or {}).get(GPU)
            total += int(amount or 0)
    return total


def _memory(node, scheduler_cfg) -> tuple[float, str]:
    labels = node.metadata.labels or {}
    for label, divisor, source in (("nvidia.com/gpu.memory", 1024, "gfd"),  # MiB, GPU Feature Discovery
                                   ("miner-bot/gpu-memory-gb", 1, "label")):  # set by install_gpu_node.sh
        try:
            if labels.get(label):
                return float(labels[label]) / divisor, source
        except ValueError:
            pass
    if node.metadata.name in scheduler_cfg.node_gpu_memory_gb:
        return float(scheduler_cfg.node_gpu_memory_gb[node.metadata.name]), "config"
    return float(scheduler_cfg.default_gpu_memory_gb), "default"


def _schedulable(node) -> bool:
    if node.spec is not None and node.spec.unschedulable:
        return False
    ready = next((c for c in (node.status.conditions or []) if c.type == "Ready"), None)
    return ready is not None and ready.status == "True"


def inventory(core, scheduler_cfg) -> tuple[list[NodeGPU], dict[str, set[str]]]:
    """Schedulable GPU nodes, and the nodes each managed chute's pods are running on."""
    pods = core.list_pod_for_all_namespaces(field_selector="status.phase!=Succeeded,status.phase!=Failed").items
    used: dict[str, int] = {}
    placements: dict[str, set[str]] = {}
    for pod in pods:
        node = pod.spec.node_name if pod.spec else None
        if not node:
            continue
        if is_managed(pod):
            model = (pod.metadata.labels or {}).get(CHUTE_LABEL)
            if model:
                placements.setdefault(model, set()).add(node)
        else:
            used[node] = used.get(node, 0) + _gpu_request(pod)

    nodes = []
    for node in core.list_node().items:
        gpus = int((node.status.allocatable or {}).get(GPU, 0) or 0)
        if gpus == 0 or not _schedulable(node):
            continue
        memory, source = _memory(node, scheduler_cfg)
        labels = node.metadata.labels or {}
        nodes.append(NodeGPU(
            name=node.metadata.name,
            gpus=gpus,
            used_by_others=used.get(node.metadata.name, 0),
            gpu_memory_gb=memory,
            memory_source=source,
            product=labels.get("nvidia.com/gpu.product") or labels.get("miner-bot/gpu-product"),
        ))
    return sorted(nodes, key=lambda n: n.name), placements
