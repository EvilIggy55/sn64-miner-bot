"""Shared plumbing for the one-shot commands behind sn51_cli.py (`python -m miner_bot.<name>`).

Contract for every command module:
- Status commands print only JSON on stdout. Notes and warnings go to stderr.
- A missing prerequisite (bittensor not installed, no hotkey set, the bot unreachable) is
  reported as `{"available": false, "error": ...}` or a one-line error on stderr, never a
  traceback and never a made-up number.
- Chain reads need only public ss58 addresses. Nothing here reads a key or a password.
- Anything that spends TAO shells out to btcli with this terminal attached, after you type a
  confirmation word. btcli then shows the exact amount and asks again.

Run from the repo root (config/ is read from there), or set MINER_BOT_CONFIG_DIR.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .config import Config, ConfigError, load

BLOCK_SECONDS = 12
BOT_URL = os.getenv("MINER_BOT_URL", "http://127.0.0.1:8000")


def die(message: str, code: int = 1):
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


def note(message: str):
    print(message, file=sys.stderr)


def emit(data) -> int:
    print(json.dumps(data, indent=2, default=str))
    return 0


def load_dotenv(path: str = ".env"):
    """Same rules as the scripts: KEY=value lines, never overriding the environment."""
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#") and key.isupper() and not os.environ.get(key):
            os.environ[key] = value.strip().strip('"')


def settings() -> Config:
    load_dotenv()
    try:
        return load()
    except ConfigError as exc:
        die(f"{exc}\nRun from the sn64-miner-bot repo root, or set MINER_BOT_CONFIG_DIR.")


def netuid(subnet: str | None, cfg: Config) -> int:
    """`--subnet sn51` or `--subnet 51`; config/miner.yaml's netuid when not given."""
    if not subnet:
        return cfg.netuid
    digits = subnet.lower().removeprefix("sn")
    if not digits.isdigit():
        die(f"--subnet must look like sn51 or 51, got {subnet!r}")
    return int(digits)


def add_subnet_arg(parser):
    parser.add_argument("--subnet", help="netuid as sn51 or 51 (default: netuid in config/miner.yaml)")


# -----------------------------
# Bot API (reach it with: kubectl -n sn64 port-forward svc/miner-bot 8000:8000)
# -----------------------------

def bot_api(path: str, method: str = "GET"):
    request = urllib.request.Request(BOT_URL.rstrip("/") + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        die(f"{method} {path}: HTTP {exc.code} {exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        die(f"cannot reach the bot at {BOT_URL} ({exc.reason}). Open a tunnel first:\n"
            "  kubectl -n sn64 port-forward svc/miner-bot 8000:8000")


# -----------------------------
# Chain reads (bittensor 11, the optional `chain` extra)
# -----------------------------

def chain_read(network: str, fetch) -> dict:
    """{"available": True, **fetch(subtensor)}, or available False with the reason."""
    try:
        import bittensor as bt

        subtensor = bt.Subtensor(network)
        try:
            return {"available": True, "error": None, **fetch(subtensor)}
        finally:
            subtensor.close()
    except Exception as exc:  # bittensor not installed, network errors, chain hiccups
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def metagraph(subtensor, uid: int):
    mg = subtensor.subnets.metagraph(uid, commitments=False)
    if mg is None:
        raise LookupError(f"subnet {uid} does not exist")
    return mg


def earnings(mg, neuron) -> dict:
    """A neuron's last-epoch emission, extrapolated to a day.

    Assumes the metagraph's per-uid `emission` is what the last epoch paid (subtensor writes its
    Emission map once per epoch, every tempo + 1 blocks). bittensor 11 doesn't document the
    cadence. If a live miner's alpha_per_day looks ~360x off, this is why.

    Emission is paid in the subnet's alpha, not TAO. The TAO figure uses the pool's spot price
    and ignores the slippage you'd take unstaking, so treat it as an upper bound.
    """
    epochs_per_day = 86400 / BLOCK_SECONDS / (mg.tempo + 1)
    alpha_per_day = neuron.emission.amount * epochs_per_day
    return {
        "uid": neuron.uid,
        "incentive": neuron.incentive,
        "alpha_per_epoch": neuron.emission.amount,
        "epochs_per_day": round(epochs_per_day, 2),
        "alpha_per_day": alpha_per_day,
        "alpha_price_tao": mg.price,
        "tao_per_day_at_spot": alpha_per_day * mg.price if mg.price is not None else None,
    }


# -----------------------------
# btcli (spends TAO; interactive)
# -----------------------------

def btcli(args: list[str], confirm_word: str, summary: str):
    if shutil.which("btcli") is None:
        die("btcli not found. Install it with: pip install bittensor-cli")
    print(summary)
    if input(f"Type {confirm_word} to continue: ").strip() != confirm_word:
        die("aborted; nothing was submitted.")
    sys.exit(subprocess.run(["btcli", *args]).returncode)


def wallet_names() -> tuple[str, str]:
    name, hotkey = os.getenv("WALLET_NAME"), os.getenv("WALLET_HOTKEY")
    if not name or not hotkey:
        die("set WALLET_NAME and WALLET_HOTKEY (in .env or the environment)")
    return name, hotkey


def register_hotkey(cfg: Config, uid: int):
    """Burn-register WALLET_HOTKEY on `uid`. Same flow as scripts/register_hotkey.sh."""
    name, hotkey = wallet_names()
    cost = chain_read(cfg.network, lambda s: {"burn_tao": s.subnets.burn(uid).tao})
    lines = [f"About to register hotkey '{hotkey}' of wallet '{name}' on netuid {uid} ({cfg.network}).",
             f" - Burns {cost['burn_tao']:.4f} TAO at the current price. Not refundable."
             if cost["available"] else " - Burns TAO. btcli shows the exact cost.",
             " - Nothing in this repo earns emissions (see README), so expect the UID to be"
             " pruned for low incentive once its immunity period ends."]
    if uid != cfg.netuid:
        lines.append(f" - WARNING: config/miner.yaml tracks netuid {cfg.netuid}, not {uid}.")
    btcli(["subnet", "register", "--netuid", str(uid), "--network", cfg.network,
           "--wallet.name", name, "--wallet.hotkey", hotkey],
          "REGISTER", "\n".join(lines))
