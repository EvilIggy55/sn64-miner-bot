"""Check the registering coldkey can cover a registration, and plan a top-up if it can't.

"Enough" is the subnet's current burn plus a margin for fees. By default this only prints the
shortfall and the btcli command that would cover it. With --execute it runs that transfer,
after you type TRANSFER; btcli then shows the amount and asks again.

Reads WALLET_COLDKEY_SS58 (the coldkey that pays for registration, public address) and
TOPUP_SOURCE_WALLET (the btcli wallet name the TAO comes from). Nothing here reads a key.

    python -m miner_bot.auto_topup [--subnet sn51] [--margin 0.05] [--execute]
"""
import argparse
import os
import shlex
import sys

from . import ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="auto_topup", description=__doc__.splitlines()[0])
    ops.add_subnet_arg(parser)
    parser.add_argument("--margin", type=float, default=0.05, help="TAO kept above the burn for fees (default 0.05)")
    parser.add_argument("--execute", action="store_true", help="run the transfer (asks first) instead of printing it")
    args = parser.parse_args(argv)
    if args.margin < 0:
        ops.die("--margin can't be negative")
    cfg = ops.settings()
    uid = ops.netuid(args.subnet, cfg)
    coldkey = os.getenv("WALLET_COLDKEY_SS58")
    if not coldkey:
        ops.die("set WALLET_COLDKEY_SS58 to the public address of the coldkey that pays for registration")

    state = ops.chain_read(cfg.network, lambda s: {"balance": s.balances.get(coldkey).tao,
                                                   "burn": s.subnets.burn(uid).tao})
    if not state["available"]:
        ops.die(f"cannot read balances on {cfg.network}: {state['error']}")
    target = state["burn"] + args.margin
    shortfall = round(target - state["balance"], 4)
    print(f"coldkey {coldkey}: {state['balance']:.4f} TAO free; netuid {uid} registration burn "
          f"{state['burn']:.4f} + {args.margin:g} margin = {target:.4f} TAO")
    if shortfall <= 0:
        print("No top-up needed.")
        return 0

    source = os.getenv("TOPUP_SOURCE_WALLET")
    if not source:
        ops.die(f"short {shortfall:.4f} TAO. Set TOPUP_SOURCE_WALLET to the btcli wallet name to send from.")
    command = ["wallet", "transfer", "--destination", coldkey, "--amount", f"{shortfall:.4f}",
               "--wallet.name", source, "--network", cfg.network]
    if not args.execute:
        print(f"Short {shortfall:.4f} TAO. To cover it, run (or rerun this with --execute):")
        print("  btcli " + shlex.join(command))
        return 0
    ops.btcli(command, "TRANSFER",
              f"About to send {shortfall:.4f} TAO from wallet '{source}' to {coldkey} on {cfg.network}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
