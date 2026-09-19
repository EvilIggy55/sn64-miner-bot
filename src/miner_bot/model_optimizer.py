"""Pick the best model for this machine's GPUs and show the vLLM command, without running it.

This is local_serve's choice (highest-priority model in config/models.yaml that fits the local
GPUs) as a dry run. To change the outcome, edit models.yaml priorities. To serve it, use
`sn51_cli.py start`.

    python -m miner_bot.model_optimizer [--model NAME]
"""
import sys

from . import local_serve


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    return local_serve.main(["--dry-run", *argv])


if __name__ == "__main__":
    sys.exit(main())
