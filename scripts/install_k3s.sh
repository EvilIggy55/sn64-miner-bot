#!/usr/bin/env bash
# Install the k3s server on the control node with config/k3s.yaml, then the NVIDIA device plugin.
#   sudo bash scripts/install_k3s.sh
# Optional: K3S_VERSION=v1.xx.y+k3s1 pins k3s (give GPU nodes the same version).
set -euo pipefail
cd "$(dirname "$0")/.."

DEVICE_PLUGIN_VERSION="${DEVICE_PLUGIN_VERSION:-v0.17.1}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

install -D -m 0644 config/k3s.yaml /etc/rancher/k3s/config.yaml
if [[ -n "${K3S_VERSION:-}" ]]; then
  export INSTALL_K3S_VERSION="$K3S_VERSION"
fi
curl -sfL https://get.k3s.io | sh -s - server

echo "Waiting for the node to become Ready..."
# `kubectl wait --all` fails if the node hasn't registered yet, so wait for it to appear first.
for _ in $(seq 90); do
  [[ -n "$(k3s kubectl get nodes -o name 2>/dev/null)" ]] && break
  sleep 2
done
k3s kubectl wait --for=condition=Ready node --all --timeout=180s

# The device plugin advertises nvidia.com/gpu. On k3s it has to run with the nvidia RuntimeClass
# to see the GPUs, and only on GPU nodes (install_gpu_node.sh labels them miner-bot/gpu=true).
k3s kubectl apply -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"
k3s kubectl -n kube-system patch daemonset nvidia-device-plugin-daemonset --type merge -p \
  '{"spec":{"template":{"spec":{"runtimeClassName":"nvidia","nodeSelector":{"miner-bot/gpu":"true"}}}}}'

version="$(k3s --version | head -n1 | awk '{print $3}')"
cat <<EOF

k3s ${version} is running.
Next, on each GPU host, run scripts/install_gpu_node.sh with:
  K3S_URL=https://<this node's IP>:6443
  K3S_TOKEN=<sudo cat /var/lib/rancher/k3s/server/node-token>
  K3S_VERSION=${version}
If this node has GPUs too, run: sudo bash scripts/install_gpu_node.sh
EOF
