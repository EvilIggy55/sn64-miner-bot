"""Create, update and remove the bot's chute Deployments and Services.

Everything the bot creates carries app.kubernetes.io/managed-by=miner-bot, and every list, update
and delete is limited to objects with that label, so a reconcile can't touch Postgres, Redis or
the bot itself.
"""
import hashlib
import json
from datetime import datetime, timezone

from kubernetes.client.exceptions import ApiException

from .autoheal import FAILED, HEAL_COUNT, HEAL_WINDOW, chute_problem
from .gpu_manager import CHUTE_LABEL, GPU, MANAGED_BY
from .models_registry import ModelSpec

MANAGED_SELECTOR = f"{MANAGED_BY[0]}={MANAGED_BY[1]}"
SPEC_HASH = "miner-bot/spec-hash"
SECRET = "miner-bot-secrets"


def deployment_name(model: str) -> str:
    return f"chute-{model}"


def _annotations(obj) -> dict:
    return obj.metadata.annotations or {}


def _ignore(status: int, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ApiException as exc:
        if exc.status != status:
            raise


class Controller:
    def __init__(self, apps, core, namespace: str, hf_cache_path: str, runtime_class: str = "nvidia",
                 stop_grace_seconds: int = 30):
        self.apps, self.core = apps, core
        self.namespace = namespace
        self.hf_cache_path = hf_cache_path
        self.runtime_class = runtime_class
        self.stop_grace_seconds = stop_grace_seconds

    # Reads ------------------------------------------------------------------------------------

    def list_chutes(self) -> dict:
        """Managed chute Deployments, keyed by model name."""
        items = self.apps.list_namespaced_deployment(self.namespace, label_selector=MANAGED_SELECTOR).items
        return {d.metadata.labels[CHUTE_LABEL]: d for d in items if CHUTE_LABEL in (d.metadata.labels or {})}

    def pods(self, model: str) -> list:
        selector = f"{MANAGED_SELECTOR},{CHUTE_LABEL}={model}"
        return self.core.list_namespaced_pod(self.namespace, label_selector=selector).items

    def summary(self, heal_cfg, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(timezone.utc)
        out = []
        for model, dep in sorted(self.list_chutes().items()):
            annotations = _annotations(dep)
            pods = self.pods(model)
            out.append({
                "model": model,
                "deployment": dep.metadata.name,
                "replicas": dep.spec.replicas or 0,
                "ready": (dep.status.ready_replicas or 0) if dep.status else 0,
                "nodes": sorted({p.spec.node_name for p in pods if p.spec and p.spec.node_name}),
                "failed": annotations.get(FAILED),
                "heal_count": int(annotations.get(HEAL_COUNT) or 0),
                "problem": chute_problem(pods, now, heal_cfg),
            })
        return out

    # Desired objects --------------------------------------------------------------------------

    def _labels(self, spec: ModelSpec) -> dict:
        return {MANAGED_BY[0]: MANAGED_BY[1], CHUTE_LABEL: spec.name}

    def deployment_body(self, spec: ModelSpec, nodes: tuple[str, ...]) -> dict:
        args = ["--model", spec.model, "--served-model-name", spec.name, "--port", str(spec.port)]
        if spec.gpus > 1:
            args += ["--tensor-parallel-size", str(spec.gpus)]
        args += list(spec.args)
        env = [{"name": k, "value": v} for k, v in spec.env]
        env.append({"name": "HF_TOKEN", "valueFrom": {"secretKeyRef": {"name": SECRET, "key": "HF_TOKEN", "optional": True}}})
        pod_spec = {
            "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
                {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": sorted(set(nodes))}]},
            ]}}},
            "containers": [{
                "name": "server",
                "image": spec.image,
                "args": args,
                "env": env,
                "ports": [{"name": "http", "containerPort": spec.port}],
                "resources": {"limits": {GPU: str(spec.gpus)}},
                "readinessProbe": {"httpGet": {"path": "/health", "port": spec.port}, "periodSeconds": 10},
                "volumeMounts": [
                    {"name": "hf-cache", "mountPath": "/root/.cache/huggingface"},
                    {"name": "shm", "mountPath": "/dev/shm"},
                ],
            }],
            "volumes": [
                {"name": "hf-cache", "hostPath": {"path": self.hf_cache_path, "type": "DirectoryOrCreate"}},
                {"name": "shm", "emptyDir": {"medium": "Memory", "sizeLimit": "16Gi"}},
            ],
        }
        if self.runtime_class:
            pod_spec["runtimeClassName"] = self.runtime_class
        body = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": deployment_name(spec.name), "labels": self._labels(spec), "annotations": {}},
            "spec": {
                "replicas": len(nodes),
                "selector": {"matchLabels": self._labels(spec)},
                # A rolling update starts the new pod first, and it can't get the old pod's GPU.
                "strategy": {"type": "Recreate"},
                "template": {"metadata": {"labels": self._labels(spec)}, "spec": pod_spec},
            },
        }
        digest = hashlib.sha256(json.dumps(body["spec"], sort_keys=True).encode()).hexdigest()[:16]
        body["metadata"]["annotations"][SPEC_HASH] = digest
        return body

    def service_body(self, spec: ModelSpec) -> dict:
        return {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": deployment_name(spec.name), "labels": self._labels(spec)},
            "spec": {"selector": self._labels(spec), "ports": [{"name": "http", "port": spec.port, "targetPort": spec.port}]},
        }

    # Writes -----------------------------------------------------------------------------------

    def apply(self, plan) -> list[dict]:
        """Make the cluster match the plan. Idempotent. Returns the changes made."""
        existing = self.list_chutes()
        configured = set(plan.placements) | set(plan.unplaced)
        changes = []
        for model, placement in sorted(plan.placements.items()):
            dep = existing.get(model)
            if dep is not None and _annotations(dep).get(FAILED):
                continue
            body = self.deployment_body(placement.model, placement.nodes)
            change = {"chute": model, "replicas": placement.replicas, "nodes": list(placement.nodes)}
            if dep is None:
                try:
                    self.apps.create_namespaced_deployment(self.namespace, body)
                    changes.append({**change, "action": "created"})
                except ApiException as exc:
                    if exc.status != 409:
                        raise
                    self.apps.patch_namespaced_deployment(deployment_name(model), self.namespace, body)
                    changes.append({**change, "action": "updated"})
            elif (_annotations(dep).get(SPEC_HASH) != body["metadata"]["annotations"][SPEC_HASH]
                  or dep.spec.replicas != placement.replicas):
                self.apps.patch_namespaced_deployment(deployment_name(model), self.namespace, body)
                changes.append({**change, "action": "updated"})
            _ignore(409, self.core.create_namespaced_service, self.namespace, self.service_body(placement.model))

        for model, dep in sorted(existing.items()):
            failed = _annotations(dep).get(FAILED)
            # Failed chutes stay (scaled to 0) until reset, unless their model left models.yaml.
            if model not in plan.placements and (not failed or model not in configured):
                self.remove(model)
                changes.append({"chute": model, "action": "removed"})
        return changes

    def remove(self, model: str):
        name = deployment_name(model)
        _ignore(404, self.apps.delete_namespaced_deployment, name, self.namespace)
        try:
            service = self.core.read_namespaced_service(name, self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            return
        if (service.metadata.labels or {}).get(MANAGED_BY[0]) == MANAGED_BY[1]:
            _ignore(404, self.core.delete_namespaced_service, name, self.namespace)

    def restart(self, model: str):
        """Delete the chute's pods; the Deployment recreates them."""
        for pod in self.pods(model):
            if not pod.metadata.deletion_timestamp:
                _ignore(404, self.core.delete_namespaced_pod, pod.metadata.name, self.namespace,
                        grace_period_seconds=self.stop_grace_seconds)

    def annotate(self, model: str, annotations: dict):
        self.apps.patch_namespaced_deployment(deployment_name(model), self.namespace, {"metadata": {"annotations": annotations}})

    def mark_failed(self, model: str, reason: str):
        self.apps.patch_namespaced_deployment(deployment_name(model), self.namespace, {
            "metadata": {"annotations": {FAILED: reason}},
            "spec": {"replicas": 0},
        })

    def reset(self, model: str) -> bool:
        """Clear a chute's failed mark and restart count; the next reconcile scales it back up."""
        if model not in self.list_chutes():
            return False
        self.annotate(model, {FAILED: None, HEAL_COUNT: None, HEAL_WINDOW: None})
        return True
