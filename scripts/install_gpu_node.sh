#!/usr/bin/env bash
# Prepare a GPU host for chutes: NVIDIA Container Toolkit, node labels, and k3s.
#   Separate GPU host:            sudo bash scripts/install_gpu_node.sh   (K3S_URL, K3S_TOKEN set)
#   Control node with GPUs too:   sudo bash scripts/install_gpu_node.sh   (after install_k3s.sh, K3S_URL unset)
# The NVIDIA driver must already work (nvidia-smi). k3s only detects the NVIDIA runtime when it
# starts, so the toolkit goes on first and k3s is started (or restarted) afterwards.
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

TOOLKIT_VERSION="${NVIDIA_CONTAINER_TOOLKIT_VERSION:-1.20.0-1}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi
if ! command -v nvidia-smi >/dev/null || ! nvidia-smi -L; then
  echo "nvidia-smi doesn't work: install the NVIDIA driver first." >&2
  exit 1
fi

# NVIDIA Container Toolkit, following NVIDIA's apt install guide.
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gnupg2
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list
apt-get update
apt-get install -y \
  "nvidia-container-toolkit=${TOOLKIT_VERSION}" \
  "nvidia-container-toolkit-base=${TOOLKIT_VERSION}" \
  "libnvidia-container-tools=${TOOLKIT_VERSION}" \
  "libnvidia-container1=${TOOLKIT_VERSION}"
# No `nvidia-ctk runtime configure`: k3s runs its own containerd and adds the nvidia runtime itself.

# Labels read by the bot's scheduler (VRAM per GPU, rounded; the smallest GPU if they differ)
# and by the device plugin's node selector.
mem_mib="$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | sort -n | head -n1)"
gpu_gb=$(( (mem_mib + 512) / 1024 ))
product="$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1 \
  | sed 's/[^A-Za-z0-9_.-]/-/g' | cut -c1-63 | sed 's/^[-_.]*//; s/[-_.]*$//')"
labels=("miner-bot/gpu=true" "miner-bot/gpu-memory-gb=${gpu_gb}" "miner-bot/gpu-product=${product}")
echo "Node labels: ${labels[*]}"

if [[ -n "${K3S_URL:-}" ]]; then
  if [[ -z "${K3S_TOKEN:-}" ]]; then
    echo "K3S_TOKEN is required with K3S_URL." >&2
    exit 1
  fi
  mkdir -p /etc/rancher/k3s
  {
    echo "node-label:"
    for label in "${labels[@]}"; do echo "  - \"${label}\""; done
  } > /etc/rancher/k3s/config.yaml
  if [[ -n "${K3S_VERSION:-}" ]]; then
    export INSTALL_K3S_VERSION="$K3S_VERSION"
  fi
  curl -sfL https://get.k3s.io | K3S_URL="$K3S_URL" K3S_TOKEN="$K3S_TOKEN" sh -s - agent
  echo "Joined. On the control node, check: kubectl get node $(hostname) -L miner-bot/gpu-memory-gb"
elif systemctl is-active --quiet k3s; then
  systemctl restart k3s  # picks up the NVIDIA runtime installed above
  node="$(hostname | tr '[:upper:]' '[:lower:]')"
  # The API server takes a moment to come back after the restart.
  for _ in $(seq 90); do
    k3s kubectl get node "$node" >/dev/null 2>&1 && break
    sleep 2
  done
  k3s kubectl wait --for=condition=Ready node "$node" --timeout=180s
  k3s kubectl label node "$node" "${labels[@]}" --overwrite
else
  echo "Set K3S_URL and K3S_TOKEN to join a cluster, or run install_k3s.sh on this host first." >&2
  exit 1
fi
