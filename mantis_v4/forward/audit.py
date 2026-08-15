"""Read-only inspection of one immutable forward contract timeline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .phase11 import audit_contract
from .store import ForwardStore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract_id")
    parser.add_argument("--forward-dir", default="data/forward")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    directory = (root / args.forward_dir).resolve()
    directory.relative_to(root)
    print(json.dumps(audit_contract(ForwardStore(directory), args.contract_id), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
