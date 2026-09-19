"""Daily profit from live chain data: emission in, GPU rent out, registration burn to recover.

Revenue comes from a real neuron's last-epoch emission:
- your hotkey (BT_HOTKEY) if it's registered on the subnet, or
- with --percentile P, the miner at that percentile of emission among neurons earning
  anything. Use this for "what would a typical miner here make". Getting there would take a
  miner that validators actually score. This repo isn't one (see README), so its own number is 0.

Cost is --gpu-cost-per-hour (USD, or GPU_COST_PER_HOUR) × --gpus. Profit in USD needs --tao-usd
(or TAO_USD). Without it, revenue is shown in TAO and profit is left null rather than guessed.

    python -m miner_bot.profit_simulator_sn51 [--subnet sn51] [--percentile 50]
        [--gpu-cost-per-hour 0.69] [--gpus 1] [--tao-usd 350]
"""
import argparse
import os
import sys

from . import ops


def env_float(name):
    value = os.getenv(name)
    try:
        return float(value) if value else None
    except ValueError:
        ops.die(f"{name} must be a number, got {value!r}")


def pick_neuron(mg, hotkey, percentile):
    if percentile is None:
        neuron = mg.by_hotkey(hotkey) if hotkey else None
        return neuron, ("your hotkey" if neuron else "your hotkey (not registered: earns nothing)")
    earners = sorted((n for n in mg.neurons if n.emission.rao > 0 and not n.validator_permit),
                     key=lambda n: n.emission.rao)
    if not earners:
        return None, "no miner on this subnet earned anything last epoch"
    index = min(len(earners) - 1, int(len(earners) * percentile / 100))
    return earners[index], f"miner at the {percentile:g}th percentile of {len(earners)} earning miners"


def main(argv=None) -> int:
    ops.load_dotenv()  # before the parser reads GPU_COST_PER_HOUR / TAO_USD as defaults
    parser = argparse.ArgumentParser(prog="profit_simulator_sn51", description=__doc__.splitlines()[0])
    ops.add_subnet_arg(parser)
    parser.add_argument("--percentile", type=float, help="use the miner at this emission percentile (0-100)")
    parser.add_argument("--gpu-cost-per-hour", type=float, default=env_float("GPU_COST_PER_HOUR"))
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument("--tao-usd", type=float, default=env_float("TAO_USD"))
    args = parser.parse_args(argv)
    if args.percentile is not None and not 0 <= args.percentile <= 100:
        ops.die("--percentile must be between 0 and 100")
    cfg = ops.settings()
    uid = ops.netuid(args.subnet, cfg)

    def fetch(subtensor):
        mg = ops.metagraph(subtensor, uid)
        neuron, basis = pick_neuron(mg, cfg.hotkey, args.percentile)
        revenue = ops.earnings(mg, neuron) if neuron else {"alpha_per_day": 0.0, "tao_per_day_at_spot": 0.0}
        return {"subnet": mg.name, "block": mg.block, "basis": basis,
                "registration_burn_tao": subtensor.subnets.burn(uid).tao, **revenue}

    result = {"network": cfg.network, "netuid": uid, **ops.chain_read(cfg.network, fetch)}
    if not result["available"]:
        return ops.emit(result)

    tao_day = result["tao_per_day_at_spot"]
    cost_usd = args.gpu_cost_per_hour * 24 * args.gpus if args.gpu_cost_per_hour is not None else None
    revenue_usd = tao_day * args.tao_usd if args.tao_usd is not None and tao_day is not None else None
    net_usd = revenue_usd - cost_usd if revenue_usd is not None and cost_usd is not None else None
    burn_usd = result["registration_burn_tao"] * args.tao_usd if args.tao_usd is not None else None
    result.update(
        gpu_cost_usd_per_day=cost_usd,
        revenue_usd_per_day=revenue_usd,
        net_usd_per_day=net_usd,
        breakeven_days=round(burn_usd / net_usd, 1) if net_usd and net_usd > 0 else None,
        assumptions=[
            "revenue = last epoch's emission x epochs per day; it moves every epoch",
            "alpha valued at spot price; unstaking slippage and fees not included",
            "cost is GPU rent only: no power, bandwidth or storage" if cost_usd is not None
            else "no GPU cost given: pass --gpu-cost-per-hour",
            f"TAO at ${args.tao_usd:g}" if args.tao_usd is not None else "no TAO price given: pass --tao-usd",
        ],
    )
    return ops.emit(result)


if __name__ == "__main__":
    sys.exit(main())
