"""BRAIN AUDIT probe: did the integration keep both feature branches' symbols?

Compares the top-level symbols (JS functions/consts/classes; CSS class selectors)
of each feature parent against the integrated tree, for every file the two
features both touched. Read-only: uses ``git show`` and prints gaps.

Usage:
    python integration-retention.py <repo-root> <parentA> <parentB> <integrated> [paths...]
"""

from __future__ import annotations

import re
import subprocess
import sys

JS_FUNCTION = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)", re.M)
JS_CONST = re.compile(r"^(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=", re.M)
JS_CLASS = re.compile(r"^(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
CSS_CLASS = re.compile(r"^\.([A-Za-z_][\w-]*)", re.M)


def symbols(root: str, commit: str, path: str) -> set[str] | None:
    try:
        blob = subprocess.run(
            ["git", "-C", root, "show", f"{commit}:{path}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError:
        return None
    if path.endswith(".css"):
        return set(CSS_CLASS.findall(blob))
    return set(JS_FUNCTION.findall(blob)) | set(JS_CONST.findall(blob)) | set(JS_CLASS.findall(blob))


def main() -> int:
    root, parent_a, parent_b, integrated, *paths = sys.argv[1:]
    problems = 0
    for path in paths:
        a = symbols(root, parent_a, path)
        b = symbols(root, parent_b, path)
        merged = symbols(root, integrated, path)
        if a is None or b is None or merged is None:
            print(f"{path}: not present in one of the commits (a={a is not None} b={b is not None} merged={merged is not None})")
            continue
        missing_a = sorted(a - merged)
        missing_b = sorted(b - merged)
        status = "OK" if not missing_a and not missing_b else "GAP"
        if missing_a or missing_b:
            problems += 1
        print(
            f"{path}: {status}  parentA={len(a)} parentB={len(b)} merged={len(merged)} "
            f"missing_from_merged(A)={missing_a} missing_from_merged(B)={missing_b}"
        )
    print(f"\nfiles with retention gaps: {problems}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
