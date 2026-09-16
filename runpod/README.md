# RunPod pod bodies

Two ways to serve one model on one GPU. Both are bodies for RunPod's REST `POST /v1/pods`, with
no `_comment` or other extra keys, so they can be posted as-is:

```bash
curl -X POST https://rest.runpod.io/v1/pods \
  -H "Authorization: Bearer $RUNPOD_API_KEY" \
  -H "Content-Type: application/json" \
  -d @runpod/pod-serve-norepo.json
```

The console's template fields carry the same names: Container Image (`imageName`), Container
Start Command (`dockerStartCmd` / `dockerEntrypoint`), Volume Mount Path (`volumeMountPath`),
Expose HTTP Ports (`ports`), Environment Variables (`env`).

Neither of these mines SN64. There is no wallet, no `btcli`, no registration — nothing that
spends TAO. Chutes only pays TDX-attested workers and a RunPod 4090 is not one; see the note at
the top of the repo README.

## `pod-serve.json` — driven by `config/models.yaml`

Clones this repo into the volume and execs `scripts/runpod_serve.sh`, which runs
`miner_bot.local_serve`: it reads `config/models.yaml`, picks the highest-priority model that
fits the GPU, and execs vLLM. Change a model by editing `models.yaml` and restarting the pod.

**Prerequisite: this repo must be on a git remote.** It has no `.git` yet. Until you push it,
`$REPO_URL` has nothing to clone and the pod will exit on the first boot step — use the no-repo
body instead.

## `pod-serve-norepo.json` — plain vLLM arguments

Nothing from this repo runs. `dockerStartCmd` is appended to the image's existing entrypoint as
vLLM flags. Works today with no git remote; the flags are `qwen3-8b` from `models.yaml` written
out by hand, so the two drift apart unless you keep them in step.

## Why `dockerEntrypoint` differs between the two

RunPod's start command overrides Docker `CMD`, not `ENTRYPOINT`. `vllm/vllm-openai` sets
`ENTRYPOINT ["python3","-m","vllm.entrypoints.openai.api_server"]` and no `CMD`:

- leave the entrypoint alone and `dockerStartCmd` arrives as vLLM's own flags — what
  `pod-serve-norepo.json` wants;
- override it with `["bash","-lc"]` to run a shell line — what `pod-serve.json` needs to clone
  the repo first.

## Before you launch

- **`VLLM_API_KEY`.** Generate one (`openssl rand -hex 32`) and set it. A pod port is on a
  public IP and vLLM will answer anyone who finds it. It also serves `/metrics` on that same
  port, so there is no reason to expose 9090 or 9100.
- **`gpuTypeIds`** is `["NVIDIA GeForce RTX 4090"]`. Other ids come from `GET /v1/gpuTypes`.
- **`cloudType`** is `SECURE` here. `COMMUNITY` is cheaper and less reliable; it's a choice,
  not a requirement.
- **`HF_TOKEN`** is only needed for gated models. Leave it empty otherwise.
- **`HF_HOME=/workspace/hf`** keeps weights on the volume. Without it every restart
  re-downloads ~16 GB onto the container disk and then loses it.
