"""Fleet status from the bot API: last reconcile, GPU nodes, and where each model is placed.

Needs the bot's port-forward (see README).

    python -m miner_bot.fleet_monitor
"""
import sys

from . import ops


def main(argv=None) -> int:
    return ops.emit({"status": ops.bot_api("/status"), "plan": ops.bot_api("/plan")})


if __name__ == "__main__":
    sys.exit(main())
