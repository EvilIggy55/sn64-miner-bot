"""Prometheus metrics, and the event log (Postgres, falling back to memory when it's unreachable)."""
import logging
import threading
import time
from collections import deque

from prometheus_client import CollectorRegistry, Counter, Gauge

log = logging.getLogger("miner_bot")


class Metrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        r = self.registry = registry or CollectorRegistry()
        self.gpus_total = Gauge("miner_bot_gpus_total", "Allocatable GPUs per node", ["node"], registry=r)
        self.gpus_available = Gauge("miner_bot_gpus_available", "GPUs not claimed by other workloads, per node", ["node"], registry=r)
        self.gpus_planned = Gauge("miner_bot_gpus_planned", "GPUs the current plan gives to chutes, per node", ["node"], registry=r)
        self.chute_desired = Gauge("miner_bot_chute_replicas_desired", "Replicas the chute should run", ["chute"], registry=r)
        self.chute_ready = Gauge("miner_bot_chute_replicas_ready", "Replicas passing their readiness probe", ["chute"], registry=r)
        self.chute_failed = Gauge("miner_bot_chute_failed", "1 when autoheal gave up on the chute", ["chute"], registry=r)
        self.models_unplaced = Gauge("miner_bot_models_unplaced", "Models running fewer replicas than configured", registry=r)
        self.autoheal_actions = Counter("miner_bot_autoheal_actions", "Autoheal restarts and give-ups", ["chute", "action"], registry=r)
        self.reconcile_errors = Counter("miner_bot_reconcile_errors", "Reconcile passes that raised", registry=r)
        self.reconcile_seconds = Gauge("miner_bot_reconcile_duration_seconds", "Duration of the last reconcile", registry=r)
        self.last_reconcile = Gauge("miner_bot_last_reconcile_timestamp_seconds", "When the last reconcile finished", registry=r)
        self.leader = Gauge("miner_bot_leader", "1 while this replica holds the leader lock", registry=r)
        self.sn64 = Gauge("miner_bot_sn64", "Our hotkey's SN64 metagraph values (registered is 0/1)", ["field"], registry=r)


class EventLog:
    """Append-only record of what the scheduler and autoheal did."""

    SCHEMA = """CREATE TABLE IF NOT EXISTS miner_bot_events (
        id BIGSERIAL PRIMARY KEY,
        at TIMESTAMPTZ NOT NULL DEFAULT now(),
        kind TEXT NOT NULL,
        chute TEXT,
        message TEXT NOT NULL
    )"""
    RETRY_SECONDS = 60

    def __init__(self, dsn: str, memory_size: int = 500):
        self.dsn = dsn
        self._conn = None
        self._retry_at = 0.0
        self._memory = deque(maxlen=memory_size)
        self._lock = threading.Lock()

    @property
    def backend(self) -> str:
        return "postgres" if self._conn is not None else "memory"

    def _connection(self):
        if self._conn is not None or not self.dsn or time.time() < self._retry_at:
            return self._conn
        try:
            import psycopg

            self._conn = psycopg.connect(self.dsn, autocommit=True, connect_timeout=5)
            self._conn.execute(self.SCHEMA)
        except Exception as exc:
            self._drop(f"Postgres unavailable, keeping events in memory ({exc})")
        return self._conn

    def _drop(self, why: str):
        log.warning("event log: %s", why)
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self._retry_at = time.time() + self.RETRY_SECONDS

    def record(self, kind: str, message: str, chute: str | None = None):
        log.info("%s %s: %s", kind, chute or "-", message)
        with self._lock:
            self._memory.appendleft({"at": time.time(), "kind": kind, "chute": chute, "message": message})
            conn = self._connection()
            if conn is not None:
                try:
                    conn.execute("INSERT INTO miner_bot_events (kind, chute, message) VALUES (%s, %s, %s)",
                                 (kind, chute, message))
                except Exception as exc:
                    self._drop(f"write failed ({exc})")

    def recent(self, limit: int = 50) -> list[dict]:
        with self._lock:
            conn = self._connection()
            if conn is not None:
                try:
                    rows = conn.execute(
                        "SELECT extract(epoch FROM at), kind, chute, message FROM miner_bot_events ORDER BY id DESC LIMIT %s",
                        (limit,),
                    ).fetchall()
                    return [{"at": float(r[0]), "kind": r[1], "chute": r[2], "message": r[3]} for r in rows]
                except Exception as exc:
                    self._drop(f"read failed ({exc})")
            return list(self._memory)[:limit]
