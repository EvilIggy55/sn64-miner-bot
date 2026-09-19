#!/usr/bin/env bash
# Register your hotkey on the subnet set by `netuid:` in config/miner.yaml. THIS SPENDS TAO: the
# subnet's current registration burn, which isn't refundable. Run it once, on the machine that
# holds your wallet. It doesn't announce an axon.
#   bash scripts/register_hotkey.sh      (WALLET_NAME and WALLET_HOTKEY in .env or the environment)
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

# Same netuid the bot and sn51_cli.py use, so the two can't drift apart.
NETUID="$(sed -nE 's/^netuid:[[:space:]]*([0-9]+)[[:space:]]*(#.*)?$/\1/p' config/miner.yaml)"
if [[ ! $NETUID =~ ^[0-9]+$ ]]; then
  echo "Can't read a numeric 'netuid:' from config/miner.yaml; not registering." >&2
  exit 1
fi
NETWORK="${BT_NETWORK:-finney}"
: "${WALLET_NAME:?Set WALLET_NAME (in .env or the environment)}"
: "${WALLET_HOTKEY:?Set WALLET_HOTKEY (in .env or the environment)}"

if ! command -v btcli >/dev/null; then
  echo "btcli not found. Install it with: pip install bittensor-cli" >&2
  exit 1
fi

cat <<EOF
About to register hotkey '${WALLET_HOTKEY}' of wallet '${WALLET_NAME}' on netuid ${NETUID} (${NETWORK}).
 - This burns TAO. btcli shows the exact cost and asks again before submitting.
 - Register once. A second UID only competes with your first.
EOF
read -r -p "Type REGISTER to continue: " answer
if [[ "$answer" != "REGISTER" ]]; then
  echo "Aborted; nothing was submitted."
  exit 1
fi

btcli subnet register \
  --netuid "$NETUID" \
  --network "$NETWORK" \
  --wallet.name "$WALLET_NAME" \
  --wallet.hotkey "$WALLET_HOTKEY"

echo "Put the hotkey's ss58 address in .env as BT_HOTKEY so the bot can report it."
