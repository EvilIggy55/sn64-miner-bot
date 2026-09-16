#!/usr/bin/env bash
# Install the scheduling strategy: config/miner.yaml + config/models.yaml go into the
# miner-bot-config ConfigMap, and the bot is restarted to load them.
#
# Named after chutes-miner's gepetto, whose strategy is updated the same way (a gepetto-code
# ConfigMap plus a rollout restart). Here the strategy is data (which models, priorities, GPU
# needs); the placement logic itself is src/miner_bot/chute_scheduler.py.
#   sudo bash scripts/install_gepetto.sh     (after editing config/, on the control node; root
#                                             because k3s's kubeconfig is root-only, or set KUBECONFIG)
#   RESTART=0 skips the restart; SKIP_VALIDATE=1 skips the config check.
set -euo pipefail
cd "$(dirname "$0")/.."

NAMESPACE=sn64  # matches k8s/*.yaml
IMAGE="${IMAGE:-miner-bot:0.1.0}"
KUBECTL="${KUBECTL:-kubectl}"

# Check the config before it reaches the cluster.
if [[ "${SKIP_VALIDATE:-0}" != 1 ]]; then
  if python3 -c 'import yaml' 2>/dev/null; then
    python3 src/cli.py validate --config config
  elif command -v docker >/dev/null && docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker run --rm -v "$PWD/config:/config:ro" "$IMAGE" miner-bot validate --config /config
  else
    echo "Can't validate config/: needs python3 with PyYAML, or the $IMAGE image. SKIP_VALIDATE=1 skips." >&2
    exit 1
  fi
fi

$KUBECTL -n "$NAMESPACE" create configmap miner-bot-config \
  --from-file=miner.yaml=config/miner.yaml \
  --from-file=models.yaml=config/models.yaml \
  --dry-run=client -o yaml | $KUBECTL apply -f -

if [[ "${RESTART:-1}" == 1 ]] && $KUBECTL -n "$NAMESPACE" get deployment miner-bot >/dev/null 2>&1; then
  $KUBECTL -n "$NAMESPACE" rollout restart deployment/miner-bot
  $KUBECTL -n "$NAMESPACE" rollout status deployment/miner-bot --timeout=180s
fi
