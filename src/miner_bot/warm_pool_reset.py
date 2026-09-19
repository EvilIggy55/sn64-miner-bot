"""Clear autoheal's failed mark so the scheduler brings those chutes back.

Autoheal scales a chute to 0 after repeated restarts and leaves it failed until reset. With no
arguments this resets every failed chute; --model resets one. Healthy chutes are left alone.

    python -m miner_bot.warm_pool_reset [--model NAME]
"""
import argparse
import sys
import urllib.parse

from . import ops


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="warm_pool_reset", description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="reset this chute only (default: every failed chute)")
    args = parser.parse_args(argv)
    targets = [args.model] if args.model else \
        [c["model"] for c in ops.bot_api("/chutes")["chutes"] if c["failed"]]
    if not targets:
        print("No failed chutes; nothing to reset.")
        return 0
    for model in targets:
        ops.bot_api(f"/chutes/{urllib.parse.quote(model)}/reset", "POST")
        print(f"reset {model}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
