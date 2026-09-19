"""Re-register the hotkey if it has been deregistered. Checks first; never loops.

Looks up SN64_HOTKEY on the metagraph. Registered: prints its UID and exits. Deregistered:
offers the same burn registration as `bittensor_client register`, which asks before spending.
It won't register when it can't read the chain: a blind registration could burn TAO for a
hotkey that is still registered.

SN64_HOTKEY must be the ss58 address of WALLET_HOTKEY. Nothing here can check that without keys.

    python -m miner_bot.auto_reregistration [--subnet sn51] [--check]
"""
import argparse
import sys

from . import ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="auto_reregistration", description=__doc__.splitlines()[0])
    ops.add_subnet_arg(parser)
    parser.add_argument("--check", action="store_true", help="only report; exit 1 if deregistered")
    args = parser.parse_args(argv)
    cfg = ops.settings()
    uid = ops.netuid(args.subnet, cfg)
    if not cfg.hotkey:
        ops.die("set SN64_HOTKEY (the ss58 address of WALLET_HOTKEY) so registration can be checked")

    state = ops.chain_read(cfg.network, lambda s: {"neuron": ops.metagraph(s, uid).by_hotkey(cfg.hotkey)})
    if not state["available"]:
        ops.die(f"cannot read netuid {uid} on {cfg.network}, so not registering blind: {state['error']}")
    if state["neuron"] is not None:
        print(f"{cfg.hotkey} is registered on netuid {uid} as UID {state['neuron'].uid}; nothing to do.")
        return 0
    print(f"{cfg.hotkey} is not registered on netuid {uid}.")
    if args.check:
        return 1
    ops.register_hotkey(cfg, uid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
