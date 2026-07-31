"""Human-readable inspection entry point for built-in Pathways."""

from __future__ import annotations

import argparse

from continucare.pathways.registry import load_builtin_pathways


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect a ContinuCare Pathway")
    parser.add_argument("code", nargs="?", default="GLP1-14D")
    parser.add_argument("--version")
    args = parser.parse_args()

    pathway = load_builtin_pathways().get(args.code, args.version)
    print(f"{pathway.name} ({pathway.code} v{pathway.version})")
    print(f"status={pathway.status.value} synthetic_only={pathway.synthetic_only}")
    print(f"fhir_version={pathway.fhir_version}")
    print(f"approval={pathway.approval.status.value}")
    print("\nFHIR artifacts")
    for artifact in pathway.artifacts:
        print(
            f"- {artifact.resource_type}: {artifact.canonical}|{artifact.version} "
            f"({artifact.file_name})"
        )
    print("\nClinical rules")
    if not pathway.clinical_rules:
        print("- none (fail-closed until clinical approval)")
    for rule in pathway.clinical_rules:
        print(f"- {rule.rule_id}: {rule.status.value} — {rule.title}")


if __name__ == "__main__":
    main()
