"""Free TAO in your coldkey and your hotkey's stake on the subnet.

Reads public addresses only: WALLET_COLDKEY_SS58 for the balance, SN64_HOTKEY for the stake.
Either may be unset; its section then says so.

    python -m miner_bot.wallet_monitor [--subnet sn51]
"""
import argparse
import os
import sys

from . import ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="wallet_monitor", description=__doc__.splitlines()[0])
    ops.add_subnet_arg(parser)
    args = parser.parse_args(argv)
    cfg = ops.settings()
    uid = ops.netuid(args.subnet, cfg)
    coldkey = os.getenv("WALLET_COLDKEY_SS58", "")

    def fetch(subtensor):
        result = {}
        if coldkey:
            result["coldkey_free_tao"] = subtensor.balances.get(coldkey).tao
        if cfg.hotkey:
            neuron = ops.metagraph(subtensor, uid).by_hotkey(cfg.hotkey)
            result["hotkey_registered"] = neuron is not None
            if neuron is not None:
                # Both in the subnet's alpha, not TAO.
                result.update(uid=neuron.uid, alpha_stake=neuron.alpha_stake.amount,
                              total_stake_alpha=neuron.total_stake.amount)
        return result

    base = {"network": cfg.network, "netuid": uid, "coldkey": coldkey or None, "hotkey": cfg.hotkey or None}
    if not coldkey and not cfg.hotkey:
        return ops.emit({**base, "available": False,
                         "error": "set WALLET_COLDKEY_SS58 and/or SN64_HOTKEY (public ss58 addresses)"})
    return ops.emit({**base, **ops.chain_read(cfg.network, fetch)})


if __name__ == "__main__":
    sys.exit(main())
