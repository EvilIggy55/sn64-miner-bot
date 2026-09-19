"""Our hotkey's standing on SN64, read from the Bittensor metagraph.

Uses the bittensor 11 client API (the optional `chain` extra) and only needs the hotkey's public
ss58 address. Registration itself is done once with scripts/register_hotkey.sh, or:

    python -m miner_bot.bittensor_client register [--subnet sn51]   # spends TAO; asks first
"""
import threading
import time


class ChainStats:
    def __init__(self, network: str, netuid: int, hotkey: str, ttl: float = 300, error_ttl: float = 30):
        self.network, self.netuid, self.hotkey = network, netuid, hotkey
        self.ttl, self.error_ttl = ttl, error_ttl
        self._lock = threading.Lock()
        self._cached: dict | None = None
        self._expires = 0.0

    def get(self) -> dict:
        """Cached: a metagraph fetch takes seconds."""
        with self._lock:
            if self._cached is not None and time.time() < self._expires:
                return self._cached
            base = {"network": self.network, "netuid": self.netuid, "hotkey": self.hotkey}
            if not self.hotkey:
                data, ttl = {**base, "available": False, "error": "SN64_HOTKEY is not set"}, self.error_ttl
            else:
                try:
                    data, ttl = {**base, "available": True, "error": None, **self._fetch()}, self.ttl
                except Exception as exc:  # bittensor not installed, network errors, chain hiccups
                    data, ttl = {**base, "available": False, "error": f"{type(exc).__name__}: {exc}"}, self.error_ttl
            data["updated_at"] = time.time()
            self._cached, self._expires = data, time.time() + ttl
            return data

    def _fetch(self) -> dict:
        import bittensor as bt  # optional dependency, imported on use

        subtensor = bt.Subtensor(self.network)
        try:
            mg = subtensor.subnets.metagraph(self.netuid, commitments=False)
        finally:
            subtensor.close()
        if mg is None:
            raise LookupError(f"subnet {self.netuid} does not exist on {self.network}")
        neuron = mg.by_hotkey(self.hotkey)
        result = {"subnet": mg.name, "block": mg.block, "registered": neuron is not None}
        if neuron is not None:
            # Scores are normalized 0..1; stake and emission are Balances in the subnet's alpha.
            result.update(
                uid=neuron.uid,
                stake=neuron.total_stake.amount,
                incentive=neuron.incentive,
                emission=neuron.emission.amount,
                trust=neuron.trust,
                consensus=neuron.consensus,
            )
        return result


def main(argv=None) -> int:
    import argparse

    from . import ops

    parser = argparse.ArgumentParser(prog="bittensor_client")
    sub = parser.add_subparsers(dest="command", required=True)
    ops.add_subnet_arg(sub.add_parser("register", help="burn-register WALLET_HOTKEY (spends TAO; asks first)"))
    args = parser.parse_args(argv)
    cfg = ops.settings()
    ops.register_hotkey(cfg, ops.netuid(args.subnet, cfg))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
