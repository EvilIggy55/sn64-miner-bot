"""Command-line client for the miner bot.

Talks to the bot's API, which is only reachable inside the cluster. Open a tunnel first:
    kubectl -n sn64 port-forward svc/miner-bot 8000:8000
`validate` works offline and checks config/ before you deploy it.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

DEFAULT_URL = os.getenv("MINER_BOT_URL", "http://127.0.0.1:8000")


def call(base: str, path: str, method: str = "GET"):
    request = urllib.request.Request(base.rstrip("/") + path, method=method)
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        sys.exit(f"{method} {path}: HTTP {exc.code} {exc.read().decode(errors='replace')}")
    except urllib.error.URLError as exc:
        sys.exit(f"Cannot reach the bot at {base} ({exc.reason}). Open a tunnel first:\n"
                 "  kubectl -n sn64 port-forward svc/miner-bot 8000:8000")


def table(rows: list[list], headers: list[str]) -> str:
    cells = [[str(c) for c in headers]] + [["—" if c in (None, "") else str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def when(ts) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else "never"


def show_status(d):
    last = d["last_reconcile"]
    print(f"namespace: {d['namespace']}   models configured: {d['models']}   events stored in: {d['events_backend']}")
    print(f"last reconcile: {when(last['at'])}   leader: {'yes' if last['leader'] else 'no'}")
    if last.get("error"):
        print(f"error: {last['error']}")
    for c in last.get("changes", []):
        print(f"  {c['action']} {c['chute']}")
    for a in last.get("heal", []):
        print(f"  autoheal {a['action']} {a['chute']}: {a['reason']}")


def show_plan(d):
    print(table([[n["name"], n["gpus"], n["available"], d["free"].get(n["name"]),
                  f"{n['gpu_memory_gb']:g} ({n['memory_source']})", n["product"]] for n in d["nodes"]],
                ["NODE", "GPUS", "AVAILABLE", "FREE AFTER PLAN", "GB/GPU (SOURCE)", "PRODUCT"]))
    print()
    print(table([[k, v["replicas"], v["gpus_per_replica"], ", ".join(v["nodes"])] for k, v in d["placements"].items()],
                ["MODEL", "REPLICAS", "GPUS EACH", "NODES"]))
    for model, why in d["unplaced"].items():
        print(f"not fully placed: {model}: {why}")


def show_chutes(d):
    rows = []
    for c in d["chutes"]:
        state = f"FAILED: {c['failed']}" if c["failed"] else (c["problem"] or "ok")
        rows.append([c["model"], f"{c['ready']}/{c['replicas']}", ", ".join(c["nodes"]), state, c["heal_count"]])
    print(table(rows, ["MODEL", "READY", "NODES", "STATE", "RESTARTS"]))


def show_events(d):
    print(table([[when(e["at"]), e["kind"], e["chute"], e["message"]] for e in d["events"]],
                ["TIME", "KIND", "CHUTE", "MESSAGE"]))


def show_sn64(d):
    for key in ("network", "netuid", "hotkey", "available", "registered", "uid", "stake", "incentive",
                "emission", "trust", "consensus", "block", "error"):
        if key in d:
            print(f"{key:>10}: {d[key]}")


def validate(config_dir: str) -> int:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
    from miner_bot.config import ConfigError, load
    from miner_bot.models_registry import ModelsError

    try:
        cfg = load(config_dir)
    except (ConfigError, ModelsError) as exc:
        print(f"invalid: {exc}")
        return 1
    print(f"ok: namespace {cfg.namespace}, netuid {cfg.netuid}, {len(cfg.models)} model(s)")
    print(table([[m.name, m.model, m.gpus, f"{m.min_gpu_memory_gb:g}", m.priority, m.replicas] for m in cfg.models],
                ["NAME", "MODEL", "GPUS", "MIN GB", "PRIORITY", "REPLICAS"]))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="miner-bot", description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"bot API (default {DEFAULT_URL}, or MINER_BOT_URL)")
    parser.add_argument("--json", action="store_true", help="print raw JSON")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="last reconcile and where events are stored")
    sub.add_parser("plan", help="GPU inventory and where models would be placed (dry run)")
    sub.add_parser("chutes", help="running chutes and their health")
    events = sub.add_parser("events", help="recent scheduler and autoheal actions")
    events.add_argument("--limit", type=int, default=30)
    sub.add_parser("sn64", help="your hotkey on the SN64 metagraph")
    sub.add_parser("reconcile", help="reconcile now instead of waiting for the loop")
    reset = sub.add_parser("reset", help="clear a chute's failed mark so it's scheduled again")
    reset.add_argument("model")
    check = sub.add_parser("validate", help="check config/ offline")
    check.add_argument("--config", default="config")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return validate(args.config)

    routes = {
        "status": ("GET", "/status", show_status),
        "plan": ("GET", "/plan", show_plan),
        "chutes": ("GET", "/chutes", show_chutes),
        "events": ("GET", f"/events?limit={getattr(args, 'limit', 30)}", show_events),
        "sn64": ("GET", "/sn64", show_sn64),
        "reconcile": ("POST", "/reconcile", show_status_from_reconcile),
        "reset": ("POST", f"/chutes/{getattr(args, 'model', '')}/reset", lambda d: print(f"reset {d['model']}")),
    }
    method, path, show = routes[args.command]
    data = call(args.url, path, method)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        show(data)
    return 0


def show_status_from_reconcile(d):
    show_status({"namespace": "-", "models": "-", "events_backend": "-", "last_reconcile": d})


if __name__ == "__main__":
    sys.exit(main())
