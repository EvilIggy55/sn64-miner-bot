"""Restart crash-looping or stuck chutes, and give up on ones that keep failing.

A chute that fails more than max_restarts times within restart_window_seconds is scaled to 0
and marked failed; the scheduler then leaves it alone until `miner-bot reset <model>`.
Restart attempts are kept in Deployment annotations, so they survive a bot restart.
"""
from datetime import datetime, timezone

# Container waiting reasons that mean the pod won't come up on its own.
BAD_WAITING = {
    "CrashLoopBackOff",
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
}
FAILED = "miner-bot/failed"
HEAL_COUNT = "miner-bot/heal-count"
HEAL_WINDOW = "miner-bot/heal-window-start"


def pod_problem(pod, now: datetime, cfg) -> str | None:
    """Why this pod needs intervention, or None (healthy, or still starting within its grace period)."""
    if pod.metadata.deletion_timestamp:
        return None
    st = pod.status
    containers = st.container_statuses or []
    if st.phase == "Failed":
        return st.reason or "Failed"
    waiting = [c.state.waiting.reason for c in containers if c.state and c.state.waiting]
    stuck = next((w for w in waiting if w in BAD_WAITING), None)
    if stuck:
        last = [c.last_state.terminated for c in containers if c.last_state and c.last_state.terminated]
        if stuck == "CrashLoopBackOff" and any(t.reason == "OOMKilled" for t in last):
            return "OOMKilled"
        return stuck

    created = pod.metadata.creation_timestamp
    if any(c.type == "PodScheduled" and c.status == "False" for c in st.conditions or []):
        age = (now - created).total_seconds() if created else 0
        return "Unschedulable" if age > cfg.unschedulable_grace_seconds else None

    if st.phase == "Running" and containers and not all(c.ready for c in containers):
        running_since = [c.state.running.started_at for c in containers
                         if c.state and c.state.running and c.state.running.started_at]
        started = min(running_since) if running_since else created
        if started and (now - started).total_seconds() > cfg.not_ready_grace_seconds:
            return "NotReady"
    return None


def chute_problem(pods, now: datetime, cfg) -> str | None:
    terminating = any(p.metadata.deletion_timestamp for p in pods)
    for pod in pods:
        reason = pod_problem(pod, now, cfg)
        if reason == "Unschedulable" and terminating:
            continue  # the replacement is waiting for the old pod to release its GPU
        if reason:
            return reason
    return None


def heal_decision(annotations: dict, now: datetime, cfg) -> tuple[str, dict]:
    """("restart", annotations to write) or ("give-up", {})."""
    try:
        count = int(annotations.get(HEAL_COUNT) or 0)
        start = float(annotations.get(HEAL_WINDOW) or 0)
    except ValueError:
        count, start = 0, 0.0
    ts = now.timestamp()
    if ts - start > cfg.restart_window_seconds:
        count, start = 0, ts
    if count >= cfg.max_restarts:
        return "give-up", {}
    return "restart", {HEAL_COUNT: str(count + 1), HEAL_WINDOW: str(start)}


def run(controller, cfg, now: datetime | None = None) -> list[dict]:
    """Check every managed chute that should be running; restart or give up. Returns actions taken."""
    now = now or datetime.now(timezone.utc)
    actions = []
    for model, dep in sorted(controller.list_chutes().items()):
        annotations = dep.metadata.annotations or {}
        if annotations.get(FAILED) or not dep.spec.replicas:
            continue
        reason = chute_problem(controller.pods(model), now, cfg)
        if not reason:
            continue
        decision, patch = heal_decision(annotations, now, cfg)
        if decision == "restart":
            controller.annotate(model, patch)
            controller.restart(model)
            actions.append({"chute": model, "action": "restarted", "reason": reason, "attempt": int(patch[HEAL_COUNT])})
        else:
            controller.mark_failed(model, reason)
            actions.append({"chute": model, "action": "gave up", "reason": reason})
    return actions
