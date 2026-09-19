"""GraVal score: not available, and deliberately not faked.

GraVal is Chutes' (SN64) GPU-validation challenge, run by Chutes validators against miners in
TDX-attested VMs. This stack isn't attested, so no validator ever challenges it and there is no
score to read. SN51 doesn't use GraVal at all. This prints why instead of inventing a number.

    python -m miner_bot.graval_monitor
"""
import sys

from . import ops

REASON = ("GraVal is Chutes' GPU validation for TDX-attested miners. This stack isn't attested "
          "(see README), so validators never challenge it and there is no score to report.")


def main(argv=None) -> int:
    return ops.emit({"available": False, "score": None, "error": REASON})


if __name__ == "__main__":
    sys.exit(main())
