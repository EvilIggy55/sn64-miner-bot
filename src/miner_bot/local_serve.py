"""Serve one model from config/models.yaml on this machine's GPU, without Kubernetes.

main.py places chutes across a k3s cluster. On a single box -- a RunPod pod, a workstation --
there is nothing to place: one node, one GPU. This picks the highest-priority model in
config/models.yaml that fits the local GPU and execs vLLM with the same arguments
k3s_controller.deployment_body() would have given it.

This serves models. It is not an SN64 miner: no wallet, no chain, no registration, nothing
that spends TAO. Chutes only pays TDX-attested workers (see README), so nothing here earns.

    python -m miner_bot.local_serve                           # exec vLLM
    python -m miner_bot.local_serve --dry-run                 # print the command and exit
    python -m miner_bot.local_serve --model qwen2-5-coder-7b  # override the choice

Set VLLM_API_KEY in the environment: vLLM reads it and requires it on every request. On a host
with a public IP that is the only thing between your GPU and the internet.
"""
import argparse
import os
import subprocess
import sys

from .models_registry import ModelSpec, ModelsError, load_models

VLLM = ["python3", "-m", "vllm.entrypoints.openai.api_server"]


def local_gpus() -> list[int]:
    """VRAM of each local GPU, in whole GB.

    models.yaml speaks in whole GB while nvidia-smi reports MiB, and a "24 GB" 4090 reports
    24564 MiB = 23.99 GiB. Rounding keeps `min_gpu_memory_gb: 24` matching the card it was
    written for instead of rejecting it by 0.01 GB.
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"cannot read GPUs from nvidia-smi: {exc}") from None
    try:
        # A GPU in a bad state prints [N/A]. Fail here rather than quietly serving as if the
        # card weren't there and running out of memory later.
        return [round(int(line) / 1024) for line in out.split()]
    except ValueError:
        raise RuntimeError(f"nvidia-smi reported a GPU with no memory total: {out.strip()!r}") from None


def choose(models, gpus: int, memory_gb: float) -> tuple[ModelSpec | None, dict[str, str]]:
    """The highest-priority model that fits this host, and why each of the others doesn't."""
    fits, rejected = [], {}
    for spec in sorted(models, key=lambda m: (-m.priority, m.name)):
        if spec.replicas < 1:
            rejected[spec.name] = "replicas: 0"
        elif spec.gpus > gpus:
            rejected[spec.name] = f"needs {spec.gpus} GPUs, this host has {gpus}"
        elif spec.min_gpu_memory_gb > memory_gb:
            rejected[spec.name] = f"needs {spec.min_gpu_memory_gb:g} GB per GPU, this host has {memory_gb:g}"
        else:
            fits.append(spec)
    for spec in fits[1:]:
        rejected[spec.name] = "fits, but lower priority (one process serves one model)"
    return (fits[0] if fits else None), rejected


def vllm_args(spec: ModelSpec) -> list[str]:
    """The arguments k3s_controller.deployment_body() builds, plus an explicit bind address.

    spec.args comes last so anything set in models.yaml overrides these defaults.
    """
    args = ["--model", spec.model, "--served-model-name", spec.name,
            "--port", str(spec.port), "--host", "0.0.0.0"]
    if spec.gpus > 1:
        args += ["--tensor-parallel-size", str(spec.gpus)]
    return args + list(spec.args)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="miner-bot-serve", description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=os.getenv("MINER_BOT_CONFIG_DIR", "config"),
                        help="directory holding models.yaml (default: %(default)s)")
    parser.add_argument("--model", help="serve this model by name instead of the highest-priority fit")
    parser.add_argument("--dry-run", action="store_true", help="print the vLLM command and exit")
    args = parser.parse_args(argv)

    try:
        models = load_models(os.path.join(args.config, "models.yaml"))
    except (OSError, ModelsError) as exc:
        return fail(f"cannot load {args.config}/models.yaml: {exc}")
    try:
        memory_per_gpu = local_gpus()
    except RuntimeError as exc:
        return fail(str(exc))
    if not memory_per_gpu:
        return fail("nvidia-smi reported no GPUs")

    smallest = min(memory_per_gpu)
    print(f"{len(memory_per_gpu)} GPU(s), {smallest:g} GB each (smallest)", file=sys.stderr)
    spec, rejected = choose(models, len(memory_per_gpu), smallest)

    if args.model:
        spec = next((m for m in models if m.name == args.model), None)
        if spec is None:
            return fail(f"no model named '{args.model}' in {args.config}/models.yaml")
        if rejected.get(spec.name, "").startswith("needs"):
            print(f"warning: {spec.name} {rejected[spec.name]} -- it will probably run out of memory",
                  file=sys.stderr)
    for name, why in sorted(rejected.items()):
        if spec is None or name != spec.name:
            print(f"  skipped {name}: {why}", file=sys.stderr)
    if spec is None:
        return fail("no model in models.yaml fits this host; edit it, or force one with --model")
    if spec.replicas > 1:
        print(f"note: {spec.name} asks for {spec.replicas} replicas; one process serves one",
              file=sys.stderr)

    for key, value in spec.env:
        os.environ.setdefault(key, value)
    command = VLLM + vllm_args(spec)
    print(f"serving {spec.name} ({spec.model}) on port {spec.port}", file=sys.stderr)
    print(" ".join(command), file=sys.stderr)
    if args.dry_run:
        return 0
    if not os.getenv("VLLM_API_KEY"):
        print("warning: VLLM_API_KEY is not set -- anyone who can reach this port can use the GPU",
              file=sys.stderr)
    os.execvp(command[0], command)


if __name__ == "__main__":
    sys.exit(main())
