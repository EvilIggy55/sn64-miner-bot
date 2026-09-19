"""Your hotkey's earnings per day on the subnet, from its last epoch's emission.

Emission is paid in the subnet's alpha. `tao_per_day_at_spot` converts at the pool's spot price
and ignores unstaking slippage. Needs BT_HOTKEY (public ss58) and the `chain` extra.

    python -m miner_bot.tao_monitor [--subnet sn51]
"""
import argparse
import sys

from . import ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tao_monitor", description=__doc__.splitlines()[0])
    ops.add_subnet_arg(parser)
    args = parser.parse_args(argv)
    cfg = ops.settings()
    uid = ops.netuid(args.subnet, cfg)
    base = {"network": cfg.network, "netuid": uid, "hotkey": cfg.hotkey}
    if not cfg.hotkey:
        return ops.emit({**base, "available": False, "error": "BT_HOTKEY is not set"})

    def fetch(subtensor):
        mg = ops.metagraph(subtensor, uid)
        neuron = mg.by_hotkey(cfg.hotkey)
        result = {"subnet": mg.name, "block": mg.block, "registered": neuron is not None}
        if neuron is not None:
            result.update(ops.earnings(mg, neuron))
        return result

    return ops.emit({**base, **ops.chain_read(cfg.network, fetch)})


if __name__ == "__main__":
    sys.exit(main())
