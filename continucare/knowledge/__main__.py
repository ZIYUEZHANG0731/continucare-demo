"""Developer-only read-only Knowledge Evidence inspection CLI."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from continucare.knowledge.models import KnowledgeBundleError
from continucare.knowledge.registry import LoadMode, load_builtin_bundle
from continucare.knowledge.render import render_pathway_knowledge


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect registered Knowledge Evidence")
    parser.add_argument("code")
    parser.add_argument("--version")
    parser.add_argument("--historical", action="store_true")
    args = parser.parse_args(argv)
    if not args.version:
        print("error: --version is required for an exact Pathway lookup", file=sys.stderr)
        return 2
    mode = LoadMode.HISTORICAL if args.historical else LoadMode.CURRENT
    try:
        registry = load_builtin_bundle(mode=mode)
        view = registry.for_pathway(args.code, args.version)
    except KnowledgeBundleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_pathway_knowledge(view), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
