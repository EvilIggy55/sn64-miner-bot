# sn64-miner-bot

A standalone GPU model-serving stack on k3s, laid out like the Chutes (Bittensor subnet 64) miner:
a control node running a scheduler, GPU nodes running model servers, Postgres, Redis and
monitoring.

> **This does not earn on SN64.** The Chutes network only pays GPU workers that run inside
> Intel TDX confidential VMs booted from Chutes' attested images (github.com/chutesai/sek8s),
> registered through the official chutes-miner stack. This repo has the architecture without
> the attestation, so validators won't send it work. Use it to learn the moving parts, or to
> serve models on your own GPUs. To mine SN64, follow github.com/chutesai/chutes-miner.

## What it does

The bot runs a reconcile loop every 30 seconds:

1. **Inventory** (`gpu_manager.py`): reads each node's allocatable `nvidia.com/gpu`, subtracts
   GPUs held by workloads the bot doesn't own, and works out VRAM per GPU. It uses the GPU
   Feature Discovery label if present, then the `miner-bot/gpu-memory-gb` label that
   `install_gpu_node.sh` sets, then `config/miner.yaml`.
2. **Plan** (`chute_scheduler.py`): places the models in `config/models.yaml`, highest
   priority first. Each replica goes on a node with enough free GPUs of enough VRAM, stays on
   nodes it already runs on, and otherwise goes to the tightest fit.
3. **Apply** (`k3s_controller.py`): creates, updates or removes one Deployment plus Service per
   model. These are its *chutes*: vLLM OpenAI-compatible servers, not Chutes-network chutes.
   Everything it creates carries `app.kubernetes.io/managed-by=miner-bot`, and it never
   touches anything without that label.
4. **Autoheal** (`autoheal.py`): restarts chutes that are crash-looping, can't pull their
   image, stay unschedulable, or run but aren't Ready after 10 minutes. After 3 restarts in an
   hour it scales the chute to 0 and marks it failed until you `miner-bot reset` it.

Around the loop:

- `telemetry.py` exports Prometheus metrics and keeps an event log in Postgres. If Postgres is
  down it keeps the events in memory.
- `main.py` serves the HTTP API that `cli.py` talks to, and takes a Redis leader lock so only
  one replica reconciles. Without Redis it runs anyway.
- `bittensor_client.py` reports your hotkey's SN64 metagraph standing: UID, incentive,
  emission and stake. It needs only the public ss58 address.

## Layout

| Path | Purpose |
|---|---|
| `config/miner.yaml` | Bot settings: namespace, reconcile interval, scheduler and autoheal tuning |
| `config/models.yaml` | Models to serve, with GPU count, VRAM, priority, replicas and vLLM args |
| `config/k3s.yaml` | k3s server config, copied to `/etc/rancher/k3s/config.yaml` |
| `scripts/install_k3s.sh` | Control node: k3s server and the NVIDIA device plugin |
| `scripts/install_gpu_node.sh` | GPU host: NVIDIA Container Toolkit, node labels, and k3s agent (or the local server) |
| `scripts/install_gepetto.sh` | Loads `config/` into the bot's ConfigMap and restarts it, like chutes-miner's `gepetto-code` workflow |
| `scripts/register_hotkey.sh` | `btcli subnet register --netuid 64`. Spends TAO; asks for confirmation first |
| `scripts/deploy_stack.sh` | Builds the bot image, imports it into k3s, and applies `k8s/` |
| `scripts/runpod_serve.sh` | Single-GPU start command: no k3s, one vLLM process (see below) |
| `runpod/` | Pod bodies for the single-GPU path, and how to post them |
| `k8s/` | Namespace, Postgres, Redis, the bot (with RBAC) and its ClusterIP Service |
| `monitoring/` | Prometheus scrape config and a Grafana dashboard |

## Requirements

- A Linux control node (Ubuntu) with Docker, for building the bot image.
- GPU nodes with a working NVIDIA driver (`nvidia-smi`). The control node can be a GPU node too.
- For the CLI: Python 3.11+ in a virtualenv (Ubuntu's system Python refuses `pip install`):
  `python3 -m venv .venv && .venv/bin/pip install -e .`, then run `.venv/bin/miner-bot`.

## Setup

1. **Configure.** `cp .env.example .env` and fill it in. Never commit `.env`. Edit
   `config/models.yaml` for your GPUs, then check it offline: `miner-bot validate`.
2. **Control node:** `sudo bash scripts/install_k3s.sh`. It prints the join URL, where to find
   the token, and the k3s version.
3. **GPU nodes:** `sudo bash scripts/install_gpu_node.sh` on each, with `K3S_URL`, `K3S_TOKEN`
   and `K3S_VERSION` in `.env`. On a control node that has GPUs, run it without `K3S_URL`.
4. **Deploy:** on the control node, `sudo bash scripts/deploy_stack.sh`. The repo has no
   Dockerfile; the script builds the image from one inlined in the script and imports it into
   k3s with `k3s ctr images import`, so the bot is pinned to the control node.
5. **Optional, costs TAO:** `bash scripts/register_hotkey.sh` registers your hotkey on SN64.
   Then set `SN64_HOTKEY` in `.env` and rerun `deploy_stack.sh` so the bot reports it.

## Operating

```bash
kubectl -n sn64 port-forward svc/miner-bot 8000:8000 &
miner-bot status        # last reconcile, leader, errors
miner-bot plan          # GPU inventory and where each model would go (dry run)
miner-bot chutes        # running chutes, readiness, problems, restart counts
miner-bot events        # scheduler and autoheal history
miner-bot sn64          # your hotkey on the metagraph
miner-bot reconcile     # reconcile now
miner-bot reset MODEL   # retry a chute autoheal gave up on
```

To change what runs, edit `config/models.yaml` (or `miner.yaml`), then run
`sudo bash scripts/install_gepetto.sh` on the control node. It needs root because k3s's
kubeconfig is readable only by root; or point `KUBECONFIG` at a copy you can read. Chute
endpoints are cluster-internal:
`http://chute-<name>.sn64:8000/v1`.

## Single GPU, no cluster (RunPod)

Everything above is a k3s stack: a scheduler placing replicas across nodes, Postgres, Redis, a
leader lock. On one box with one GPU there is nothing to schedule, so none of it applies.
`src/miner_bot/local_serve.py` is the short path instead: it reads `config/models.yaml`, picks
the highest-priority model that fits the local GPU, and execs vLLM with the same arguments
`k3s_controller.deployment_body()` would have built.

It still does not mine SN64 — no wallet, no `btcli`, no registration, nothing that spends TAO.
It serves models.

```bash
miner-bot-serve --dry-run     # what it would run, and why it skipped the rest
miner-bot-serve               # exec vLLM
miner-bot-serve --model qwen2-5-coder-7b
```

On a 24 GB card (4090, A10G, L4) `qwen3-32b` is skipped — it wants 2 GPUs of 48 GB — and
`qwen3-8b` wins on priority. `replicas: 2` is ignored: one process serves one model.
`--gpu-memory-utilization 0.90` with `--max-model-len 16384` leaves roughly 5 GB of KV cache
after Qwen3-8B's weights, which is fine at low concurrency and will queue under load.

### RunPod

Two pod bodies in `runpod/`, both for the REST `POST /v1/pods` (the console's fields are the
same names: Container Image, Container Start Command, Volume Mount Path, Expose HTTP Ports):

| File | Use |
|---|---|
| `runpod/pod-serve.json` | Clones this repo and runs `scripts/runpod_serve.sh`, so `config/models.yaml` picks the model. **Needs this repo pushed to a git remote** — set `REPO_URL`. |
| `runpod/pod-serve-norepo.json` | No git remote. vLLM arguments only, appended to the image's entrypoint. Works as-is; `models.yaml` isn't consulted, so keep the two in step by hand. |

`runpod/README.md` has the `curl`, the console field names, and what to set before launching.

Both expose one port, `8000/http`, and set `VLLM_API_KEY`. **Set it.** A pod port is on a public
IP, and vLLM will otherwise answer anyone who finds it — `/metrics` is on that same port, so
there is nothing to open 9090 or 9100 for. `HF_HOME=/workspace/hf` puts weights on the volume
so a restart doesn't re-download ~16 GB.

`dockerEntrypoint` matters: RunPod's start command only overrides Docker `CMD`, and
`vllm/vllm-openai` sets `ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]` with
no `CMD`. Leave the entrypoint alone and your start command is read as vLLM flags (which is
exactly what the no-repo body wants); override it with `["bash","-lc"]` to run a shell line
(what the repo body needs).

```bash
curl -s https://<pod-id>-8000.proxy.runpod.net/v1/models -H "Authorization: Bearer $VLLM_API_KEY"
```

## Monitoring

Load `monitoring/prometheus.yaml` into a Prometheus running in the cluster. It scrapes the bot,
every chute's vLLM metrics, and the DCGM exporter if you run it. Then import
`monitoring/grafana-dashboard.json` into Grafana and point it at that Prometheus.

## Security notes

- The bot's API can create and delete Deployments. It's ClusterIP only; reach it through
  `kubectl port-forward` and never expose it.
- The bot's RBAC is limited to Deployments, Pods and Services in `sn64`, plus read-only access
  to nodes and pods cluster-wide for the inventory.
- `.env` holds the Postgres password, Hugging Face token and k3s join token. Keep it out of git.
