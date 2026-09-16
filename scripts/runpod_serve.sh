#!/usr/bin/env bash
# Start command for a single-GPU pod (RunPod or any one-box host running the vLLM image).
# Serves one model from config/models.yaml. See src/miner_bot/local_serve.py.
#
# This is not an SN64 miner: no wallet, no btcli, no registration, nothing that spends TAO.
# The pod's start command clones this repo and execs this script; everything after the clone
# lives here so it's version-controlled rather than pasted into a template field.
#
# Environment (set these in the pod template):
#   VLLM_API_KEY  required in practice. vLLM demands it on every request; without it the
#                 OpenAI API on the public port is open to anyone who finds it.
#   HF_TOKEN      only for gated models.
#   REPO_DIR      where the pod cloned this repo (default /workspace/sn64-miner-bot).
#   HF_HOME       weight cache (default /workspace/hf: on the volume, so it survives restarts).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/workspace/sn64-miner-bot}"
export HF_HOME="${HF_HOME:-/workspace/hf}"
mkdir -p "$HF_HOME"

# The vLLM image ships python 3.12 and vLLM itself; pyyaml is the only thing local_serve.py
# needs that might be missing. Don't `pip install -e .`: that drags in kubernetes, psycopg and
# redis for a box that has none of them.
python3 -c "import yaml" 2>/dev/null || pip install --no-cache-dir --quiet pyyaml

cd "$REPO_DIR"
export PYTHONPATH="$REPO_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m miner_bot.local_serve "$@"
