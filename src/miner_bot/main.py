"""The bot: a reconcile loop (inventory -> plan -> apply -> autoheal) and a small HTTP API.

Run in the cluster with `uvicorn miner_bot.main:app --host 0.0.0.0 --port 8000`.
"""
import asyncio
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from . import autoheal
from .bittensor_client import ChainStats
from .chute_scheduler import make_plan
from .config import Config, load
from .gpu_manager import inventory
from .k3s_controller import Controller
from .telemetry import EventLog, Metrics

log = logging.getLogger("miner_bot")


class LeaderLock:
    """Redis lock so only one bot replica reconciles at a time.

    Without Redis (unset or unreachable) this replica acts as leader: the Deployment runs one
    replica and every reconcile step is idempotent, so that's safe, just not guarded.
    """

    KEY = "miner-bot:leader"

    def __init__(self, url: str, ttl_seconds: float):
        self.url = url
        self.ttl_ms = int(ttl_seconds * 1000)
        self.identity = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._redis = None

    def acquire(self) -> bool:
        if not self.url:
            return True
        try:
            if self._redis is None:
                import redis

                self._redis = redis.Redis.from_url(self.url, socket_timeout=5, decode_responses=True)
            if self._redis.set(self.KEY, self.identity, nx=True, px=self.ttl_ms):
                return True
            if self._redis.get(self.KEY) == self.identity:
                self._redis.pexpire(self.KEY, self.ttl_ms)
                return True
            return False
        except Exception as exc:
            log.warning("leader lock: Redis unavailable, acting as leader (%s)", exc)
            self._redis = None
            return True


class Bot:
    def __init__(self, cfg: Config, apps, core, events=None, lock=None, chain=None, metrics=None):
        self.cfg = cfg
        self.core = core
        self.controller = Controller(apps, core, cfg.namespace, cfg.hf_cache_path, cfg.runtime_class)
        self.events = events or EventLog(cfg.database_url)
        self.lock = lock or LeaderLock(cfg.redis_url, max(cfg.reconcile_interval_seconds, 10) * 3)
        self.chain = chain or ChainStats(cfg.network, cfg.netuid, cfg.hotkey)
        self.metrics = metrics or Metrics()
        self.last: dict = {"at": None, "leader": False, "changes": [], "heal": [], "error": None}
        self._reconciling = threading.Lock()

    def plan(self):
        nodes, running = inventory(self.core, self.cfg.scheduler)
        failed = {m for m, d in self.controller.list_chutes().items() if (d.metadata.annotations or {}).get(autoheal.FAILED)}
        return nodes, make_plan(self.cfg.models, nodes, running, self.cfg.scheduler.reserve_gpus, skip=failed)

    def reconcile_once(self) -> dict:
        with self._reconciling:
            started = time.monotonic()
            leader = self.lock.acquire()
            self.metrics.leader.set(1 if leader else 0)
            result = {"at": time.time(), "leader": leader, "changes": [], "heal": [], "error": None}
            if leader:
                try:
                    nodes, plan = self.plan()
                    result["changes"] = self.controller.apply(plan)
                    for c in result["changes"]:
                        where = f" on {', '.join(c['nodes'])}" if c.get("nodes") else ""
                        self.events.record("scheduler", f"{c['action']}{where}", chute=c["chute"])
                    result["heal"] = autoheal.run(self.controller, self.cfg.autoheal)
                    for a in result["heal"]:
                        self.events.record("autoheal", f"{a['action']}: {a['reason']}", chute=a["chute"])
                        self.metrics.autoheal_actions.labels(a["chute"], a["action"]).inc()
                    self._update_metrics(nodes, plan)
                except Exception as exc:
                    log.exception("reconcile failed")
                    result["error"] = f"{type(exc).__name__}: {exc}"
                    self.metrics.reconcile_errors.inc()
            self._update_chain_metrics()
            self.metrics.reconcile_seconds.set(time.monotonic() - started)
            self.metrics.last_reconcile.set(result["at"])
            self.last = result
            return result

    def _update_metrics(self, nodes, plan):
        m = self.metrics
        for gauge in (m.gpus_total, m.gpus_available, m.gpus_planned, m.chute_desired, m.chute_ready, m.chute_failed):
            gauge.clear()
        for n in nodes:
            m.gpus_total.labels(n.name).set(n.gpus)
            m.gpus_available.labels(n.name).set(n.available)
            m.gpus_planned.labels(n.name).set(n.available - plan.free.get(n.name, n.available))
        for model, dep in self.controller.list_chutes().items():
            m.chute_desired.labels(model).set(dep.spec.replicas or 0)
            m.chute_ready.labels(model).set((dep.status.ready_replicas or 0) if dep.status else 0)
            m.chute_failed.labels(model).set(1 if (dep.metadata.annotations or {}).get(autoheal.FAILED) else 0)
        m.models_unplaced.set(len(plan.unplaced))

    def _update_chain_metrics(self):
        stats = self.chain.get()
        self.metrics.sn64.clear()
        if stats.get("available"):
            self.metrics.sn64.labels("registered").set(1 if stats.get("registered") else 0)
            for key in ("stake", "incentive", "emission", "trust", "consensus"):
                if stats.get(key) is not None:
                    self.metrics.sn64.labels(key).set(stats[key])


def build_bot() -> Bot:
    from kubernetes import client, config as kube_config

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = load()
    try:
        kube_config.load_incluster_config()
    except kube_config.ConfigException:
        kube_config.load_kube_config()
    return Bot(cfg, client.AppsV1Api(), client.CoreV1Api())


async def _loop(bot: Bot):
    while True:
        try:
            await asyncio.to_thread(bot.reconcile_once)
        except Exception:
            log.exception("reconcile loop error")
        await asyncio.sleep(bot.cfg.reconcile_interval_seconds)


def create_app(bot_factory=build_bot) -> FastAPI:
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_app):
        bot = state["bot"] = bot_factory()
        task = asyncio.create_task(_loop(bot)) if bot.cfg.reconcile_interval_seconds > 0 else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="sn64-miner-bot", lifespan=lifespan)

    def bot() -> Bot:
        return state["bot"]

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/status")
    def status():
        b = bot()
        return {
            "namespace": b.cfg.namespace,
            "models": len(b.cfg.models),
            "events_backend": b.events.backend,
            "last_reconcile": b.last,
        }

    @app.get("/plan")
    def plan():
        nodes, p = bot().plan()
        return {
            "nodes": [{**asdict(n), "available": n.available} for n in nodes],
            "placements": {k: {"replicas": v.replicas, "gpus_per_replica": v.model.gpus, "nodes": list(v.nodes)}
                           for k, v in p.placements.items()},
            "unplaced": p.unplaced,
            "free": p.free,
        }

    @app.get("/chutes")
    def chutes():
        b = bot()
        return {"chutes": b.controller.summary(b.cfg.autoheal)}

    @app.get("/events")
    def events(limit: int = 50):
        return {"events": bot().events.recent(max(1, min(limit, 1000)))}

    @app.get("/sn64")
    def sn64():
        return bot().chain.get()

    @app.post("/reconcile")
    def reconcile():
        return bot().reconcile_once()

    @app.post("/chutes/{model}/reset")
    def reset(model: str):
        b = bot()
        if not b.controller.reset(model):
            raise HTTPException(404, f"no managed chute for model '{model}'")
        b.events.record("manual", "reset: failed mark and restart count cleared", chute=model)
        return {"ok": True, "model": model}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(bot().metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
