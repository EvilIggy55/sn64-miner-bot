#!/usr/bin/env bash
# Build the bot image, load it into k3s, and deploy everything in k8s/.
# Run on the control node from a checkout of this repo (needs docker, and root for `k3s ctr`):
#   sudo bash scripts/deploy_stack.sh
# There is no Dockerfile in the repo: the image is built from the one inlined below.
set -euo pipefail
cd "$(dirname "$0")/.."

# Load .env without overriding anything already set in the environment.
if [[ -f .env ]]; then
  while IFS='=' read -r key value; do
    [[ $key =~ ^[A-Z_][A-Z0-9_]*$ && -z "${!key:-}" ]] || continue
    value="${value%\"}"; value="${value#\"}"
    export "$key=$value"
  done < <(grep -Ev '^\s*(#|$)' .env)
fi

NAMESPACE=sn64  # matches k8s/*.yaml
IMAGE="${IMAGE:-miner-bot:0.1.0}"
KUBECTL="${KUBECTL:-kubectl}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi
: "${POSTGRES_PASSWORD:?Set POSTGRES_PASSWORD in .env}"
if [[ ! $POSTGRES_PASSWORD =~ ^[A-Za-z0-9]+$ ]]; then
  echo "POSTGRES_PASSWORD must be letters and digits only (it goes into a connection URL)." >&2
  exit 1
fi

echo "Building $IMAGE..."
docker build -t "$IMAGE" -f - . <<'DOCKERFILE'
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[chain]"
ENV MINER_BOT_CONFIG_DIR=/config
EXPOSE 8000
CMD ["uvicorn", "miner_bot.main:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE

echo "Importing $IMAGE into k3s..."
docker save "$IMAGE" | k3s ctr images import -

$KUBECTL apply -f k8s/namespace.yaml

secret_args=(
  --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD"
  --from-literal=DATABASE_URL="postgresql://miner_bot:${POSTGRES_PASSWORD}@postgres:5432/miner_bot"
  --from-literal=BT_NETWORK="${BT_NETWORK:-finney}"
)
# Optional values are only stored when set, so an empty one never overrides a default.
[[ -n "${SN64_HOTKEY:-}" ]] && secret_args+=(--from-literal=SN64_HOTKEY="$SN64_HOTKEY")
[[ -n "${HF_TOKEN:-}" ]] && secret_args+=(--from-literal=HF_TOKEN="$HF_TOKEN")
$KUBECTL -n "$NAMESPACE" create secret generic miner-bot-secrets "${secret_args[@]}" \
  --dry-run=client -o yaml | $KUBECTL apply -f -

# Validate config/ with the image just built, then load it into the ConfigMap.
docker run --rm -v "$PWD/config:/config:ro" "$IMAGE" miner-bot validate --config /config
RESTART=0 SKIP_VALIDATE=1 KUBECTL="$KUBECTL" bash scripts/install_gepetto.sh

$KUBECTL apply -f k8s/postgres.yaml -f k8s/redis.yaml -f k8s/miner-bot-deployment.yaml -f k8s/miner-bot-service.yaml
$KUBECTL -n "$NAMESPACE" set image deployment/miner-bot bot="$IMAGE"
# Same tag, new image: restart so the pod picks it up.
$KUBECTL -n "$NAMESPACE" rollout restart deployment/miner-bot
$KUBECTL -n "$NAMESPACE" rollout status deployment/miner-bot --timeout=300s

cat <<EOF

Deployed. To use the CLI from this machine:
  kubectl -n $NAMESPACE port-forward svc/miner-bot 8000:8000 &
  miner-bot status
EOF
