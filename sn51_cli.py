import json
import os
import shlex
import shutil
import subprocess
import sys
import time

import typer

app = typer.Typer(help="SN51 Miner CLI — Autonomous Fleet Control")

SESSION = "miner"
SUBNET = "sn51"  # passed to every chain command; config/miner.yaml says 64
PY = sys.executable  # same interpreter/venv this CLI runs under
# Serves the best-fitting model from config/models.yaml with vLLM. Run from the repo root.
SERVE = [PY, "-m", "miner_bot.local_serve"]

# -----------------------------
# Utility
# -----------------------------

def fail(msg):
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

def require(tool):
    if shutil.which(tool) is None:
        fail(f"'{tool}' not found on PATH — this command needs a Linux rig with {tool} installed.")

def run(args):
    """Run a non-interactive command and return its stdout."""
    try:
        return subprocess.run(args, capture_output=True, text=True, check=True).stdout
    except FileNotFoundError:
        fail(f"Command not found: {args[0]}")
    except subprocess.CalledProcessError as e:
        fail(f"'{shlex.join(args)}' exited {e.returncode}:\n{(e.stderr or e.stdout).strip()}")

def run_interactive(args):
    """Run a command attached to this terminal, so password prompts are visible.
    Never capture output here — a hidden prompt just hangs."""
    rc = subprocess.run(args).returncode
    if rc != 0:
        fail(f"'{shlex.join(args)}' exited {rc}")

def run_json(args):
    out = run(args)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        fail(f"Expected JSON from '{shlex.join(args)}', got:\n{out.strip()[:500]}")

def module(name, *extra):
    return [PY, "-m", f"miner_bot.{name}", *extra]

def pretty(data):
    typer.echo(json.dumps(data, indent=2))

def session_exists():
    return subprocess.run(["tmux", "has-session", "-t", SESSION],
                          capture_output=True).returncode == 0

# -----------------------------
# Node Commands
# -----------------------------

@app.command()
def start():
    """Serve the best-fitting model with vLLM in tmux (local_serve)"""
    require("tmux")
    if session_exists():
        typer.echo(f"Miner already running (tmux session '{SESSION}').")
        return
    typer.echo("Starting vLLM (miner_bot.local_serve)...")
    run(["tmux", "new-session", "-d", "-s", SESSION, "-c", os.getcwd(), shlex.join(SERVE)])
    time.sleep(3)
    if not session_exists():
        fail("Miner exited right after starting. Run it in the foreground to see why:\n"
             f"  {shlex.join(SERVE)}")
    typer.echo(f"Miner started. Attach with: tmux attach -t {SESSION}")

@app.command()
def stop():
    """Stop SN51 miner"""
    require("tmux")
    if not session_exists():
        typer.echo("Miner not running.")
        return
    typer.echo("Stopping miner...")
    run(["tmux", "kill-session", "-t", SESSION])
    typer.echo("Miner stopped.")

@app.command()
def restart():
    """Restart SN51 miner"""
    stop()
    start()

# -----------------------------
# Metrics
# -----------------------------

@app.command()
def tao():
    """Show TAO/day"""
    pretty(run_json(module("tao_monitor", "--subnet", SUBNET)))

@app.command()
def graval():
    """Show GraVal score"""
    pretty(run_json(module("graval_monitor")))

@app.command()
def gpu():
    """Show GPU telemetry (one entry per GPU)"""
    require("nvidia-smi")
    out = run(["nvidia-smi",
               "--query-gpu=index,temperature.gpu,utilization.gpu,memory.used,memory.total",
               "--format=csv,noheader,nounits"])
    gpus = []
    for line in out.strip().splitlines():
        idx, temp, util, used, total = (f.strip() for f in line.split(","))
        gpus.append({"gpu": int(idx), "temp_c": temp, "util_pct": util,
                     "vram_used_mib": used, "vram_total_mib": total})
    pretty(gpus)

# -----------------------------
# Wallet
# -----------------------------

@app.command()
def wallet():
    """Show wallet balance"""
    pretty(run_json(module("wallet_monitor", "--subnet", SUBNET)))

@app.command()
def topup(execute: bool = typer.Option(False, "--execute", help="Send the TAO (asks first) instead of printing the plan")):
    """Check the coldkey covers a registration; plan or run a top-up"""
    run_interactive(module("auto_topup", "--subnet", SUBNET, *(["--execute"] if execute else [])))

# -----------------------------
# Registration
# -----------------------------

@app.command()
def register():
    """Burn-register the hotkey (asks first; btcli may prompt for coldkey password)"""
    typer.echo("Registering miner...")
    run_interactive(module("bittensor_client", "register", "--subnet", SUBNET))
    typer.echo("Registration complete.")

@app.command()
def rereg():
    """Re-register if deregistered (asks before spending; may prompt for coldkey password)"""
    run_interactive(module("auto_reregistration", "--subnet", SUBNET))

# -----------------------------
# Models
# -----------------------------

@app.command()
def models():
    """Show loaded models"""
    pretty(run_json(module("model_loader", "--list")))

@app.command()
def optimize():
    """Show which model fits this box best, and the vLLM command (dry run)"""
    run_interactive(module("model_optimizer"))

# -----------------------------
# Warm Pool
# -----------------------------

@app.command()
def warm():
    """Show warm-pool status"""
    pretty(run_json(module("warm_pool_status")))

@app.command("warm-reset")
def warm_reset():
    """Reset failed chutes so the scheduler brings them back"""
    run_interactive(module("warm_pool_reset"))

# -----------------------------
# Profit Simulation
# -----------------------------

@app.command()
def simulate():
    """Run SN51 profit simulation"""
    pretty(run_json(module("profit_simulator_sn51", "--subnet", SUBNET)))

# -----------------------------
# Fleet Commands
# -----------------------------

@app.command()
def fleet():
    """Show fleet status"""
    pretty(run_json(module("fleet_monitor")))

@app.command("fleet-scale")
def fleet_scale():
    """Trigger auto-scaler"""
    typer.echo("Running auto-scaler...")
    run_interactive(module("auto_scaler_sn51"))
    typer.echo("Auto-scaler complete.")

# -----------------------------
# Entry
# -----------------------------

if __name__ == "__main__":
    app()
