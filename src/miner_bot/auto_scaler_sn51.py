"""Reconcile now: place config/models.yaml onto the cluster's GPUs and apply it.

This is the bot's scheduler on demand, the same pass its loop runs every 30 seconds. It scales
chutes within the GPUs the cluster already has. It doesn't rent or release machines; add GPU
nodes with scripts/install_gpu_node.sh.

    python -m miner_bot.auto_scaler_sn51
"""
import sys

from . import ops


def main(argv=None) -> int:
    result = ops.bot_api("/reconcile", "POST")
    if not result["leader"]:
        print("Another bot replica holds the leader lock; it will reconcile instead.")
    for change in result["changes"]:
        print(f"{change['action']} {change['chute']}")
    for action in result["heal"]:
        print(f"autoheal {action['action']} {action['chute']}: {action['reason']}")
    if result["error"]:
        ops.die(f"reconcile failed: {result['error']}")
    if result["leader"] and not (result["changes"] or result["heal"]):
        print("Already matches the plan; nothing changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
