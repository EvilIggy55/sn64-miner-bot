"""Chute readiness from the bot API: the "warm pool" is the set of chutes that are Ready.

There's no separate pool: every model the scheduler placed runs as a vLLM Deployment, and a
chute is warm once its replicas are Ready. Needs the bot's port-forward (see README).

    python -m miner_bot.warm_pool_status
"""
import sys

from . import ops


def main(argv=None) -> int:
    chutes = ops.bot_api("/chutes")["chutes"]
    return ops.emit({
        "warm": sum(1 for c in chutes if c["replicas"] and c["ready"] >= c["replicas"]),
        "total": len(chutes),
        "failed": [c["model"] for c in chutes if c["failed"]],
        "chutes": chutes,
    })


if __name__ == "__main__":
    sys.exit(main())
