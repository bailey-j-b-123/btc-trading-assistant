#!/usr/bin/env python3
"""Run isolated synthetic forward endurance; no live/DB/exchange options exist.

For enforced isolation, invoke main() from a pytest wrapper under the repository's
offline pytest guard. This script's explicit temporary paths and fake source are
also used when invoked directly; it never claims a flag alone establishes a guard.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--boundaries", type=int, default=1000)
    result.add_argument("--case", choices=("baseline", "stress", "both"), default="both")
    result.add_argument("--compare", type=Path, nargs=2, metavar=("BASELINE_JSON", "STRESS_JSON"))
    result.add_argument("--output", type=Path, default=Path(".pytest_cache/forward-endurance.json"))
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if (PROJECT_ROOT / ".env").exists() or (Path.cwd() / ".env").exists():
        raise SystemExit("Run endurance only in a fresh source-only workspace without an environment file")
    if args.boundaries < 1:
        raise SystemExit("--boundaries must be positive")
    output = args.output.resolve()
    if output.suffix.lower() != ".json" or not output.is_relative_to(Path.cwd().resolve()):
        raise SystemExit("--output must be a JSON artifact inside the current workspace")
    comparison_inputs = None
    if args.compare:
        inputs = [path.resolve() for path in args.compare]
        if any(path.suffix.lower() != ".json" or not path.is_relative_to(Path.cwd().resolve()) or path == output for path in inputs):
            raise SystemExit("comparison inputs must be workspace JSON reports, distinct from the output")
        comparison_inputs = [json.loads(path.read_text()) for path in inputs]
    # Preserve only the already-resolved scratch root, not inherited credentials or settings.
    scratch = tempfile.gettempdir()
    os.environ.clear()
    os.environ["TMPDIR"] = scratch
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    sys.path.insert(0, str(PROJECT_ROOT / "tests"))
    from forward_endurance_harness import compare_reports, run_endurance

    output.parent.mkdir(parents=True, exist_ok=True)
    partial = {"format": "brain-offline-endurance-progress-v1", "boundaries": args.boundaries, "case": "comparison" if comparison_inputs is not None else args.case, "phase": "starting"}

    def save(payload):
        temporary = output.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
        temporary.replace(output)

    def progress(payload):
        partial.update(payload)
        save(partial)
        print(json.dumps(payload, sort_keys=True), flush=True)

    save(partial)
    try:
        if comparison_inputs is not None:
            result = compare_reports(*comparison_inputs)
        else:
            result = run_endurance(args.boundaries, case=args.case, progress=progress)
    except Exception as error:
        partial.update(phase="failed", error_type=type(error).__name__, error=str(error))
        save(partial)
        raise
    result["phase"] = "complete"
    save(result)
    print(json.dumps({"phase": "complete", "checks_passed": result["checks_passed"], "canonical_agreement": result["canonical_agreement"], "boundaries": result["boundaries"], "case": partial["case"], "output": str(output)}, sort_keys=True), flush=True)
    if not result["checks_passed"]:
        raise SystemExit(1)
    return result


if __name__ == "__main__":
    main()
